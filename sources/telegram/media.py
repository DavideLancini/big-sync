"""Shared helpers for Telegram media handling."""
import datetime
import re
import unicodedata
from pathlib import Path

from django.conf import settings
from telethon.tl.types import User, Channel, Chat

from sources.telegram.models import MediaType


# ── Dialog type helpers ────────────────────────────────────────────────────────

def dialog_type(dialog) -> str:
    """Return 'private', 'group', 'channel', or 'bot'."""
    entity = dialog.entity
    if isinstance(entity, User):
        return "bot" if entity.bot else "private"
    if isinstance(entity, Channel):
        return "group" if entity.megagroup else "channel"
    if isinstance(entity, Chat):
        return "group"
    return "unknown"


def should_skip(dialog) -> bool:
    """Skip broadcast channels and bots (dialog object)."""
    return dialog_type(dialog) in ("channel", "bot")


def should_skip_entity(entity) -> bool:
    """Skip broadcast channels and bots (raw entity, for use in listener)."""
    if isinstance(entity, User):
        return entity.bot
    if isinstance(entity, Channel):
        return not entity.megagroup  # megagroup=False → broadcast channel
    return False


# ── Media helpers ──────────────────────────────────────────────────────────────

def detect_media_type(msg) -> str:
    if msg.sticker:
        return MediaType.STICKER
    if msg.voice:
        return MediaType.VOICE
    if msg.video_note:
        return MediaType.VIDEO_NOTE
    if msg.gif:
        return MediaType.GIF
    if msg.video:
        return MediaType.VIDEO
    if msg.audio:
        return MediaType.AUDIO
    if msg.photo:
        return MediaType.PHOTO
    if msg.document:
        return MediaType.DOCUMENT
    return MediaType.TEXT


def sticker_text(msg) -> str:
    try:
        for attr in msg.document.attributes:
            if hasattr(attr, "alt"):
                return attr.alt
    except Exception:
        pass
    return ""


def message_text(msg) -> str:
    if msg.message:
        return msg.message
    if msg.sticker:
        return sticker_text(msg)
    return ""


def serialize(obj):
    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize(v) for v in obj]
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.hex()
    return obj


# ── Media download ─────────────────────────────────────────────────────────────

def _slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "_", value) or "unknown"


def chat_media_dir(chat_name: str) -> Path:
    slug = _slugify(chat_name)
    path = Path(settings.MEDIA_ROOT) / "telegram" / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


async def download_media(client, msg, chat_name: str) -> str | None:
    """Download media to media/telegram/<chat_slug>/ and return relative path."""
    try:
        dest_dir = chat_media_dir(chat_name)
        path = await client.download_media(msg, file=str(dest_dir) + "/")
        if path:
            return str(Path(path).relative_to(settings.MEDIA_ROOT))
    except Exception:
        pass
    return None
