"""Write events to Google Calendar."""
import logging
import re
from datetime import datetime, timedelta

from googleapiclient.discovery import build

from common.google_auth import get_credentials
from common.models import WriteLog

logger = logging.getLogger(__name__)

_CALENDAR_ID = "primary"


def _build_service():
    return build("calendar", "v3", credentials=get_credentials())


def _parse_datetime(date_str: str, time_str: str) -> str | None:
    """Return RFC3339 datetime string or None. Falls back to date-only if time can't be parsed."""
    if not date_str:
        return None
    if time_str:
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            return dt.isoformat()
        except ValueError:
            pass  # unparseable time (e.g. "evening") → fall back to date-only
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        return None


def _events_overlap(a: dict, b: dict) -> bool:
    """True if two event dicts look like the same event (title + date match)."""
    def norm(s):
        return re.sub(r"\s+", " ", (s or "").lower().strip())

    if norm(a.get("summary")) != norm(b.get("summary")):
        return False

    # Compare start dates (ignore time for fuzzy match)
    def start_date(ev):
        start = ev.get("start", {})
        dt = start.get("dateTime") or start.get("date") or ""
        return dt[:10]

    return start_date(a) == start_date(b)


def _find_existing(service, summary: str, date: str) -> dict | None:
    """Search for an event with the same title around the given date.

    First consults the local CachedEvent table (fast + complete view across
    all calendars). Falls back to a Google API search if the cache is empty.
    Returns a dict shaped like a Google event payload so the rest of the
    pipeline keeps working unchanged.
    """
    if not summary or not date:
        return None

    from common.models import CachedEvent

    try:
        target = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return None

    # Local cache lookup: same calendar, ±3 days, exact-title match.
    norm = re.sub(r"\s+", " ", summary.lower().strip())
    cached = CachedEvent.objects.filter(
        calendar_id=_CALENDAR_ID,
        is_todo=False,
        deleted_at__isnull=True,
        start_at__date__gte=(target - timedelta(days=3)).date(),
        start_at__date__lte=(target + timedelta(days=3)).date(),
    )
    for c in cached:
        if re.sub(r"\s+", " ", (c.title or "").lower().strip()) == norm:
            return c.raw or {"id": c.google_id, "summary": c.title,
                              "start": {"date": str(c.start_at.date()) if c.start_at else date}}

    # Cache miss → fall back to Google API
    try:
        time_min = (target - timedelta(days=3)).isoformat() + "Z"
        time_max = (target + timedelta(days=4)).isoformat() + "Z"
        result = service.events().list(
            calendarId=_CALENDAR_ID,
            q=summary,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
        ).execute()
        for ev in result.get("items", []):
            if _events_overlap(ev, {"summary": summary, "start": {"date": date}}):
                return ev
    except Exception:
        logger.exception("Error searching calendar event: %s", summary)
    return None


