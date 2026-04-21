"""
Backfill: take every existing todo source (Google Tasks in 'big-sync' tasklist
and any existing '[todo] ...' Calendar events) and re-place them as Calendar
events with a consistent 30-minute duration, anchored at 08:00 with
conflict-aware slot scanning.

Flow:
1. Collect originals from both sources. Anchor day comes from due/updated
   for tasks and from event start date for existing todo events.
2. Delete originals up-front so they don't block their own re-placement.
3. Place everything fresh: 08:00→20:00, 30-min slots, skipping existing
   (non-todo) events and slots already filled earlier in this run.

Safe to re-run: each pass consolidates whatever's left.
"""
import logging
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from googleapiclient.discovery import build

from common.google_auth import get_credentials
from outputs.todos import create_todo_event_at, find_free_slot

logger = logging.getLogger(__name__)

_TASKLIST_TITLE = "big-sync"
_DURATION_MIN = 30
_TODO_PREFIX = "[todo] "
# Search horizon for existing [todo] events
_LOOKBACK_DAYS = 365
_LOOKAHEAD_DAYS = 365


class Command(BaseCommand):
    help = "Backfill todos (tasklist + existing [todo] events) into 30-min Calendar events."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print plan without writing.")
        parser.add_argument("--keep-originals", action="store_true",
                            help="Don't delete original tasks/events.")
        parser.add_argument("--max", type=int, default=0, help="Max items to process (0 = all).")

    def handle(self, *args, **options):
        creds = get_credentials()
        tasks_svc = build("tasks", "v1", credentials=creds)
        cal_svc = build("calendar", "v3", credentials=creds)

        tasklist_id = self._find_tasklist(tasks_svc)

        originals: list[dict] = []

        if tasklist_id:
            self.stdout.write(f"Fetching Google Tasks from '{_TASKLIST_TITLE}'...")
            for t in self._list_all_tasks(tasks_svc, tasklist_id):
                day = self._anchor_day_from_task(t)
                if day is None:
                    continue
                originals.append({
                    "source": "task",
                    "id": t["id"],
                    "title": t.get("title", "").strip(),
                    "notes": t.get("notes", ""),
                    "day": day,
                })
            self.stdout.write(f"  {len(originals)} tasks found")
        else:
            self.stdout.write(f"Tasklist '{_TASKLIST_TITLE}' not found — skipping task collection.")

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

        # Delete originals first (unless keeping or dry-run), so they don't
        # block their own re-placement.
        if not options["dry_run"] and not options["keep_originals"]:
            self.stdout.write("Deleting originals...")
            for o in originals:
                try:
                    if o["source"] == "task":
                        tasks_svc.tasks().delete(tasklist=tasklist_id, task=o["id"]).execute()
                    else:
                        cal_svc.events().delete(calendarId="primary", eventId=o["id"]).execute()
                except Exception:
                    logger.exception("Failed to delete %s %s", o["source"], o["id"])

        # Group by day in chronological order (preserve input order within day)
        by_day: dict[datetime, list[dict]] = {}
        for o in originals:
            by_day.setdefault(o["day"], []).append(o)

        total_created = 0
        total_skipped = 0

        for day, items in sorted(by_day.items()):
            self.stdout.write(f"\n{day.date().isoformat()} — {len(items)} todos")
            local_busy: list[tuple[datetime, datetime]] = []

            for o in items:
                title = o["title"]
                if not title:
                    continue

                slot = find_free_slot(
                    cal_svc, day, duration_min=_DURATION_MIN, extra_busy=local_busy
                )
                if slot is None:
                    self.stdout.write(f"  [SKIP] {title} — no free slot")
                    total_skipped += 1
                    continue

                if options["dry_run"]:
                    self.stdout.write(f"  [DRY] {slot.strftime('%H:%M')} → {title}")
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
                        self.stdout.write(f"  [OK]  {slot.strftime('%H:%M')} → {title}")
                    else:
                        total_skipped += 1
                        self.stdout.write(f"  [FAIL] {title}")

                local_busy.append((slot, slot + timedelta(minutes=_DURATION_MIN)))

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Created: {total_created}, skipped: {total_skipped}"
        ))

    def _find_tasklist(self, svc) -> str | None:
        result = svc.tasklists().list().execute()
        for tl in result.get("items", []):
            if tl.get("title") == _TASKLIST_TITLE:
                return tl["id"]
        return None

    def _list_all_tasks(self, svc, tasklist_id: str) -> list[dict]:
        tasks = []
        page_token = None
        while True:
            params = {
                "tasklist": tasklist_id,
                "showCompleted": False,
                "showHidden": False,
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
        # Filter to only those that actually start with the prefix
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
