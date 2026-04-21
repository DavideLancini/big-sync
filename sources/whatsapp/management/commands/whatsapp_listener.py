"""
Long-running WhatsApp listener. Persists every incoming message to the DB and
kicks off Gemini analysis via the shared realtime workflow — same pattern as
telegram_listener.

Run via systemd (user-side), not in cron.
"""
import asyncio
import logging
from datetime import datetime, timezone as dt_timezone

from asgiref.sync import sync_to_async
from decouple import config
from django.conf import settings
from django.core.management.base import BaseCommand

from sources.whatsapp.models import WhatsAppMessage, WaMediaType
from sources.whatsapp.media import download_media
from sources.whatsapp.parse import parse_event, detect_media_type, message_text, jid_str
from workflows.gemini import AUDIO_MEDIA_TYPES
from workflows.workflow_telegram import process_realtime_message

logger = logging.getLogger(__name__)

_CONTEXT_LIMIT = 10


def _session_path() -> str:
    return config(
        "WHATSAPP_SESSION_FILE",
        default=str(settings.BASE_DIR / "whatsapp_session.sqlite3"),
    )


def _session_has_no_device(path: str) -> bool:
    """Return True if the session sqlite file exists but has no paired device row."""
    import sqlite3
    try:
        conn = sqlite3.connect(path)
        try:
            cur = conn.execute("SELECT COUNT(*) FROM whatsmeow_device")
            return (cur.fetchone()[0] or 0) == 0
        finally:
            conn.close()
    except sqlite3.Error:
        return True


def _ingest_history(ev) -> int:
    """Persist messages from a HistorySync event. Returns number of new rows."""
    new = 0
    for conv in ev.conversations:
        chat_jid = conv.ID
        if not chat_jid:
            continue
        is_group = chat_jid.endswith("@g.us")
        chat_name = conv.name or conv.displayName or chat_jid.split("@")[0]

        for hs in conv.messages:
            wmi = hs.message
            if not wmi or not wmi.key.ID:
                continue

            ts = wmi.messageTimestamp or 0
            try:
                s = ts / 1000 if ts > 1e11 else ts
                when = datetime.fromtimestamp(s, tz=dt_timezone.utc)
            except (ValueError, OSError):
                continue

            msg_proto = wmi.message
            mt = detect_media_type(msg_proto) if msg_proto else WaMediaType.TEXT
            text = message_text(msg_proto) if msg_proto else ""

            sender_jid = ""
            if is_group and wmi.key.participant:
                sender_jid = wmi.key.participant
            else:
                sender_jid = wmi.key.remoteJID or ""

            try:
                _, created = WhatsAppMessage.objects.get_or_create(
                    chat_jid=chat_jid,
                    message_id=wmi.key.ID,
                    defaults={
                        "chat_name": chat_name,
                        "sender_jid": sender_jid,
                        "sender_name": (wmi.pushName or "")[:255],
                        "text": text,
                        "media_type": mt,
                        "date": when,
                        "is_from_me": bool(wmi.key.fromMe),
                        "is_group": is_group,
                    },
                )
                if created:
                    new += 1
            except Exception:
                logger.exception("failed to save history msg %s in %s",
                                 wmi.key.ID, chat_jid)
    return new


def _update_media_path(pk: int, path: str):
    WhatsAppMessage.objects.filter(pk=pk).update(
        media_path=path, media_downloaded=True
    )


def _save_message(parsed: dict, chat_name: str) -> tuple[WhatsAppMessage, bool]:
    obj, created = WhatsAppMessage.objects.get_or_create(
        chat_jid=parsed["chat_jid"],
        message_id=parsed["message_id"],
        defaults={
            "chat_name": chat_name,
            "sender_jid": parsed["sender_jid"],
            "sender_name": parsed["sender_name"],
            "text": parsed["text"],
            "media_type": parsed["media_type"],
            "date": parsed["date"],
            "is_from_me": parsed["is_from_me"],
            "is_group": parsed["is_group"],
        },
    )
    return obj, created


def _context_for(chat_jid: str, exclude_pk: int, limit: int = _CONTEXT_LIMIT) -> list[dict]:
    msgs = (
        WhatsAppMessage.objects
        .filter(chat_jid=chat_jid)
        .exclude(pk=exclude_pk)
        .order_by("-date")[:limit]
    )
    msgs = list(reversed(list(msgs)))
    return [
        {
            "time": m.date.strftime("%H:%M"),
            "date": m.date.strftime("%Y-%m-%d"),
            "sender": m.sender_name or "Sconosciuto",
            "text": (m.transcription if m.media_type in AUDIO_MEDIA_TYPES and m.transcription
                     else m.text),
            "media_type": m.media_type,
        }
        for m in msgs
    ]


def _mark_processed(pk: int):
    WhatsAppMessage.objects.filter(pk=pk).update(processed=True)


