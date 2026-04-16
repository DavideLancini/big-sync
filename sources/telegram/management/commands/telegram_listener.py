"""
Long-running Telegram listener using Telethon MTProto client.
Receives all incoming messages and saves them to the database.
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

from sources.telegram.media import (
    detect_media_type, message_text, serialize,
    should_skip_entity, download_media,
)
from sources.telegram.models import TelegramMessage, MediaType

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

                chat_id = (
                    getattr(msg.peer_id, "user_id", None)
                    or getattr(msg.peer_id, "chat_id", None)
                    or getattr(msg.peer_id, "channel_id", None)
                    or msg.chat_id
                )
                chat_name = _get_chat_name(chat)

                if should_skip_entity(chat):
                    return

                from django.conf import settings
                ignored = {abs(int(i)) for i in getattr(settings, "TELEGRAM_IGNORE_CHATS", []) if str(i).strip().lstrip("-").isdigit()}
                if abs(chat_id) in ignored:
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

            except Exception as e:
                self.stderr.write(f"Error: {e}")
                logger.exception("Error processing Telegram message")

        self.stdout.write("Listener active. Waiting for messages...")
        await client.run_until_disconnected()
