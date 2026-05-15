"""Group similar events in the cache and ask Gemini to dedupe them.

For each day in the window, take all non-todo events on the primary
calendar, ask Gemini which (if any) describe the same activity, and keep
the richest one. Removes the rest from Google + soft-deletes them in the
local cache.

Run with --dry-run first; pass --apply to actually delete.
"""
import json
import logging
from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from common.models import CachedEvent
from outputs.calendar import delete_event
from workflows.gemini import ask_text

logger = logging.getLogger(__name__)


_GROUP_PROMPT = """Sei un assistente che identifica eventi duplicati in un calendario.
Ti do un elenco di eventi che cadono nello stesso giorno. Raggruppa quelli
che descrivono LA STESSA attività (anche se in lingue diverse, con titoli
più o meno descrittivi, o con orari leggermente diversi).

Eventi:
{events_json}

Rispondi SOLO con JSON:
{{"groups": [
  {{"keep_id": "<id da tenere>", "delete_ids": ["<id1>", "<id2>", ...],
    "reason": "<frase breve in italiano>"}}
]}}

Per ogni gruppo: scegli come keep_id quello con più dettagli (location,
descrizione, attendees), o se sono equivalenti il primo nell'ordine.
Se un evento è UNICO (non ha duplicati), NON includerlo nei groups.
Se non trovi duplicati restituisci {{"groups": []}}.
"""


def _event_summary(c: CachedEvent) -> dict:
    return {
        "id": c.google_id,
        "title": c.title,
        "time": c.start_at.strftime("%H:%M") if c.start_at else "",
        "location": c.location,
        "description": (c.description or "")[:200],
        "attendees": [a.get("email", "") for a in (c.attendees or [])][:5],
    }


class Command(BaseCommand):
    help = "Find and remove duplicate events in the local calendar cache (and on Google)"

    def add_arguments(self, parser):
        parser.add_argument("--past-days", type=int, default=30)
        parser.add_argument("--future-days", type=int, default=14)
        parser.add_argument("--calendar", default="primary")
        parser.add_argument("--apply", action="store_true",
                            help="Actually delete duplicates (default: dry run)")

    def handle(self, *args, **opts):
        now = timezone.now()
        start = now - timedelta(days=opts["past_days"])
        end = now + timedelta(days=opts["future_days"])

        events = list(
            CachedEvent.objects
            .filter(
                calendar_id=opts["calendar"],
                is_todo=False,
                deleted_at__isnull=True,
                start_at__gte=start,
                start_at__lte=end,
            )
            .order_by("start_at")
        )

        by_day = defaultdict(list)
        for e in events:
            if e.start_at:
                by_day[e.start_at.date()].append(e)

        self.stdout.write(
            f"Calendario: {opts['calendar']}  finestra: {start.date()} → {end.date()}\n"
            f"Eventi totali: {len(events)}, giorni con almeno 2 eventi: "
            f"{sum(1 for d in by_day.values() if len(d) >= 2)}"
        )

        kept = 0
        to_delete = []

        for day, items in sorted(by_day.items()):
            if len(items) < 2:
                continue

            payload = json.dumps([_event_summary(e) for e in items],
                                  ensure_ascii=False, indent=2)
            try:
                raw = ask_text(_GROUP_PROMPT.format(events_json=payload),
                                source="dedup_calendar", operation="dedup_day")
            except Exception:
                logger.exception("dedup_day failed for %s", day)
                continue

            decision = self._parse(raw)
            groups = decision.get("groups") or []
            if not groups:
                continue

            self.stdout.write(f"\n--- {day} ({len(items)} eventi) ---")
            id_to_event = {e.google_id: e for e in items}
            for g in groups:
                keep_id = g.get("keep_id")
                del_ids = g.get("delete_ids") or []
                reason = g.get("reason", "")
                keep_ev = id_to_event.get(keep_id)
                if not keep_ev or not del_ids:
                    continue
                self.stdout.write(f"  KEEP   [{keep_ev.start_at.strftime('%H:%M')}] {keep_ev.title}")
                for did in del_ids:
                    de = id_to_event.get(did)
                    if not de or de.google_id == keep_id:
                        continue
                    self.stdout.write(f"  DELETE [{de.start_at.strftime('%H:%M')}] {de.title}")
                    to_delete.append(de)
                self.stdout.write(f"  reason: {reason}")
                kept += 1

        self.stdout.write(self.style.NOTICE(
            f"\nGroups identified: {kept}  Events to delete: {len(to_delete)}"
        ))

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING(
                "(dry-run, niente eliminato. Riesegui con --apply per cancellare)"
            ))
            return

        deleted = 0
        for ev in to_delete:
            if delete_event(ev.google_id, ev.calendar_id):
                deleted += 1
        self.stdout.write(self.style.SUCCESS(f"Eliminati {deleted}/{len(to_delete)} eventi"))

    @staticmethod
    def _parse(raw: str) -> dict:
        raw = (raw or "").strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(raw[start:end])
                except json.JSONDecodeError:
                    pass
        return {}
