"""
Fix Google Calendar events where start == end (zero duration).
Sets end = start + 1 hour for all such events from 2026-01-01 onwards.

Usage:
    python scripts/fix_zero_duration_events.py [--dry-run]
"""
import sys
import os
import django
from datetime import datetime, timedelta, timezone

# Bootstrap Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from googleapiclient.discovery import build
from common.google_auth import get_credentials

DRY_RUN = "--dry-run" in sys.argv
CALENDAR_ID = "primary"
TIME_MIN = "2026-01-01T00:00:00Z"
TIME_MAX = "2026-12-31T23:59:59Z"
PAGE_SIZE = 250


def main():
    service = build("calendar", "v3", credentials=get_credentials())

    fixed = 0
    checked = 0
    page_token = None

    print(f"Scanning events from {TIME_MIN[:10]} to {TIME_MAX[:10]}"
          + (" [DRY RUN]" if DRY_RUN else ""))

    while True:
        kwargs = dict(
            calendarId=CALENDAR_ID,
            timeMin=TIME_MIN,
            timeMax=TIME_MAX,
            maxResults=PAGE_SIZE,
            singleEvents=True,
            orderBy="startTime",
        )
        if page_token:
            kwargs["pageToken"] = page_token

        result = service.events().list(**kwargs).execute()
        items = result.get("items", [])

        for ev in items:
            checked += 1
            start = ev.get("start", {}).get("dateTime")
            end = ev.get("end", {}).get("dateTime")

            if not start or not end:
                continue  # all-day event, skip

            if start == end:
                new_end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
                print(f"  [{ev.get('summary', '(no title)')}] {start[:16]} → end set to {new_end[:16]}")
                if not DRY_RUN:
                    updated = dict(ev)
                    updated["end"] = {"dateTime": new_end, "timeZone": ev["start"].get("timeZone", "Europe/Rome")}
                    service.events().update(
                        calendarId=CALENDAR_ID,
                        eventId=ev["id"],
                        body=updated,
                    ).execute()
                fixed += 1

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    print(f"\nDone. {checked} events checked, {fixed} {'would be ' if DRY_RUN else ''}fixed.")


if __name__ == "__main__":
    main()