def _build_body(data: dict) -> dict:
    body = {"summary": data.get("title", "").strip()}

    start_dt = _parse_datetime(data.get("date"), data.get("time"))
    end_dt = _parse_datetime(data.get("end_date") or data.get("date"),
                             data.get("end_time"))

    if start_dt and "T" in start_dt:
        # Has time component
        body["start"] = {"dateTime": start_dt, "timeZone": "Europe/Rome"}
        if end_dt and "T" in end_dt:
            body["end"] = {"dateTime": end_dt, "timeZone": "Europe/Rome"}
        else:
            # Default 1 hour
            body["end"] = {"dateTime": (datetime.fromisoformat(start_dt) + timedelta(hours=1)).isoformat(),
                           "timeZone": "Europe/Rome"}
    elif start_dt:
        body["start"] = {"date": start_dt[:10]}
        body["end"] = {"date": (datetime.strptime(start_dt[:10], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")}
    else:
        return {}

    if data.get("location"):
        body["location"] = data["location"]
    if data.get("description"):
        body["description"] = data["description"]
    if data.get("meet_link"):
        body.setdefault("description", "")
        body["description"] = (body["description"] + "\n" + data["meet_link"]).strip()

    attendees = data.get("attendees") or []
    if attendees:
        # Calendar API requires email; skip name-only attendees
        with_email = [{"email": a} for a in attendees if "@" in str(a)]
        if with_email:
            body["attendees"] = with_email

    return body


def _enrich_event(service, existing: dict, data: dict) -> str | None:
    """Add missing fields to existing event."""
    event_id = existing.get("id")
    updated = dict(existing)
    changed = False

    if data.get("location") and not existing.get("location"):
        updated["location"] = data["location"]
        changed = True

    # Merge meet_link into description
    meet_link = data.get("meet_link") or ""
    if meet_link and meet_link not in (existing.get("description") or ""):
        updated["description"] = ((existing.get("description") or "") + "\n" + meet_link).strip()
        changed = True

    if data.get("description") and not existing.get("description"):
        updated["description"] = data["description"]
        changed = True

    # Add new attendees
    if data.get("attendees"):
        existing_emails = {
            (a.get("email") or "").lower()
            for a in existing.get("attendees", [])
        }
        new_attendees = [
            {"email": a} for a in data["attendees"]
            if "@" in str(a) and a.lower() not in existing_emails
        ]
        if new_attendees:
            updated["attendees"] = existing.get("attendees", []) + new_attendees
            changed = True

    if not changed:
        logger.debug("Calendar event already up to date: %s", data.get("title"))
        return event_id

    try:
        service.events().update(
            calendarId=_CALENDAR_ID,
            eventId=event_id,
            body=updated,
        ).execute()
        logger.info("Enriched calendar event: %s", data.get("title"))
        return event_id
    except Exception:
        logger.exception("Error enriching calendar event: %s", event_id)
        return event_id


def update_event(google_id: str, calendar_id: str = _CALENDAR_ID,
                 fields: dict | None = None) -> bool:
    """Patch an existing Google Calendar event and refresh the local cache.

    fields keys mirror Google's event resource: summary, location, description,
    start (dict {dateTime, timeZone}|{date}), end, attendees.
    Returns True on success.
    """
    if not google_id or not fields:
        return False
    service = _build_service()
    try:
        result = service.events().patch(
            calendarId=calendar_id, eventId=google_id, body=fields,
        ).execute()
    except Exception:
        logger.exception("Error updating calendar event %s", google_id)
        return False

    try:
        from common.models import CachedEvent
        from django.utils import timezone as _tz
        cached = CachedEvent.objects.filter(
            google_id=google_id, calendar_id=calendar_id,
        ).first()
        if cached:
            if "summary" in fields:
                cached.title = (fields.get("summary") or "")[:512]
            if "location" in fields:
                cached.location = (fields.get("location") or "")[:512]
            if "description" in fields:
                cached.description = fields.get("description") or ""
            cached.raw = result
            cached.last_seen_at = _tz.now()
            cached.save()
    except Exception:
        logger.exception("CachedEvent refresh failed for %s", google_id)
    return True


def delete_event(google_id: str, calendar_id: str = _CALENDAR_ID) -> bool:
    """Delete a Google Calendar event and soft-delete the local cache row."""
    if not google_id:
        return False
    service = _build_service()
    try:
        service.events().delete(calendarId=calendar_id, eventId=google_id).execute()
    except Exception:
        logger.exception("Error deleting calendar event %s", google_id)
        return False

    try:
        from common.models import CachedEvent
        from django.utils import timezone as _tz
        CachedEvent.objects.filter(
            google_id=google_id, calendar_id=calendar_id,
        ).update(deleted_at=_tz.now())
    except Exception:
        logger.exception("CachedEvent soft-delete failed for %s", google_id)
    return True


def upsert_event(data: dict) -> str | None:
    """
    Create or enrich a Google Calendar event.
    Returns the event id or None on error.
    data keys: title, date, time, end_date, end_time, location, description, attendees, meet_link, confidence
    """
    title = (data.get("title") or "").strip()
    date = (data.get("date") or "").strip()

    if not title or not date:
        return None

    # Skip low-confidence events
    if data.get("confidence") == "low":
        logger.debug("Skipping low-confidence event: %s", title)
        return None

    service = _build_service()
    existing = _find_existing(service, title, date)

    # AI second-pass: even if exact-title match failed, look for fuzzy
    # duplicates (different language, more/less detail) within ±3 days.
    if existing is None:
        existing = _find_existing_ai(service, data)

    if existing:
        return _enrich_event(service, existing, data)

    body = _build_body(data)
    if not body:
        logger.warning("Could not build calendar event body for: %s", data)
        return None

    target_calendar = _route_calendar_for(data)
    try:
        result = service.events().insert(calendarId=target_calendar, body=body).execute()
        logger.info("Created calendar event: %s (calendar=%s)", title, target_calendar)
        WriteLog.objects.create(type=WriteLog.TYPE_EVENT, title=title, detail=data.get("date") or "")
        return result.get("id")
    except Exception:
        logger.exception("Error creating calendar event: %s", data)
        return None


def _route_calendar_for(data: dict) -> str:
    """Pick the destination calendar (primary / work / chiara) for a new event.

    Conservative: any failure or low-confidence answer keeps the event on primary.
    """
    try:
        from workflows.routing import classify_event
        from common.calendars import ROUTE_TO_CALENDAR
    except Exception:
        return _CALENDAR_ID
    try:
        ev = {
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "location": data.get("location", ""),
            "attendees": data.get("attendees", []),
            "is_todo": False,
        }
        route = classify_event(ev)
        return ROUTE_TO_CALENDAR.get(route, _CALENDAR_ID)
    except Exception:
        logger.exception("Calendar routing failed, keeping primary")
        return _CALENDAR_ID


def _find_existing_ai(service, data: dict) -> dict | None:
    """Use Gemini to spot fuzzy duplicates (different language, varying detail).

    Pulls all CachedEvent rows on the target calendar within ±3 days, then
    asks Gemini whether any of them is the same activity. Falls back to None
    on any error so we never block legitimate creations.
    """
    try:
        from common.models import CachedEvent
        from workflows.dedup import is_same_event
    except Exception:
        return None

    try:
        target = datetime.strptime(data.get("date", ""), "%Y-%m-%d")
    except ValueError:
        return None

    candidates_qs = CachedEvent.objects.filter(
        calendar_id=_CALENDAR_ID,
        is_todo=False,
        deleted_at__isnull=True,
        start_at__date__gte=(target - timedelta(days=3)).date(),
        start_at__date__lte=(target + timedelta(days=3)).date(),
    ).only("google_id", "title", "start_at", "location", "raw")[:30]
    candidates = [
        {
            "id": c.google_id,
            "title": c.title,
            "date": c.start_at.date().isoformat() if c.start_at else "",
            "time": c.start_at.strftime("%H:%M") if c.start_at else "",
            "location": c.location,
        }
        for c in candidates_qs
    ]
    if not candidates:
        return None

    new_event = {
        "title": data.get("title", ""),
        "date": data.get("date", ""),
        "time": data.get("time", ""),
        "location": data.get("location", ""),
    }
    match_id = is_same_event(new_event, candidates)
    if not match_id:
        return None

    cached = CachedEvent.objects.filter(
        calendar_id=_CALENDAR_ID, google_id=match_id, deleted_at__isnull=True,
    ).first()
    if not cached:
        return None
    return cached.raw or {"id": cached.google_id, "summary": cached.title}
