"""Re-evaluate historical todos with the AI quality filter and remove noise.

Walks every is_todo CachedEvent in the window, asks workflows.dedup.is_useful_todo,
and removes the ones flagged as noise. Default is --dry-run.
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from common.models import CachedEvent
from outputs.calendar import delete_event
from workflows.dedup import is_useful_todo

logger = logging.getLogger(__name__)

# strip the [todo]  prefix added by outputs/todos.py before sending to the filter
_TODO_PREFIX = "[todo] "


class Command(BaseCommand):
    help = "Find and remove low-quality (noisy) todos via the Gemini quality filter"

    def add_arguments(self, parser):
        parser.add_argument("--past-days", type=int, default=30)
        parser.add_argument("--future-days", type=int, default=30)
        parser.add_argument("--calendar", default="primary")
        parser.add_argument("--apply", action="store_true",
                            help="Actually delete noise (default: dry run)")
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **opts):
        now = timezone.now()
        start = now - timedelta(days=opts["past_days"])
        end = now + timedelta(days=opts["future_days"])

        todos = list(
            CachedEvent.objects
            .filter(
                calendar_id=opts["calendar"],
                is_todo=True,
                deleted_at__isnull=True,
                start_at__gte=start,
                start_at__lte=end,
            )
            .order_by("start_at")[: opts["limit"]]
        )

        self.stdout.write(f"Esamino {len(todos)} todo dal {start.date()} al {end.date()}")

        to_delete = []
        for t in todos:
            raw_title = (t.title or "").removeprefix(_TODO_PREFIX).strip()
            if not raw_title:
                continue
            try:
                keep, reason = is_useful_todo(raw_title, context_text=t.description or "")
            except Exception:
                logger.exception("is_useful_todo failed for %s", t.google_id)
                continue
            if keep:
                continue
            self.stdout.write(f"  NOISE  [{t.start_at.strftime('%d/%m %H:%M')}] {t.title}")
            self.stdout.write(f"         reason: {reason}")
            to_delete.append(t)

        self.stdout.write(self.style.NOTICE(
            f"\nTodo da rimuovere: {len(to_delete)} su {len(todos)} esaminati"
        ))

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING(
                "(dry-run, niente eliminato. Riesegui con --apply per cancellare)"
            ))
            return

        deleted = 0
        for t in to_delete:
            if delete_event(t.google_id, t.calendar_id):
                deleted += 1
        self.stdout.write(self.style.SUCCESS(f"Eliminati {deleted}/{len(to_delete)} todo rumorosi"))
