"""
One-off pairing for the WhatsApp session.

Run once on the server:
    python manage.py whatsapp_pair

It requests an 8-char pair code for the phone number in WHATSAPP_PHONE (.env),
prints it, and waits for the phone to confirm. After success the session is
persisted to WHATSAPP_SESSION_FILE and the command exits. Subsequent runs of
whatsapp_listener resume the same session without re-pairing.
"""
import asyncio
import logging

from decouple import config
from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


def _session_path() -> str:
    return config(
        "WHATSAPP_SESSION_FILE",
        default=str(settings.BASE_DIR / "whatsapp_session.sqlite3"),
    )


class Command(BaseCommand):
    help = "Pair a phone number with WhatsApp via code (first-time setup)."

    def handle(self, *args, **options):
        phone = config("WHATSAPP_PHONE", default="").strip().replace(" ", "").replace("+", "")
        if not phone:
            self.stderr.write("Set WHATSAPP_PHONE in .env (e.g. 393314649642)")
            return

        session_file = _session_path()
        self.stdout.write(f"Session file: {session_file}")
        self.stdout.write(f"Phone:        +{phone}")

        asyncio.run(self._pair(session_file, phone))

    async def _pair(self, session_file: str, phone: str):
        from neonize.aioze.client import NewAClient
        from neonize.aioze.events import ConnectedEv, PairStatusEv

        client = NewAClient(session_file)
        done = asyncio.Event()

        @client.event(ConnectedEv)
        async def _on_connected(_, __):
            self.stdout.write("Connected to WhatsApp servers.")

        @client.event(PairStatusEv)
        async def _on_paired(_, ev):
            self.stdout.write(self.style.SUCCESS(
                f"Paired as {ev.ID.User}@{ev.ID.Server}"
            ))
            done.set()

        @client.paircode
        async def _on_paircode(_, code: str, connected: bool = True):
            if connected:
                self.stdout.write(f"Pair code processed: {code}")
            else:
                self.stdout.write(self.style.WARNING(
                    f"\n→ Open WhatsApp on your phone → Linked Devices → Link with phone number → enter:\n"
                    f"\n    {code}\n"
                ))

        connect_task = asyncio.create_task(client.connect())

        # Give connect() a moment to establish the websocket before PairPhone
        await asyncio.sleep(2)
        try:
            code = await client.PairPhone(phone, True)
            self.stdout.write(f"Requested pair code: {code}")
        except Exception as e:
            self.stderr.write(f"PairPhone failed: {e}")
            connect_task.cancel()
            return

        try:
            await asyncio.wait_for(done.wait(), timeout=300)
        except asyncio.TimeoutError:
            self.stderr.write("Timed out waiting for pairing (5 min).")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
            try:
                await connect_task
            except Exception:
                pass
