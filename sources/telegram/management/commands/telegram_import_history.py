"""
Full Telegram history import.
Order: private chats first, then groups.
Skips broadcast channels and bots entirely.
Safe to re-run: unique_together skips duplicates.
"""
import asyncio
import logging

from asgiref.sync import sync_to_async
from decouple import config
from django.core.management.base import BaseCommand
from django.utils import timezone
from telethon import TelegramClient

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


class Command(BaseCommand):
    help = "Import full Telegram history — privates first, then groups. Skips channels and bots."

    def handle(self, *args, **options):
        asyncio.run(self._import())

    async def _import(self):
        api_id = config("TELEGRAM_API_ID", cast=int)
        api_hash = config("TELEGRAM_API_HASH")
        session_name = config("TELEGRAM_SESSION_NAME", default="big_sync_telegram")

        client = TelegramClient(session_name, api_id, api_hash)
        await client.start()

        me = await client.get_me()
        self.stdout.write(f"Importing full history as {me.first_name} (@{me.username})\n")

        # Collect all dialogs first, split by type
        privates = []
        groups = []

        async for dialog in client.iter_dialogs():
            if should_skip(dialog):
                dtype = dialog_type(dialog)
                self.stdout.write(f"  [SKIP {dtype}] {dialog.name}")
                continue
            if dialog_type(dialog) == "private":
                privates.append(dialog)
            else:
                groups.append(dialog)

        self.stdout.write(f"\n{len(privates)} private chats, {len(groups)} groups\n")

        total_saved = 0
        total_skipped = 0

        for phase, dialogs in [("PRIVATE", privates), ("GROUP", groups)]:
            self.stdout.write(f"\n{'─'*60}")
            self.stdout.write(f"Phase: {phase} ({len(dialogs)} dialogs)\n")

            for idx, dialog in enumerate(dialogs, 1):
                name = dialog.name or str(dialog.id)
                saved = 0
                skipped = 0

                self.stdout.write(f"  [{idx}/{len(dialogs)}] {name} ...", ending="")
                self.stdout.flush()

                try:
                    async for msg in client.iter_messages(dialog):
                        media_type = detect_media_type(msg)
                        text = message_text(msg)

                        if not text and media_type == MediaType.TEXT:
                            skipped += 1
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
                        else:
                            skipped += 1

                except Exception as e:
                    self.stdout.write(f" ERROR: {e}")
                    logger.exception("Error importing dialog %s", name)
                    continue

                self.stdout.write(f" +{saved} new, {skipped} skipped")
                total_saved += saved
                total_skipped += skipped

        await client.disconnect()
        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {total_saved} messages saved, {total_skipped} skipped."
        ))