def _analyze(obj: WhatsAppMessage) -> dict:
    context = _context_for(obj.chat_jid, obj.pk)
    new_msg = {
        "time": obj.date.strftime("%H:%M"),
        "date": obj.date.strftime("%Y-%m-%d"),
        "sender": obj.sender_name or "Sconosciuto",
        "text": (obj.transcription if obj.media_type in AUDIO_MEDIA_TYPES and obj.transcription
                 else obj.text),
        "media_type": obj.media_type,
    }
    counts = process_realtime_message(obj.chat_name, new_msg, context)
    _mark_processed(obj.pk)
    return counts


class Command(BaseCommand):
    help = "Run the WhatsApp listener (long-running process)"

    def handle(self, *args, **options):
        self.stdout.write("Starting WhatsApp listener...")
        try:
            asyncio.run(self._listen())
        except KeyboardInterrupt:
            self.stdout.write("Stopped.")

    async def _listen(self):
        import os
        from neonize.aioze.client import NewAClient
        from neonize.aioze.events import ConnectedEv, HistorySyncEv, MessageEv, PairStatusEv

        session_file = _session_path()
        client = NewAClient(session_file)

        # If the session file is missing or has no paired device, use the phone
        # number from .env to request a pair code on first boot. Keeps the whole
        # lifecycle in one process so whatsmeow never sees two connections
        # racing on the same session (which triggers 401 "logged out from
        # another device" and wipes the device row).
        needs_pair = (
            not os.path.exists(session_file)
            or os.path.getsize(session_file) == 0
            or _session_has_no_device(session_file)
        )
        pair_phone = config("WHATSAPP_PHONE", default="").strip().replace(" ", "").replace("+", "")

        @client.paircode
        async def _on_paircode(_, code: str, connected: bool = True):
            if connected:
                self.stdout.write(f"✔ Pair code processed: {code}")
            else:
                self.stdout.write(
                    f"\n→ On phone: WhatsApp → Linked Devices → Link with phone number\n"
                    f"  Enter: {code}\n"
                )

        @client.event(ConnectedEv)
        async def _on_connected(_, __):
            self.stdout.write("✔ Connected to WhatsApp")

        @client.event(PairStatusEv)
        async def _on_paired(_, ev):
            self.stdout.write(f"✔ Paired as {ev.ID.User}@{ev.ID.Server}")

        @client.event(HistorySyncEv)
        async def _on_history(_, ev):
            try:
                count = await sync_to_async(_ingest_history)(ev)
                if count:
                    self.stdout.write(
                        f"[HistorySync type={ev.syncType}] +{count} messages "
                        f"from {len(ev.conversations)} chats"
                    )
            except Exception:
                logger.exception("history sync ingest failed")

        @client.event(MessageEv)
        async def _on_message(_, event):
            try:
                parsed = parse_event(event)
            except Exception:
                logger.exception("parse_event failed")
                return

            if parsed["is_from_me"]:
                return  # ignore self-sent — mirror Telegram listener behavior

            # chat name resolution: for groups, try GroupInfo; for DMs fallback to sender push name
            chat_name = parsed["sender_name"] or parsed["chat_jid"].split("@")[0]
            if parsed["is_group"]:
                try:
                    info = await client.get_group_info(event.Info.MessageSource.Chat)
                    if info and info.GroupName.Name:
                        chat_name = info.GroupName.Name
                except Exception:
                    pass

            try:
                obj, created = await sync_to_async(_save_message)(parsed, chat_name)
            except Exception:
                logger.exception("save failed for %s", parsed.get("message_id"))
                return

            if not created:
                return

            self.stdout.write(
                f"[{parsed['media_type']}] [{chat_name}] {parsed['sender_name']}: "
                f"{(parsed['text'] or '')[:80]}"
            )

            if parsed["media_type"] != WaMediaType.TEXT:
                path = await download_media(client, event, chat_name, parsed["media_type"])
                if path:
                    await sync_to_async(_update_media_path)(obj.pk, path)
                    self.stdout.write(f"  → media saved: {path}")

            try:
                counts = await sync_to_async(_analyze)(obj)
                total = sum(counts.values())
                if total:
                    self.stdout.write(
                        f"  → analyzed: contacts:{counts['contacts']} "
                        f"events:{counts['events']} todos:{counts['todos']}"
                    )
            except Exception as e:
                self.stderr.write(f"  → analysis error: {e}")
                logger.exception("WA analysis error for pk=%s", obj.pk)

        task = await client.connect()

        if needs_pair:
            if not pair_phone:
                self.stderr.write("WHATSAPP_PHONE missing in .env — cannot pair.")
                return
            # Give connect() a moment to establish the websocket
            await asyncio.sleep(2)
            try:
                code = await client.PairPhone(pair_phone, True)
                self.stdout.write(f"Requested pair code: {code}")
            except Exception as e:
                self.stderr.write(f"PairPhone failed: {e}")

        try:
            await task
        except asyncio.CancelledError:
            pass
