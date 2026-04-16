"""Shared helpers for Telegram media handling."""
import datetime

from sources.telegram.models import MediaType


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
    """Return the emoji alt-text of a sticker, if available."""
    try:
        for attr in msg.document.attributes:
            if hasattr(attr, "alt"):
                return attr.alt
    except Exception:
        pass
    return ""


def message_text(msg) -> str:
    """Return the best text representation for any message type."""
    if msg.message:
        return msg.message
    if msg.sticker:
        return sticker_text(msg)
    return ""


def serialize(obj):
    """Recursively convert non-JSON-serializable types."""
    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize(v) for v in obj]
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.hex()
    return obj
