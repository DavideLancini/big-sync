"""
One-time interactive authentication for the Telegram MTProto client.
Run once on the server to generate the .session file.
"""
import asyncio
from decouple import config
from django.core.management.base import BaseCommand
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError


class Command(BaseCommand):
    help = "Authenticate the Telegram client (one-time setup)"

    def handle(self, *args, **options):
        asyncio.run(self._auth())

    async def _auth(self):
        api_id = config("TELEGRAM_API_ID", cast=int)
        api_hash = config("TELEGRAM_API_HASH")
        session_name = config("TELEGRAM_SESSION_NAME", default="big_sync_telegram")

        client = TelegramClient(session_name, api_id, api_hash)
        await client.connect()

        if await client.is_user_authorized():
            me = await client.get_me()
            self.stdout.write(self.style.SUCCESS(
                f"Already authenticated as {me.first_name} (@{me.username})"
            ))
            await client.disconnect()
            return

        phone = input("Phone number (with country code, e.g. +39...): ").strip()
        await client.send_code_request(phone)

        code = input("OTP code received on Telegram: ").strip()
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            password = input("2FA password: ").strip()
            await client.sign_in(password=password)

        me = await client.get_me()
        self.stdout.write(self.style.SUCCESS(
            f"Authenticated as {me.first_name} (@{me.username}). Session saved."
        ))
        await client.disconnect()
