"""WhatsApp media download + path helpers. Mirror of sources/telegram/media.py."""
import logging
import re
import unicodedata
from pathlib import Path

from django.conf import settings

from .models import WaMediaType

logger = logging.getLogger(__name__)


_EXT_BY_TYPE = {
    WaMediaType.PHOTO: "jpg",
    WaMediaType.VIDEO: "mp4",
    WaMediaType.VOICE: "ogg",
    WaMediaType.AUDIO: "mp3",
    WaMediaType.STICKER: "webp",
    WaMediaType.GIF: "mp4",
    WaMediaType.DOCUMENT: "bin",
}


def _slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "_", value) or "unknown"


def chat_media_dir(chat_name: str) -> Path:
    slug = _slugify(chat_name)
    path = Path(settings.MEDIA_ROOT) / "whatsapp" / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


async def download_media(client, event, chat_name: str, media_type: str) -> str | None:
    """Download an incoming media message to media/whatsapp/<chat_slug>/<msg_id>.<ext>.

    Returns the relative path (inside MEDIA_ROOT) or None on failure.
    """
    if media_type not in _EXT_BY_TYPE:
        return None

    try:
        dest_dir = chat_media_dir(chat_name)
        msg_id = event.Info.ID or "unknown"
        ext = _EXT_BY_TYPE[media_type]
        dest = dest_dir / f"{msg_id}.{ext}"

        if dest.exists():
            return str(dest.relative_to(settings.MEDIA_ROOT))

        await client.download_any(event.Message, path=str(dest))
        if dest.exists() and dest.stat().st_size > 0:
            return str(dest.relative_to(settings.MEDIA_ROOT))
    except Exception:
        logger.exception("WhatsApp media download failed")
    return None
