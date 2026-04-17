"""Shared helper for TELEGRAM_IGNORE_CHATS normalization."""
from django.conf import settings

_CHANNEL_PREFIX = 1_000_000_000_000


def ignored_chat_ids() -> set[int]:
    """
    Return a set of all ignored chat IDs in both formats:
    - canonical (-1001822864957 → 1001822864957)
    - bare (1001822864957 → also adds 1822864957)

    This handles the mismatch between Bot API format (-100XXXXXXX)
    and the bare channel_id Telethon uses in peer events.
    """
    ids: set[int] = set()
    for val in getattr(settings, "TELEGRAM_IGNORE_CHATS", []):
        try:
            v = abs(int(str(val).strip()))
            ids.add(v)
            if v > _CHANNEL_PREFIX:
                ids.add(v - _CHANNEL_PREFIX)
        except ValueError:
            pass
    return ids
