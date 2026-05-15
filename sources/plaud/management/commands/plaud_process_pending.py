"""Transcribe pending Plaud recordings via Gemini File API."""
import logging

from django.core.management.base import BaseCommand

from sources.plaud.models import PlaudRecording
from workflows.gemini import transcribe_audio

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Transcribe pending Plaud recordings"

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, help="Process a specific recording id")
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--force", action="store_true",
                            help="Re-transcribe even if processed=True")

    def handle(self, *args, **opts):
        qs = PlaudRecording.objects.all().order_by("created_at")
        if not opts["force"]:
            qs = qs.filter(processed=False)
        if opts["id"]:
            qs = qs.filter(pk=opts["id"])
        qs = qs[: opts["limit"]]

        items = list(qs)
        self.stdout.write(f"Transcribing {len(items)} recordings...")

        total_in = total_out = total_tot = 0
        for rec in items:
            self.stdout.write(f"[{rec.pk}] {rec.original_name or rec.file.name}")
            try:
                text, usage = transcribe_audio(rec.file.path, return_usage=True,
                                                source="plaud", ref_id=rec.pk)
                total_in += usage["prompt"]
                total_out += usage["output"]
                total_tot += usage["total"]
                rec.transcription = text or ""
                if not text:
                    rec.error = "empty transcription"
                    rec.save(update_fields=["transcription", "error"])
                    self.stderr.write("  → empty transcription")
                    continue
                rec.processed = True
                rec.error = ""
                rec.save(update_fields=["transcription", "processed", "error"])
                self.stdout.write(
                    f"  → transcribed ({len(text)} chars · tokens in:{usage['prompt']} "
                    f"out:{usage['output']} tot:{usage['total']})"
                )
            except Exception as e:
                rec.error = str(e)[:500]
                rec.save(update_fields=["error"])
                self.stderr.write(f"  → ERROR: {e}")
                logger.exception("Plaud transcription failed for pk=%s", rec.pk)

        self.stdout.write(
            f"--- totale tokens: in={total_in} out={total_out} tot={total_tot} ---"
        )
