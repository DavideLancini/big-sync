"""
Full Telegram history import with smart resume.
- Privates first, then groups. Channels and bots skipped.
- Per dialog: fetches only messages older than the oldest one already in DB.
  Dialogs fully imported are detected and skipped in seconds.
- Safe to re-run at any time.
"""
import asyncio
import logging

from asgiref.sync import sync_to_async
from decouple import config
from django.core.management.base import BaseCommand
from django.utils import timezone
from telethon import TelegramClient

from sources.telegram.ignored import ignored_chat_ids
from sources.telegram.media import (
    detect_media_type, message_text, serialize,
    dialog_type, should_skip, download_media,
)
from sources.telegram.models import TelegramMessage, MediaType

logger = logging.getLogger(__name__)


def _get_entity_name(entity) -> str:
    from telethon.tl.types import User, Chat, Channel
    if isinstance(entity, User):
        return f"{entity.first_name or ''} {entity.last_name or ''}".strip()
    if isinstance(entity, (Chat, Channel)):
        return entity.title or ""
    return str(getattr(entity, "id", ""))


def _oldest_message_id(chat_id) -> int | None:
    """Return the smallest message_id we already have for this chat, or None."""
    return TelegramMessage.objects.filter(chat_id=chat_id).order_by("message_id").values_list("message_id", flat=True).first()


def _newest_message_id(chat_id) -> int | None:
    """Return the largest message_id we already have for this chat, or None."""
    return TelegramMessage.objects.filter(chat_id=chat_id).order_by("-message_id").values_list("message_id", flat=True).first()


def _save_message(chat_id, message_id, chat_name, sender_id, sender_name,
                  text, media_type, date, raw):
    obj, created = TelegramMessage.objects.get_or_create(
        chat_id=chat_id,
        message_id=message_id,
        defaults={
            "chat_name": chat_name,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "text": text,
            "media_type": media_type,
            "date": date,
            "raw": raw,
        },
    )
    return obj, created


def _ignored_ids():
    return ignored_chat_ids()


