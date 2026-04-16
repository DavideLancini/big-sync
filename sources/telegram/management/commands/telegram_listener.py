"""
Long-running Telegram listener using Telethon MTProto client.
Receives all incoming messages and saves them to the database.
Run as a systemd service: manage.py telegram_listener
"""
import asyncio
import logging

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
        self.stdout.write(self.style.SUCCESS(
            f"Listening as {me.first_name} (@{me.username})"
        ))

        @client.on(events.NewMessage(incoming=True))
        async def on_message(event):
            try:
                chat = await event.get_chat()
                sender = await event.get_sender()
                msg = event.message

                TelegramMessage.objects.get_or_create(
                    chat_id=msg.peer_id.user_id if hasattr(msg.peer_id, "user_id") else msg.chat_id,
                    message_id=msg.id,
                    defaults={
                        "chat_name": _get_chat_name(chat),
                        "sender_id": sender.id if sender else None,
                        "sender_name": _get_sender_name(sender),
                        "text": msg.message or "",
                        "date": msg.date or timezone.now(),
                        "raw": msg.to_dict(),
                    },
                )
                logger.info("Saved message %s from %s", msg.id, _get_sender_name(sender))
            except Exception:
                logger.exception("Error processing Telegram message")

        self.stdout.write("Listener active. Waiting for messages...")
        await client.run_until_disconnected()
