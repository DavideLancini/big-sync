"""
One-time bulk import of all Telegram message history.
Iterates all dialogs (chats, groups, channels) and saves every message.
Safe to re-run: unique_together constraint skips duplicates.
"""
import asyncio
import datetime
import logging

from asgiref.sync import sync_to_async
from decouple import config
from django.core.management.base import BaseCommand
from django.utils import timezone
from telethon import TelegramClient
from telethon.tl.types import User, Chat, Channel

from sources.telegram.models import TelegramMessage

logger = logging.getLogger(__name__)


def _serialize(obj):
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.hex()
    return obj


def _get_chat_name(entity) -> str:
    if isinstance(entity, User):
        return f"{entity.first_name or ''} {entity.last_name or ''}".strip()
    if isinstance(entity, (Chat, Channel)):
        return entity.title or ""
    return str(getattr(entity, "id", ""))


def _save_message(chat_id, message_id, chat_name, sender_id, sender_name, text, date, raw):
    obj, created = TelegramMessage.objects.get_or_create(
        chat_id=chat_id,
        message_id=message_id,
        defaults={
            "chat_name": chat_name,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "text": text,
            "date": date,
            "raw": raw,
        },
    )
    return created


class Command(BaseCommand):
    help = "Import full Telegram message history (safe to re-run)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Max messages per dialog (default: unlimited)",
        )
        parser.add_argument(
            "--dialog",
            type=str,
            default=None,
            help="Import only a specific dialog by name or ID",
        )

    def handle(self, *args, **options):
        asyncio.run(self._import(options["limit"], options["dialog"]))

    async def _import(self, limit, dialog_filter):
        api_id = config("TELEGRAM_API_ID", cast=int)
        api_hash = config("TELEGRAM_API_HASH")
        session_name = config("TELEGRAM_SESSION_NAME", default="big_sync_telegram")

        client = TelegramClient(session_name, api_id, api_hash)
        await client.start()

        me = await client.get_me()
        self.stdout.write(f"Importing history as {me.first_name} (@{me.username})\n")

        total_saved = 0
        total_skipped = 0
        dialog_count = 0

        async for dialog in client.iter_dialogs():
            name = dialog.name or str(dialog.id)

            if dialog_filter and dialog_filter.lower() not in name.lower() and str(dialog.id) != dialog_filter:
                continue

            dialog_count += 1
            saved = 0
            skipped = 0

            self.stdout.write(f"  [{dialog_count}] {name} (id={dialog.id}) ...", ending="")

            try:
                async for msg in client.iter_messages(dialog, limit=limit):
                    if not msg.message:
                        continue

                    chat_id = dialog.id
                    sender_id = None
                    sender_name = ""

                    if msg.sender:
                        sender_id = msg.sender.id
                        sender_name = _get_chat_name(msg.sender)

                    created = await sync_to_async(_save_message)(
                        chat_id=chat_id,
                        message_id=msg.id,
                        chat_name=name,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        text=msg.message,
                        date=msg.date or timezone.now(),
                        raw=_serialize(msg.to_dict()),
                    )
                    if created:
                        saved += 1
                    else:
                        skipped += 1

            except Exception as e:
                self.stdout.write(f" ERROR: {e}")
                continue

            self.stdout.write(f" +{saved} saved, {skipped} skipped")
            total_saved += saved
            total_skipped += skipped

        await client.disconnect()
        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {dialog_count} dialogs — {total_saved} saved, {total_skipped} already present."
        ))
