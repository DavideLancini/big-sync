"""Gmail API client — fetch, parse, and label messages."""
import base64
import logging
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime

from googleapiclient.discovery import build

from common.google_auth import get_credentials

logger = logging.getLogger(__name__)

GMAIL_LABEL_PREFIX = "bs/"


def get_service():
    creds = get_credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ── Header helpers ──────────────────────────────────────────────────────────

def _decode_header_value(value: str) -> str:
    parts = decode_header(value or "")
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return _decode_header_value(h["value"])
    return ""


def _parse_sender(from_value: str) -> tuple[str, str]:
    """Return (display_name, email_address)."""
    name, addr = parseaddr(from_value)
    return _decode_header_value(name), addr.lower()


def _parse_date(date_str: str):
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


# ── Body extraction ──────────────────────────────────────────────────────────

def _extract_body(payload: dict) -> str:
    """Recursively extract plain-text body from Gmail message payload."""
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data", "")

    if mime == "text/plain" and data:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    parts = payload.get("parts", [])
    # Prefer text/plain parts; fall back to text/html stripped
    for part in parts:
        if part.get("mimeType") == "text/plain":
            text = _extract_body(part)
            if text:
                return text

    for part in parts:
        text = _extract_body(part)
        if text:
            return text

    if mime == "text/html" and data:
        raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        # strip tags crudely (body is stored for AI, not display)
        import re
        return re.sub(r"<[^>]+>", " ", raw)

    return ""


# ── Message parsing ──────────────────────────────────────────────────────────

def parse_message(msg: dict) -> dict:
    """Parse a full Gmail message dict into a flat dict for the model."""
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])

    from_raw = _header(headers, "From")
    sender_name, sender_email = _parse_sender(from_raw)

    return {
        "gmail_id": msg["id"],
        "thread_id": msg.get("threadId", ""),
        "subject": _header(headers, "Subject")[:1000],
        "sender": sender_name[:500],
        "sender_email": sender_email[:254],
        "snippet": msg.get("snippet", "")[:500],
        "body_text": _extract_body(payload)[:20000],
        "date": _parse_date(_header(headers, "Date")),
        "gmail_labels": msg.get("labelIds", []),
    }


# ── Label management ─────────────────────────────────────────────────────────

def _list_gmail_labels(service) -> dict[str, str]:
    """Return {label_name: label_id} for all user labels."""
    result = service.users().labels().list(userId="me").execute()
    return {lb["name"]: lb["id"] for lb in result.get("labels", [])}


def get_or_create_gmail_label(service, tag_name: str) -> str:
    """Return Gmail label ID for bs/<tag_name>, creating it if needed."""
    label_name = f"{GMAIL_LABEL_PREFIX}{tag_name}"
    existing = _list_gmail_labels(service)
    if label_name in existing:
        return existing[label_name]

    result = service.users().labels().create(
        userId="me",
        body={
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    return result["id"]


def apply_labels_to_message(service, gmail_id: str, label_ids: list[str]):
    if not label_ids:
        return
    service.users().messages().modify(
        userId="me",
        id=gmail_id,
        body={"addLabelIds": label_ids},
    ).execute()
