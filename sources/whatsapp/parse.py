"""Extract fields from a neonize MessageEv into plain Python dicts."""
from datetime import datetime, timezone as dt_timezone

from .models import WaMediaType


def jid_str(jid) -> str:
    """Convert a protobuf JID to 'user@server' string."""
    if jid is None:
        return ""
    user = getattr(jid, "User", "") or ""
    server = getattr(jid, "Server", "") or ""
    if user and server:
        return f"{user}@{server}"
    return user or server


def detect_media_type(msg) -> str:
    """Pick the most descriptive media_type from a neonize Message payload."""
    if getattr(msg, "conversation", None) or getattr(
        getattr(msg, "extendedTextMessage", None), "text", None
    ):
        return WaMediaType.TEXT
    if getattr(msg, "imageMessage", None) and msg.imageMessage.url:
        return WaMediaType.PHOTO
    if getattr(msg, "videoMessage", None) and msg.videoMessage.url:
        return WaMediaType.VIDEO
    if getattr(msg, "audioMessage", None) and msg.audioMessage.url:
        return WaMediaType.VOICE if msg.audioMessage.PTT else WaMediaType.AUDIO
    if getattr(msg, "stickerMessage", None) and msg.stickerMessage.URL:
        return WaMediaType.STICKER
    if getattr(msg, "documentMessage", None) and msg.documentMessage.URL:
        return WaMediaType.DOCUMENT
    if getattr(msg, "locationMessage", None) and msg.locationMessage.degreesLatitude:
        return WaMediaType.LOCATION
    if getattr(msg, "contactMessage", None) and msg.contactMessage.vcard:
        return WaMediaType.CONTACT
    return WaMediaType.UNKNOWN


def message_text(msg) -> str:
    """Best-effort extract of human-readable text from a neonize Message."""
    text = getattr(msg, "conversation", "") or ""
    if text:
        return text
    ext = getattr(msg, "extendedTextMessage", None)
    if ext and getattr(ext, "text", ""):
        return ext.text
    for field in ("imageMessage", "videoMessage", "documentMessage"):
        sub = getattr(msg, field, None)
        if sub and getattr(sub, "caption", ""):
            return sub.caption
    return ""


def parse_event(event) -> dict:
    """Flatten a neonize MessageEv into a dict ready for the DB."""
    info = event.Info
    src = info.MessageSource
    msg = event.Message

    ts = info.Timestamp
    seconds: float
    if hasattr(ts, "seconds"):
        seconds = ts.seconds
    elif isinstance(ts, (int, float)):
        seconds = ts
        # whatsmeow sometimes returns milliseconds — detect by magnitude
        if seconds > 1e11:
            seconds /= 1000
    else:
        seconds = datetime.now(tz=dt_timezone.utc).timestamp()
    try:
        when = datetime.fromtimestamp(seconds, tz=dt_timezone.utc)
    except (ValueError, OSError):
        when = datetime.now(tz=dt_timezone.utc)

    chat_jid = jid_str(src.Chat)
    sender_jid = jid_str(src.Sender)

    return {
        "message_id": info.ID,
        "chat_jid": chat_jid,
        "sender_jid": sender_jid,
        "sender_name": info.PushName or "",
        "text": message_text(msg),
        "media_type": detect_media_type(msg),
        "date": when,
        "is_from_me": bool(src.IsFromMe),
        "is_group": bool(src.IsGroup),
    }
