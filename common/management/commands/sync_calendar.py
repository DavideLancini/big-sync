"""Sync events from every Google Calendar into the local CachedEvent table.

Pulls a configurable window (default: last 60 days + next 180 days) from
every calendar visible to the authenticated user. Soft-deletes events that
are no longer present in Google. Marks [todo]-prefixed events as is_todo.
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from googleapiclient.discovery import build

from common.google_auth import get_credentials
from common.models import CachedEvent

logger = logging.getLogger(__name__)


def _parse_dt(start_or_end: dict):
    """Return (datetime|None, all_day_bool). dateTime is ISO with tz; date is YYYY-MM-DD."""
    if not start_or_end:
        return None, False
    if "dateTime" in start_or_end:
        from datetime import datetime
        return datetime.fromisoformat(start_or_end["dateTime"]), False
    if "date" in start_or_end:
        from datetime import datetime, time
        d = datetime.fromisoformat(start_or_end["date"])
        return d.replace(tzinfo=timezone.get_current_timezone()), True
    return None, False


def _attendees(ev: dict) -> list:
    return [
        {"email": a.get("email", ""), "name": a.get("displayName", ""),
         "status": a.get("responseStatus", "")}
        for a in ev.get("attendees", [])
    ]


def _meet_link(ev: dict) -> str:
    cd = ev.get("conferenceData") or {}
    for ep in cd.get("entryPoints") or []:
        if ep.get("entryPointType") == "video":
            return ep.get("uri", "")
    return ""


def _list_calendars(svc):
    return svc.calendarList().list().execute().get("items", [])


def _list_events(svc, calendar_id: str, time_min: str, time_max: str):
    page_token = None
    while True:
        kwargs = dict(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            maxResults=2500,
        )
        if page_token:
            kwargs["pageToken"] = page_token
        resp = svc.events().list(**kwargs).execute()
        for ev in resp.get("items", []):
            yield ev
        page_token = resp.get("nextPageToken")
        if not page_token:
            return


class Command(BaseCommand):
    help = "Sync all events from all Google Calendars into the local CachedEvent table"

    def add_arguments(self, parser):
        parser.add_argument("--past-days", type=int, default=60)
        parser.add_argument("--future-days", type=int, default=180)
        parser.add_argument("--calendar", type=str, default=None,
                            help="Sync only this calendar id (default: all)")

    def handle(self, *args, **opts):
        now = timezone.now()
        time_min = (now - timedelta(days=opts["past_days"])).isoformat()
        time_max = (now + timedelta(days=opts["future_days"])).isoformat()

        svc = build("calendar", "v3", credentials=get_credentials())
        calendars = _list_calendars(svc)
        if opts["calendar"]:
            calendars = [c for c in calendars if c["id"] == opts["calendar"]]

        self.stdout.write(
            f"Sync window: {time_min[:10]} → {time_max[:10]} | "
            f"calendari: {len(calendars)}"
        )

        total_seen = 0
        total_new = 0
        total_updated = 0
        total_deleted = 0

        for cal in calendars:
            # Normalize the primary calendar's id to the literal "primary"
            # so it lines up with what outputs/*.py uses when reading/writing.
            cal_id = "primary" if cal.get("primary") else cal["id"]
            cal_name = cal.get("summary", "")
            self.stdout.write(f"\n--- {cal_name} ({cal_id}) ---")

            seen_ids: set[str] = set()
            new_ct = 0
            upd_ct = 0

            for ev in _list_events(svc, cal_id, time_min, time_max):
                gid = ev.get("id")
                if not gid:
                    continue
                seen_ids.add(gid)
                title = ev.get("summary") or ""
                is_todo = title.startswith("[todo]")
                start_dt, all_day = _parse_dt(ev.get("start"))
                end_dt, _ = _parse_dt(ev.get("end"))

                defaults = {
                    "calendar_name": cal_name,
                    "title": title[:512],
                    "start_at": start_dt,
                    "end_at": end_dt,
                    "all_day": all_day,
                    "location": (ev.get("location") or "")[:512],
                    "description": ev.get("description") or "",
                    "attendees": _attendees(ev),
                    "meet_link": _meet_link(ev),
                    "organizer_email": (ev.get("organizer") or {}).get("email", "")[:255],
                    "is_todo": is_todo,
                    "raw": ev,
                    "last_seen_at": now,
                    "deleted_at": None,
                }
                obj, created = CachedEvent.objects.update_or_create(
                    google_id=gid, calendar_id=cal_id, defaults=defaults,
                )
                if created:
                    new_ct += 1
                else:
                    upd_ct += 1

            # Soft-delete events that disappeared from Google within the sync window.
            with transaction.atomic():
                stale = CachedEvent.objects.filter(
                    calendar_id=cal_id,
                    deleted_at__isnull=True,
                    start_at__gte=now - timedelta(days=opts["past_days"]),
                    start_at__lte=now + timedelta(days=opts["future_days"]),
                ).exclude(google_id__in=seen_ids)
                del_ct = stale.count()
                stale.update(deleted_at=now)

            total_seen += len(seen_ids)
            total_new += new_ct
            total_updated += upd_ct
            total_deleted += del_ct
            self.stdout.write(
                f"  seen={len(seen_ids)}  new={new_ct}  updated={upd_ct}  soft-deleted={del_ct}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nTotale: seen={total_seen} new={total_new} updated={total_updated} "
            f"soft-deleted={total_deleted}"
        ))
