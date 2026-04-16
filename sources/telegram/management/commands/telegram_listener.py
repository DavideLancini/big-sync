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

from sources.telegram.models import TelegramMessage

logger = logging.getLogger(__name__)


def _get_chat_name(chat) -> str:
    if isinstance(chat, User):
        return f"{chat.first_name or ''} {chat.last_name or ''}".strip()
    if isinstance(chat, (Chat, Channel)):
        return chat.title or ""
    return str(getattr(chat, "id", ""))


def _get_sender_name(sender) -> str:
    if sender is None:
        return ""
    if isinstance(sender, User):
        return f"{sender.first_name or ''} {sender.last_name or ''}".strip()
    return str(getattr(sender, "id", ""))


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
    return obj, created


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

                chat_id = getattr(msg.peer_id, "user_id", None) \
                       or getattr(msg.peer_id, "chat_id", None) \
                       or getattr(msg.peer_id, "channel_id", None) \
                       or msg.chat_id

                chat_name = _get_chat_name(chat)
                sender_name = _get_sender_name(sender)
                text = msg.message or ""

                self.stdout.write(f"Message from [{chat_name}] {sender_name}: {text[:80]}")

                obj, created = await sync_to_async(_save_message)(
                    chat_id=chat_id,
                    message_id=msg.id,
                    chat_name=chat_name,
                    sender_id=sender.id if sender else None,
                    sender_name=sender_name,
                    text=text,
                    date=msg.date or timezone.now(),
                    raw=msg.to_dict(),
                )
                status = "saved" if created else "already exists"
                self.stdout.write(f"  → {status} (id={obj.id})")

            except Exception as e:
                self.stderr.write(f"Error: {e}")
                logger.exception("Error processing Telegram message")

        self.stdout.write("Listener active. Waiting for messages...")
        await client.run_until_disconnected()
