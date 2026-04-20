"""Generate WAV audio briefing for a given date's RSS summaries."""
import sys
from datetime import date as date_type

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sources.rss.models import RssDailySummary
from workflows.tts import generate_daily_briefing


class Command(BaseCommand):
    help = "Generate WAV audio briefing from daily RSS summaries"

    def add_arguments(self, parser):
        parser.add_argument("--date", default=None, help="Date YYYY-MM-DD (default: today)")
        parser.add_argument("--force", action="store_true", help="Regenerate even if file exists")

    def handle(self, *args, **options):
        if options["date"]:
            try:
                target_date = date_type.fromisoformat(options["date"])
            except ValueError:
                raise CommandError(f"Data non valida: {options['date']}")
        else:
            from django.utils import timezone
            target_date = timezone.localdate()

        audio_dir = settings.MEDIA_ROOT / "rss_audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / f"{target_date}.wav"

        if audio_path.exists() and not options["force"]:
            self.stdout.write(f"File già esistente: {audio_path}")
            self.stdout.write("Usa --force per rigenerare.")
            return

        summaries = list(
            RssDailySummary.objects
            .filter(date=target_date, article_count__gt=0)
            .select_related("topic")
            .order_by("topic__order")
        )

        if not summaries:
            raise CommandError(f"Nessun riassunto trovato per {target_date}")

        self.stdout.write(f"Generazione audio per {target_date} ({len(summaries)} sezioni)...")

        date_label = target_date.strftime("%-d %B %Y")
        wav_data = generate_daily_briefing(date_label, [
            {"topic": s.topic.name, "text": s.text} for s in summaries
        ], stdout=self.stdout)

        audio_path.write_bytes(wav_data)
        size_kb = len(wav_data) // 1024
        self.stdout.write(f"Salvato: {audio_path} ({size_kb} KB)")
