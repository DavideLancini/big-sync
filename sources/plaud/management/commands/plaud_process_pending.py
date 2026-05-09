"""Transcribe + analyze pending Plaud recordings via Gemini."""
import logging

from django.core.management.base import BaseCommand

from sources.plaud.models import PlaudRecording
from workflows.gemini import transcribe_audio
from workflows.workflow_telegram import process_realtime_message

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Transcribe and analyze pending Plaud recordings"

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, help="Process a specific recording id")
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **opts):
        qs = PlaudRecording.objects.filter(processed=False).order_by("created_at")
        if opts["id"]:
            qs = qs.filter(pk=opts["id"])
        qs = qs[: opts["limit"]]

        n = qs.count() if hasattr(qs, "count") else len(list(qs))
        self.stdout.write(f"Processing {n} pending recordings...")

        for rec in qs:
            self.stdout.write(f"[{rec.pk}] {rec.original_name or rec.file.name}")
            try:
                text = transcribe_audio(rec.file.path)
                rec.transcription = text or ""
                if not text:
                    rec.error = "empty transcription"
                    rec.save(update_fields=["transcription", "error"])
                    self.stderr.write("  → empty transcription, skipping")
                    continue
                self.stdout.write(f"  → transcribed ({len(text)} chars)")

                ts = rec.recorded_at or rec.created_at
                new_msg = {
                    "time": ts.strftime("%H:%M"),
                    "date": ts.strftime("%Y-%m-%d"),
                    "sender": "Davide",
                    "text": text,
                    "media_type": "voice",
                }
                counts = process_realtime_message("Plaud · Voice Notes", new_msg, [])
                self.stdout.write(
                    f"  → analyzed: contacts:{counts['contacts']} "
                    f"events:{counts['events']} todos:{counts['todos']}"
                )
                rec.processed = True
                rec.error = ""
                rec.save(update_fields=["transcription", "processed", "error"])
            except Exception as e:
                rec.error = str(e)[:500]
                rec.save(update_fields=["error"])
                self.stderr.write(f"  → ERROR: {e}")
                logger.exception("Plaud processing failed for pk=%s", rec.pk)
