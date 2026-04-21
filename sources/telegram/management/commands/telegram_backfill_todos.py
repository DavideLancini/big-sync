"""
Backfill: take every existing todo source (all Google Tasks lists + any
existing '[todo] ...' Calendar events) and re-place them as 30-minute
Calendar events. Anchor day is the task/event origin day; if that day is
full, overflow onto following days up to TODAY (never beyond).

Flow:
1. Collect originals from every tasklist AND existing '[todo] ' Calendar events.
2. Delete originals up-front so they don't block their own re-placement.
3. Place chronologically: try anchor day, then +1, +2, ... up to today, skipping
   slots conflicting with existing (non-todo) events and same-day placements.

Safe to re-run: consolidates whatever's left.
"""
import logging
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from googleapiclient.discovery import build

from common.google_auth import get_credentials
from outputs.todos import create_todo_event_at, find_free_slot

logger = logging.getLogger(__name__)

_DURATION_MIN = 30
_TODO_PREFIX = "[todo] "
_LOOKBACK_DAYS = 365
_LOOKAHEAD_DAYS = 365


class Command(BaseCommand):
    help = "Backfill todos from all tasklists + existing [todo] events into 30-min Calendar events."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print plan without writing.")
        parser.add_argument("--keep-originals", action="store_true",
                            help="Don't delete original tasks/events.")
        parser.add_argument("--max", type=int, default=0, help="Max items to process (0 = all).")

    def handle(self, *args, **options):
        creds = get_credentials()
        tasks_svc = build("tasks", "v1", credentials=creds)
        cal_svc = build("calendar", "v3", credentials=creds)

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        originals: list[dict] = []

        tasklists = tasks_svc.tasklists().list().execute().get("items", [])
        self.stdout.write(f"Scanning {len(tasklists)} tasklist(s)...")
        for tl in tasklists:
            tl_id = tl["id"]
            tl_title = tl.get("title", "?")
            count_before = len(originals)
            for t in self._list_all_tasks(tasks_svc, tl_id):
                day = self._anchor_day_from_task(t)
                if day is None:
                    continue
                originals.append({
                    "source": "task",
                    "tasklist_id": tl_id,
                    "id": t["id"],
                    "title": t.get("title", "").strip(),
                    "notes": t.get("notes", ""),
                    "day": day,
                })
            self.stdout.write(f"  {tl_title}: +{len(originals) - count_before} tasks")

        self.stdout.write("Fetching existing [todo] calendar events...")
        existing_events = self._list_todo_events(cal_svc)
        self.stdout.write(f"  {len(existing_events)} events found")
        for ev in existing_events:
            summary = ev.get("summary", "")
            if not summary.startswith(_TODO_PREFIX):
                continue
            day = self._anchor_day_from_event(ev)
            if day is None:
                continue
            originals.append({
                "source": "event",
                "id": ev["id"],
                "title": summary[len(_TODO_PREFIX):].strip(),
                "notes": ev.get("description", ""),
                "day": day,
            })

        if options["max"]:
            originals = originals[: options["max"]]

        self.stdout.write(f"\nTotal originals: {len(originals)}")

        if not options["dry_run"] and not options["keep_originals"]:
            self.stdout.write("Deleting originals...")
            for o in originals:
                try:
                    if o["source"] == "task":
                        tasks_svc.tasks().delete(
                            tasklist=o["tasklist_id"], task=o["id"]
                        ).execute()
                    else:
                        cal_svc.events().delete(
                            calendarId="primary", eventId=o["id"]
                        ).execute()
                except Exception:
                    logger.exception("Failed to delete %s %s", o["source"], o["id"])

        # Chronological order, then fill days sequentially with overflow
        originals.sort(key=lambda o: o["day"])

        busy_per_day: dict[datetime, list[tuple[datetime, datetime]]] = {}
        total_created = 0
        total_skipped = 0

        for o in originals:
            title = o["title"]
            if not title:
                continue

            start_day = o["day"]
            slot = None
            landed_day = None

            # If origin is in the future: only try that day (no overflow)
            if start_day > today:
                busy = busy_per_day.setdefault(start_day, [])
                slot = find_free_slot(cal_svc, start_day, duration_min=_DURATION_MIN, extra_busy=busy)
                if slot:
                    landed_day = start_day
            else:
                # Past/today: try anchor day, then +1, +2, ... up to today
                d = start_day
                while d <= today:
                    busy = busy_per_day.setdefault(d, [])
                    slot = find_free_slot(cal_svc, d, duration_min=_DURATION_MIN, extra_busy=busy)
                    if slot:
                        landed_day = d
                        break
                    d += timedelta(days=1)

            if slot is None or landed_day is None:
                self.stdout.write(f"  [SKIP] {start_day.date()} → {title}")
                total_skipped += 1
                continue

            overflow_marker = "" if landed_day == start_day else f" (da {start_day.date()})"

            if options["dry_run"]:
                self.stdout.write(
                    f"  [DRY] {landed_day.date()} {slot.strftime('%H:%M')} → {title}{overflow_marker}"
                )
            else:
                ev_id = create_todo_event_at(
                    cal_svc,
                    title=title,
                    start=slot,
                    duration_min=_DURATION_MIN,
                    notes=o["notes"],
                )
                if ev_id:
                    total_created += 1
                    self.stdout.write(
                        f"  [OK]  {landed_day.date()} {slot.strftime('%H:%M')} → {title}{overflow_marker}"
                    )
                else:
                    total_skipped += 1
                    self.stdout.write(f"  [FAIL] {title}")

            busy_per_day[landed_day].append(
                (slot, slot + timedelta(minutes=_DURATION_MIN))
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Created: {total_created}, skipped: {total_skipped}"
        ))

    def _list_all_tasks(self, svc, tasklist_id: str) -> list[dict]:
        tasks = []
        page_token = None
        while True:
            params = {
                "tasklist": tasklist_id,
                "showCompleted": True,
                "showHidden": True,
                "maxResults": 100,
            }
            if page_token:
                params["pageToken"] = page_token
            result = svc.tasks().list(**params).execute()
            tasks.extend(result.get("items", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return tasks

    def _list_todo_events(self, svc) -> list[dict]:
        now = datetime.utcnow()
        time_min = (now - timedelta(days=_LOOKBACK_DAYS)).isoformat() + "Z"
        time_max = (now + timedelta(days=_LOOKAHEAD_DAYS)).isoformat() + "Z"
        events: list[dict] = []
        page_token = None
        while True:
            params = {
                "calendarId": "primary",
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": True,
                "q": _TODO_PREFIX.strip(),
                "maxResults": 2500,
            }
            if page_token:
                params["pageToken"] = page_token
            result = svc.events().list(**params).execute()
            events.extend(result.get("items", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return [e for e in events if e.get("summary", "").startswith(_TODO_PREFIX)]

    def _anchor_day_from_task(self, task: dict) -> datetime | None:
        raw = task.get("due") or task.get("updated")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            return None

    def _anchor_day_from_event(self, ev: dict) -> datetime | None:
        s = ev.get("start", {})
        raw = s.get("dateTime") or s.get("date")
        if not raw:
            return None
        try:
            if "T" in raw:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
            else:
                dt = datetime.fromisoformat(raw)
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            return None
