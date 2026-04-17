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
    """Search for an event with the same title around the given date."""
    if not summary or not date:
        return None
    try:
        # Search ±3 days around target date
        try:
            target = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return None
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

    if existing:
        return _enrich_event(service, existing, data)

    body = _build_body(data)
    if not body:
        logger.warning("Could not build calendar event body for: %s", data)
        return None

    try:
        result = service.events().insert(calendarId=_CALENDAR_ID, body=body).execute()
        logger.info("Created calendar event: %s", title)
        WriteLog.objects.create(type=WriteLog.TYPE_EVENT, title=title, detail=data.get("date") or "")
        return result.get("id")
    except Exception:
        logger.exception("Error creating calendar event: %s", data)
        return None
