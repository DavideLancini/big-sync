"""
Convert existing Google Tasks (from the 'big-sync' tasklist) into Calendar events.

Placement rule (backfill only):
- Anchor day = task's 'due' date if present, else its 'updated' date.
- Start scanning at 08:00 on that day, 15-min slots, skip conflicts with existing
  events (and tasks already placed during this backfill).
- Duration = 15 minutes.
- After creating the event, delete the original Google Task.

Idempotent per invocation: each completed task is deleted from the tasklist,
so re-running only processes what's still there.
"""
import logging
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from googleapiclient.discovery import build

from common.google_auth import get_credentials
from outputs.todos import create_todo_event_at, find_free_slot

logger = logging.getLogger(__name__)

_TASKLIST_TITLE = "big-sync"
_DURATION_MIN = 15


class Command(BaseCommand):
    help = "Backfill existing Google Tasks into Calendar events with 8:00 anchor + conflict avoidance."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print plan without writing.")
        parser.add_argument("--keep-tasks", action="store_true", help="Don't delete original Google Tasks.")
        parser.add_argument("--max", type=int, default=0, help="Max tasks to process (0 = all).")

    def handle(self, *args, **options):
        creds = get_credentials()
        tasks_svc = build("tasks", "v1", credentials=creds)
        cal_svc = build("calendar", "v3", credentials=creds)

        tasklist_id = self._find_tasklist(tasks_svc)
        if not tasklist_id:
            self.stdout.write(f"No tasklist named '{_TASKLIST_TITLE}' found.")
            return

        self.stdout.write(f"Fetching tasks from '{_TASKLIST_TITLE}'...")
        all_tasks = self._list_all_tasks(tasks_svc, tasklist_id)
        self.stdout.write(f"  found {len(all_tasks)} tasks")

        if options["max"]:
            all_tasks = all_tasks[: options["max"]]

        # Group by day. Within a day, remember placements to avoid overlapping
        # subsequent tasks with earlier placements on the same day.
        by_day: dict[datetime, list[dict]] = {}
        for t in all_tasks:
            day = self._anchor_day(t)
            if day is None:
                continue
            by_day.setdefault(day, []).append(t)

        total_created = 0
        total_skipped = 0

        for day, tasks in sorted(by_day.items()):
            self.stdout.write(f"\n{day.date().isoformat()} — {len(tasks)} tasks")
            # Track slots already taken in this run (so subsequent tasks on the same
            # day account for earlier placements even before the calendar refreshes).
            local_busy: list[tuple[datetime, datetime]] = []

            for t in tasks:
                title = t.get("title", "").strip()
                if not title:
                    continue

                slot = self._find_slot_considering_local(cal_svc, day, local_busy)
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
                        notes=t.get("notes", ""),
                    )
                    if ev_id:
                        total_created += 1
                        self.stdout.write(f"  [OK]  {slot.strftime('%H:%M')} → {title}")
                        if not options["keep_tasks"]:
                            try:
                                tasks_svc.tasks().delete(
                                    tasklist=tasklist_id, task=t["id"]
                                ).execute()
                            except Exception:
                                logger.exception("Failed to delete task %s", t.get("id"))
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

    def _anchor_day(self, task: dict) -> datetime | None:
        raw = task.get("due") or task.get("updated")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            return None

    def _find_slot_considering_local(
        self, cal_svc, day: datetime, local_busy: list[tuple[datetime, datetime]]
    ) -> datetime | None:
        return find_free_slot(
            cal_svc, day, duration_min=_DURATION_MIN, extra_busy=local_busy
        )
