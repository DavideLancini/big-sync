"""Generate per-topic WAV audio briefings for a given date.

For each RssDailySummary of the date with article_count > 0:
  - if no RssDailyAudio exists, generate it
  - if RssDailyAudio exists but its summary_updated_at is older than
    the summary's updated_at (section was re-analyzed), regenerate it
  - else skip (already fresh)

Audio files live in media/rss_audio/{YYYY-MM-DD}/{topic_slug}.wav
"""
from datetime import date as date_type

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from sources.rss.models import RssDailyAudio, RssDailySummary
from workflows.tts import generate_section_wav


class Command(BaseCommand):
    help = "Generate per-topic WAV audio briefings for a date (only missing/stale)"

    def add_arguments(self, parser):
        parser.add_argument("--date", default=None, help="Date YYYY-MM-DD (default: today)")
        parser.add_argument("--force", action="store_true",
                            help="Regenerate all sections, even if fresh")

    def handle(self, *args, **options):
        if options["date"]:
            try:
                target_date = date_type.fromisoformat(options["date"])
            except ValueError:
                raise CommandError(f"Data non valida: {options['date']}")
        else:
            from django.utils import timezone
            target_date = timezone.localdate()

        summaries = list(
            RssDailySummary.objects
            .filter(date=target_date, article_count__gt=0)
            .select_related("topic")
            .order_by("topic__order")
        )

        if not summaries:
            self.stdout.write(f"Nessun riassunto per {target_date}, nulla da fare.")
            return

        existing = {a.topic_id: a for a in RssDailyAudio.objects.filter(date=target_date)}

        to_generate = []
        skipped = 0
        for s in summaries:
            existing_audio = existing.get(s.topic_id)
            if not options["force"] and existing_audio and existing_audio.summary_updated_at >= s.updated_at:
                skipped += 1
                continue
            to_generate.append(s)

        self.stdout.write(
            f"Data {target_date}: {len(summaries)} sezioni totali, "
            f"{len(to_generate)} da generare, {skipped} già aggiornate."
        )

        if not to_generate:
            self.stdout.write("Tutto aggiornato, nessuna nuova notizia da convertire.")
            return

        for s in to_generate:
            self.stdout.write(f"  {s.topic.name}...")
            wav = generate_section_wav(s.topic.name, s.text,
                                         source="rss", ref_id=s.topic.slug)

            audio_obj = existing.get(s.topic_id)
            if audio_obj is None:
                audio_obj = RssDailyAudio(topic=s.topic, date=target_date,
                                          summary_updated_at=s.updated_at)
            else:
                # Delete old file from disk before saving the new one.
                if audio_obj.file:
                    audio_obj.file.delete(save=False)
                audio_obj.summary_updated_at = s.updated_at

            filename = f"{s.topic.slug}.wav"
            audio_obj.file.save(filename, ContentFile(wav), save=False)
            audio_obj.save()

            size_kb = len(wav) // 1024
            self.stdout.write(f"    → {audio_obj.file.name} ({size_kb} KB)")

        self.stdout.write(f"Generate {len(to_generate)} sezioni in media/rss_audio/{target_date}/")
