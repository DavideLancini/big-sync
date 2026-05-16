"""Move events that live on primary to their proper calendar (Work / Chiara).

Walks every CachedEvent on the primary calendar (past + future, no time
filter by default), batches them, asks Gemini for a routing decision, and
calls events().move() on Google for everything that should leave primary.

Conservative by default: only acts on confidence=high/medium and only when
the proposed route differs from primary. Run with --dry-run first.
"""
import logging
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.utils import timezone
from googleapiclient.discovery import build

from common.calendars import (
    CALENDAR_LABEL,
    PRIMARY_CALENDAR_ID,
    ROUTE_TO_CALENDAR,
)
from common.google_auth import get_credentials
from common.models import CachedEvent
from workflows.routing import classify_events_batch

logger = logging.getLogger(__name__)

BATCH_SIZE = 15


class Command(BaseCommand):
    help = "Reclassify primary-calendar events and move work/chiara items to their own calendar"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Actually move events (default: dry run)")
        parser.add_argument("--only", choices=["events", "todos"], default=None,
                            help="Process only events or only todos")
        parser.add_argument("--from", dest="frm", default=None,
                            help="ISO date — only process events with start_at >= this")
        parser.add_argument("--to", dest="to", default=None,
                            help="ISO date — only process events with start_at <= this")
        parser.add_argument("--limit", type=int, default=None,
                            help="Cap on rows to process (debug)")

    def handle(self, *args, **opts):
        qs = CachedEvent.objects.filter(
            calendar_id=PRIMARY_CALENDAR_ID,
            deleted_at__isnull=True,
        )
        if opts["only"] == "events":
            qs = qs.filter(is_todo=False)
        elif opts["only"] == "todos":
            qs = qs.filter(is_todo=True)
        if opts["frm"]:
            qs = qs.filter(start_at__gte=opts["frm"])
        if opts["to"]:
            qs = qs.filter(start_at__lte=opts["to"])

        rows = list(qs.order_by("-start_at"))
        if opts["limit"]:
            rows = rows[: opts["limit"]]

        self.stdout.write(f"Da analizzare: {len(rows)} righe su primary")

        decisions: dict[str, dict] = {}
        for i in range(0, len(rows), BATCH_SIZE):
            chunk = rows[i:i + BATCH_SIZE]
            payload = [
                {
                    "id": str(r.pk),
                    "title": r.title,
                    "description": r.description,
                    "location": r.location,
                    "attendees": [a.get("email", "") for a in (r.attendees or [])],
                    "is_todo": r.is_todo,
                }
                for r in chunk
            ]
            batch_decisions = classify_events_batch(payload)
            decisions.update(batch_decisions)
            self.stdout.write(f"  batch {i // BATCH_SIZE + 1}: {len(batch_decisions)}/{len(chunk)} classificati")

        # Collect move targets
        moves_by_dest = defaultdict(list)
        for r in rows:
            d = decisions.get(str(r.pk))
            if not d:
                continue
            route = d["route"]
            if route == "personal":
                continue
            if d["confidence"] not in ("high", "medium"):
                continue
            dest = ROUTE_TO_CALENDAR.get(route)
            if not dest or dest == PRIMARY_CALENDAR_ID:
                continue
            moves_by_dest[dest].append((r, d))

        total_moves = sum(len(v) for v in moves_by_dest.values())
        self.stdout.write(self.style.NOTICE(f"\nSpostamenti proposti: {total_moves}"))
        for dest, items in moves_by_dest.items():
            label = CALENDAR_LABEL.get(dest, dest)
            self.stdout.write(f"\n→ {label}  ({len(items)} eventi)")
            for r, d in items[:50]:
                when = r.start_at.strftime("%Y-%m-%d %H:%M") if r.start_at else "?"
                tag = "[todo]" if r.is_todo else "[event]"
                self.stdout.write(f"  {tag} {when} | {r.title[:80]}")
                self.stdout.write(f"    reason: {d['reason']}")
            if len(items) > 50:
                self.stdout.write(f"  ... e altri {len(items) - 50}")

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING(
                "\n(dry-run: nessuno spostamento applicato. Riesegui con --apply)"
            ))
            return

        svc = build("calendar", "v3", credentials=get_credentials())
        moved = 0
        failed = 0
        for dest, items in moves_by_dest.items():
            for r, d in items:
                try:
                    svc.events().move(
                        calendarId=PRIMARY_CALENDAR_ID,
                        eventId=r.google_id,
                        destination=dest,
                    ).execute()
                except Exception as e:
                    failed += 1
                    logger.warning("move failed for %s (%s): %s", r.google_id, r.title, e)
                    continue
                r.calendar_id = dest
                r.calendar_name = CALENDAR_LABEL.get(dest, dest)
                r.last_seen_at = timezone.now()
                r.save(update_fields=["calendar_id", "calendar_name",
                                       "last_seen_at", "synced_at"])
                moved += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nSpostati {moved}/{total_moves} eventi  (falliti: {failed})"
        ))
