"""Generate title + summary + extract entities for transcribed Plaud recordings."""
import logging

from django.core.management.base import BaseCommand

from sources.plaud.models import PlaudRecording
from workflows.gemini import summarize_transcription
from workflows.workflow_telegram import process_realtime_message

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Summarize transcribed Plaud recordings (title + markdown summary) and extract contacts/events/todos"

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int)
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--force", action="store_true",
                            help="Re-summarize even if summarized=True")

    def handle(self, *args, **opts):
        qs = PlaudRecording.objects.exclude(transcription="")
        if not opts["force"]:
            qs = qs.filter(summarized=False)
        if opts["id"]:
            qs = qs.filter(pk=opts["id"])
        qs = qs.order_by("-recorded_at", "-created_at")[: opts["limit"]]

        items = list(qs)
        self.stdout.write(f"Summarizing {len(items)} recordings...")

        for rec in items:
            self.stdout.write(f"[{rec.pk}] {rec.original_name[:60]}")
            try:
                title, summary = summarize_transcription(rec.transcription,
                                                          source="plaud", ref_id=rec.pk)
                rec.title = title
                rec.summary = summary
                rec.summarized = True
                rec.error = ""
                rec.save(update_fields=["title", "summary", "summarized", "error"])
                self.stdout.write(f"  → title: {title[:80]}")
                self.stdout.write(f"  → summary: {len(summary)} chars")

                ts = rec.recorded_at or rec.created_at
                new_msg = {
                    "time": ts.strftime("%H:%M"),
                    "date": ts.strftime("%Y-%m-%d"),
                    "sender": "Davide",
                    "text": rec.transcription,
                    "media_type": "voice",
                }
                counts = process_realtime_message("Plaud · Voice Notes", new_msg, [],
                                                   source="plaud")
                self.stdout.write(
                    f"  → extracted: contacts:{counts['contacts']} "
                    f"events:{counts['events']} todos:{counts['todos']}"
                )
            except Exception as e:
                rec.error = str(e)[:500]
                rec.save(update_fields=["error"])
                self.stderr.write(f"  → ERROR: {e}")
                logger.exception("Plaud summarize failed for pk=%s", rec.pk)
