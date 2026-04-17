"""
Long-running Telegram listener using Telethon MTProto client.
Receives all incoming messages, saves them, then analyzes with Gemini.
Run as a systemd service: manage.py telegram_listener
"""
import asyncio
import logging

from asgiref.sync import sync_to_async
from decouple import config
from django.core.management.base import BaseCommand
from django.utils import timezone
from telethon import TelegramClient, events
from telethon.tl.types import User, Chat, Channel

from sources.telegram.ignored import ignored_chat_ids
from sources.telegram.media import (
    detect_media_type, message_text, serialize,
    should_skip_entity, download_media,
)
from sources.telegram.models import TelegramMessage, MediaType
from workflows.gemini import AUDIO_MEDIA_TYPES
from workflows.workflow_telegram import process_realtime_message

logger = logging.getLogger(__name__)


def _get_chat_name(entity) -> str:
    if isinstance(entity, User):
        return f"{entity.first_name or ''} {entity.last_name or ''}".strip()
    if isinstance(entity, (Chat, Channel)):
        return entity.title or ""
    return str(getattr(entity, "id", ""))


def _save_message(chat_id, message_id, defaults):
    obj, created = TelegramMessage.objects.get_or_create(
        chat_id=chat_id,
        message_id=message_id,
        defaults=defaults,
    )
    return obj, created


def _update_media_path(pk, path):
    TelegramMessage.objects.filter(pk=pk).update(
        media_path=path, media_downloaded=True
    )


def _get_context_messages(chat_id: int, exclude_pk: int, limit: int = 10) -> list:
    """Fetch up to `limit` most recent messages before the new one (same chat)."""
    msgs = (
        TelegramMessage.objects
        .filter(chat_id=chat_id)
        .exclude(pk=exclude_pk)
        .order_by("-date")[:limit]
    )
    return list(reversed(list(msgs)))


def _mark_processed(pk: int):
    TelegramMessage.objects.filter(pk=pk).update(processed=True)


def _analyze_new_message(obj: TelegramMessage, chat_name: str) -> dict:
    """
    Fetch context, call Gemini, mark message processed.
    Runs synchronously — call via sync_to_async in the async handler.
    """
    context_db = _get_context_messages(obj.chat_id, obj.pk)
    context_msgs = [
        {
            "time": m.date.strftime("%H:%M"),
            "date": m.date.strftime("%Y-%m-%d"),
            "sender": m.sender_name or "Sconosciuto",
            "text": (m.transcription if m.media_type in AUDIO_MEDIA_TYPES and m.transcription
                     else m.text),
            "media_type": m.media_type,
        }
        for m in context_db
    ]
    new_msg = {
        "time": obj.date.strftime("%H:%M"),
        "date": obj.date.strftime("%Y-%m-%d"),
        "sender": obj.sender_name or "Sconosciuto",
        "text": (obj.transcription if obj.media_type in AUDIO_MEDIA_TYPES and obj.transcription
                 else obj.text),
        "media_type": obj.media_type,
    }
    counts = process_realtime_message(chat_name, new_msg, context_msgs)
    _mark_processed(obj.pk)
    return counts


class Command(BaseCommand):
    help = "Run the Telegram listener (long-running process)"

    def handle(self, *args, **options):
        self.stdout.write("Starting Telegram listener...")
        asyncio.run(self._listen())

    async def _listen(self):
        api_id = config("TELEGRAM_API_ID", cast=int)
        api_hash = config("TELEGRAM_API_HASH")
        session_name = config("TELEGRAM_SESSION_NAME", default="big_sync_telegram")

        client = TelegramClient(session_name, api_id, api_hash)
        await client.start()

        me = await client.get_me()
        self.stdout.write(f"Listening as {me.first_name} (@{me.username})")

        @client.on(events.NewMessage)
        async def on_message(event):
            try:
                msg = event.message
                chat = await event.get_chat()
                sender = await event.get_sender()

                # Use canonical peer ID (same format as dialog.id in import)
                from telethon.utils import get_peer_id
                chat_id = get_peer_id(msg.peer_id)
                chat_name = _get_chat_name(chat)

                if should_skip_entity(chat):
                    return

                if abs(chat_id) in ignored_chat_ids():
                    return

                media_type = detect_media_type(msg)
                text = message_text(msg)
                sender_name = _get_chat_name(sender) if sender else ""

                self.stdout.write(f"[{media_type}] [{chat_name}] {sender_name}: {text[:80]}")

                obj, created = await sync_to_async(_save_message)(
                    chat_id=chat_id,
                    message_id=msg.id,
                    defaults={
                        "chat_name": chat_name,
                        "sender_id": sender.id if sender else None,
                        "sender_name": sender_name,
                        "text": text,
                        "media_type": media_type,
                        "date": msg.date or timezone.now(),
                        "raw": serialize(msg.to_dict()),
                    },
                )

                status = "saved" if created else "already exists"
                self.stdout.write(f"  → {status} (id={obj.id})")

                if created and media_type != MediaType.TEXT:
                    path = await download_media(client, msg, chat_name)
                    if path:
                        await sync_to_async(_update_media_path)(obj.pk, path)
                        self.stdout.write(f"  → media saved: {path}")

                if created:
                    try:
                        counts = await sync_to_async(_analyze_new_message)(obj, chat_name)
                        total = sum(counts.values())
                        if total:
                            self.stdout.write(
                                f"  → analyzed: contacts:{counts['contacts']} "
                                f"events:{counts['events']} todos:{counts['todos']}"
                            )
                        else:
                            self.stdout.write(f"  → analyzed: nothing extracted")
                    except Exception as e:
                        self.stderr.write(f"  → analysis error: {e}")
                        logger.exception("Real-time analysis error for msg id=%s", obj.pk)

            except Exception as e:
                self.stderr.write(f"Error: {e}")
                logger.exception("Error processing Telegram message")

        self.stdout.write("Listener active. Waiting for messages...")
        await client.run_until_disconnected()