class Command(BaseCommand):
    help = "Import full Telegram history with smart resume. Privates first, then groups."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip",
            type=str,
            default="",
            help="Comma-separated chat IDs to skip in this run only (e.g. for deferred heavy groups)",
        )
        parser.add_argument(
            "--gap-check",
            action="store_true",
            help=(
                "Iterate all messages newest→oldest regardless of what's stored; "
                "stop a chat after 50 consecutive duplicates. "
                "Use to catch messages missed while the listener was off."
            ),
        )

    def handle(self, *args, **options):
        skip_ids = set()
        for val in options["skip"].split(","):
            val = val.strip()
            if val:
                try:
                    skip_ids.add(int(val))
                except ValueError:
                    pass
        asyncio.run(self._import(skip_ids, gap_check=options["gap_check"]))

    async def _import(self, skip_ids, gap_check: bool = False):
        api_id = config("TELEGRAM_API_ID", cast=int)
        api_hash = config("TELEGRAM_API_HASH")
        session_name = config("TELEGRAM_SESSION_NAME", default="big_sync_telegram")

        client = TelegramClient(session_name, api_id, api_hash)
        await client.start()

        me = await client.get_me()
        self.stdout.write(f"Importing full history as {me.first_name} (@{me.username})\n")

        privates = []
        groups = []

        ignored_ids = _ignored_ids()

        async for dialog in client.iter_dialogs():
            if should_skip(dialog):
                self.stdout.write(f"  [SKIP {dialog_type(dialog)}] {dialog.name}")
                continue
            if abs(dialog.id) in ignored_ids:
                self.stdout.write(f"  [IGNORE] {dialog.name}")
                continue
            if abs(dialog.id) in {abs(i) for i in skip_ids}:
                self.stdout.write(f"  [SKIP this run] {dialog.name}")
                continue
            if dialog_type(dialog) == "private":
                privates.append(dialog)
            else:
                groups.append(dialog)

        self.stdout.write(f"\n{len(privates)} private chats, {len(groups)} groups\n")

        total_saved = 0
        total_skipped = 0

        all_dialogs = privates + groups

        for phase, dialogs in [("PRIVATE", privates), ("GROUP", groups)]:
            self.stdout.write(f"\n{'─'*60}")
            self.stdout.write(f"Phase: {phase} ({len(dialogs)} dialogs)\n")

            for idx, dialog in enumerate(dialogs, 1):
                name = dialog.name or str(dialog.id)
                saved = 0
                skipped = 0

                if gap_check:
                    resume_note = "gap-check"
                    iter_kwargs = {}
                else:
                    # Resume: find oldest message already in DB for this dialog
                    oldest_id = await sync_to_async(_oldest_message_id)(dialog.id)

                    if oldest_id is not None and oldest_id <= 1:
                        self.stdout.write(f"  [{idx}/{len(dialogs)}] {name} ... [already complete, skip]")
                        continue

                    resume_note = f"resuming from msg_id<{oldest_id}" if oldest_id else "full fetch"
                    iter_kwargs = {"max_id": oldest_id} if oldest_id else {}

                self.stdout.write(f"  [{idx}/{len(dialogs)}] {name} ({resume_note}) ...", ending="")
                self.stdout.flush()

                try:
                    consecutive_dupes = 0

                    async for msg in client.iter_messages(dialog, **iter_kwargs):
                        media_type = detect_media_type(msg)
                        text = message_text(msg)

                        if not text and media_type == MediaType.TEXT:
                            skipped += 1
                            if gap_check:
                                consecutive_dupes += 1
                                if consecutive_dupes >= 50:
                                    self.stdout.write(f" [50 dupes, done]", ending="")
                                    break
                            continue

                        sender_id = None
                        sender_name = ""
                        if msg.sender:
                            sender_id = msg.sender.id
                            sender_name = _get_entity_name(msg.sender)

                        obj, created = await sync_to_async(_save_message)(
                            chat_id=dialog.id,
                            message_id=msg.id,
                            chat_name=name,
                            sender_id=sender_id,
                            sender_name=sender_name,
                            text=text,
                            media_type=media_type,
                            date=msg.date or timezone.now(),
                            raw=serialize(msg.to_dict()),
                        )

                        if created:
                            saved += 1
                            consecutive_dupes = 0
                            if media_type != MediaType.TEXT:
                                path = await download_media(client, msg, name)
                                if path:
                                    await sync_to_async(
                                        TelegramMessage.objects.filter(pk=obj.pk).update
                                    )(media_path=path, media_downloaded=True)
                        else:
                            skipped += 1
                            if gap_check:
                                consecutive_dupes += 1
                                if consecutive_dupes >= 50:
                                    self.stdout.write(f" [50 dupes, done]", ending="")
                                    break

                except Exception as e:
                    self.stdout.write(f" ERROR: {e}")
                    logger.exception("Error importing dialog %s", name)
                    continue

                self.stdout.write(f" +{saved} new, {skipped} skipped")
                total_saved += saved
                total_skipped += skipped

        # ── Catch-up: fetch messages newer than our most recent, for all dialogs ──
        # Captures messages that arrived while the listener was stopped during import.
        self.stdout.write(f"\n{'─'*60}")
        self.stdout.write("Phase: CATCH-UP (new messages since import started)\n")

        catchup_saved = 0
        for dialog in all_dialogs:
            newest_id = await sync_to_async(_newest_message_id)(dialog.id)
            if not newest_id:
                continue
            name = dialog.name or str(dialog.id)
            saved = 0
            try:
                async for msg in client.iter_messages(dialog, min_id=newest_id):
                    media_type = detect_media_type(msg)
                    text = message_text(msg)
                    if not text and media_type == MediaType.TEXT:
                        continue
                    sender_id = None
                    sender_name = ""
                    if msg.sender:
                        sender_id = msg.sender.id
                        sender_name = _get_entity_name(msg.sender)
                    obj, created = await sync_to_async(_save_message)(
                        chat_id=dialog.id,
                        message_id=msg.id,
                        chat_name=name,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        text=text,
                        media_type=media_type,
                        date=msg.date or timezone.now(),
                        raw=serialize(msg.to_dict()),
                    )
                    if created:
                        saved += 1
                        if media_type != MediaType.TEXT:
                            path = await download_media(client, msg, name)
                            if path:
                                await sync_to_async(
                                    TelegramMessage.objects.filter(pk=obj.pk).update
                                )(media_path=path, media_downloaded=True)
            except Exception as e:
                self.stdout.write(f"  [{name}] catch-up ERROR: {e}")
                continue
            if saved:
                self.stdout.write(f"  {name}: +{saved} catch-up")
                catchup_saved += saved

        total_saved += catchup_saved
        await client.disconnect()
        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {total_saved} messages saved ({catchup_saved} catch-up), {total_skipped} skipped."
        ))
