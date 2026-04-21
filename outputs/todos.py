"""Write todos to Google Calendar as timed events on the primary calendar."""
import logging
import re
from datetime import datetime, timedelta

from googleapiclient.discovery import build

from common.google_auth import get_credentials
from common.models import WriteLog

logger = logging.getLogger(__name__)

_CALENDAR_ID = "primary"
_TITLE_PREFIX = "[todo] "
DEFAULT_DURATION_MIN = 15


def _build_service():
    return build("calendar", "v3", credentials=get_credentials())


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def _parse_start(date_str: str, time_str: str) -> datetime | None:
    if not date_str or not time_str:
        return None
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _find_existing(service, title: str, start: datetime) -> dict | None:
    """Search for a todo event with same title on the same day."""
    try:
        day_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        result = service.events().list(
            calendarId=_CALENDAR_ID,
            q=title,
            timeMin=day_start.isoformat() + "Z",
            timeMax=day_end.isoformat() + "Z",
            singleEvents=True,
        ).execute()
        title_norm = _norm(title)
        for ev in result.get("items", []):
            if _norm(ev.get("summary", "")) == title_norm:
                return ev
    except Exception:
        logger.exception("Error searching todo event: %s", title)
    return None


def upsert_todo_event(data: dict, fallback_datetime: datetime | None = None) -> str | None:
    """
    Create a Google Calendar event for a todo on the primary calendar.
    data keys: title, start_date, start_time, duration_minutes, notes, assigned_to
    fallback_datetime: used when start_date/start_time missing.
    """
    title = (data.get("title") or "").strip()
    if not title:
        return None

    # Only create todos assigned to me
    assigned = (data.get("assigned_to") or "me").lower().strip()
    if assigned not in ("me", "davide", "davide lancini", "@davidelenc"):
        logger.debug("Skipping todo assigned to %s: %s", assigned, title)
        return None

    start = _parse_start(data.get("start_date") or "", data.get("start_time") or "")
    if start is None:
        if fallback_datetime is None:
            logger.warning("Todo missing start and no fallback: %s", title)
            return None
        start = fallback_datetime.replace(second=0, microsecond=0)

    try:
        duration = int(data.get("duration_minutes") or DEFAULT_DURATION_MIN)
    except (TypeError, ValueError):
        duration = DEFAULT_DURATION_MIN
    if duration <= 0:
        duration = DEFAULT_DURATION_MIN

    end = start + timedelta(minutes=duration)
    prefixed_title = _TITLE_PREFIX + title

    service = _build_service()
    existing = _find_existing(service, prefixed_title, start)
    if existing:
        logger.debug("Todo event already exists: %s", title)
        return existing.get("id")

    body = {
        "summary": prefixed_title,
        "start": {"dateTime": start.isoformat(), "timeZone": "Europe/Rome"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Europe/Rome"},
    }
    if data.get("notes"):
        body["description"] = data["notes"]

    try:
        result = service.events().insert(calendarId=_CALENDAR_ID, body=body).execute()
        logger.info("Created todo event: %s @ %s", title, start.isoformat())
        WriteLog.objects.create(
            type=WriteLog.TYPE_TASK,
            title=title,
            detail=start.strftime("%Y-%m-%d %H:%M"),
        )
        return result.get("id")
    except Exception:
        logger.exception("Error creating todo event: %s", data)
        return None


def find_free_slot(
    service,
    day: datetime,
    duration_min: int = DEFAULT_DURATION_MIN,
    start_hour: int = 8,
    end_hour: int = 20,
) -> datetime | None:
    """
    Find the first free slot of `duration_min` on `day` starting at `start_hour`.
    Scans existing calendar events. Advances by 15-min steps.
    Returns a naive datetime (Europe/Rome assumed) or None if no slot found.
    """
    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    result = service.events().list(
        calendarId=_CALENDAR_ID,
        timeMin=day_start.isoformat() + "Z",
        timeMax=day_end.isoformat() + "Z",
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    busy: list[tuple[datetime, datetime]] = []
    for ev in result.get("items", []):
        s = ev.get("start", {})
        e = ev.get("end", {})
        s_dt = s.get("dateTime")
        e_dt = e.get("dateTime")
        if s_dt and e_dt:
            s_parsed = datetime.fromisoformat(s_dt.replace("Z", "+00:00")).replace(tzinfo=None)
            e_parsed = datetime.fromisoformat(e_dt.replace("Z", "+00:00")).replace(tzinfo=None)
            busy.append((s_parsed, e_parsed))
        elif s.get("date"):
            busy.append((day_start, day_end))

    busy.sort()

    candidate = day_start.replace(hour=start_hour, minute=0)
    limit = day_start.replace(hour=end_hour, minute=0)
    step = timedelta(minutes=15)
    dur = timedelta(minutes=duration_min)

    while candidate + dur <= limit:
        slot_end = candidate + dur
        conflict = False
        for b_start, b_end in busy:
            if candidate < b_end and slot_end > b_start:
                conflict = True
                candidate = b_end
                # Round up to next 15-min grid
                minute_mod = candidate.minute % 15
                if minute_mod:
                    candidate += timedelta(minutes=15 - minute_mod)
                candidate = candidate.replace(second=0, microsecond=0)
                break
        if not conflict:
            return candidate

    return None


def create_todo_event_at(
    service,
    title: str,
    start: datetime,
    duration_min: int,
    notes: str = "",
) -> str | None:
    """Create a todo event at an explicit start time. Used by backfill."""
    end = start + timedelta(minutes=duration_min)
    body = {
        "summary": _TITLE_PREFIX + title,
        "start": {"dateTime": start.isoformat(), "timeZone": "Europe/Rome"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Europe/Rome"},
    }
    if notes:
        body["description"] = notes

    try:
        result = service.events().insert(calendarId=_CALENDAR_ID, body=body).execute()
        WriteLog.objects.create(
            type=WriteLog.TYPE_TASK,
            title=title,
            detail=start.strftime("%Y-%m-%d %H:%M"),
        )
        return result.get("id")
    except Exception:
        logger.exception("Error creating backfill todo event: %s", title)
        return None
