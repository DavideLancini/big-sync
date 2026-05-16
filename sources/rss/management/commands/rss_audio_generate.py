"""Generate per-topic WAV audio briefings for a given date.

For each RssDailySummary of the date with article_count > 0:
  - if no RssDailyAudio exists, generate it
  - if RssDailyAudio exists but its summary_updated_at is older than the
    summary's updated_at (section was re-analyzed), regenerate it
  - else skip (already fresh)

Audio files live in media/rss_audio/{YYYY-MM-DD}/{topic_slug}.wav.

When --job-id is passed, progress is mirrored to the corresponding
RssAudioJob row so the dashboard can poll for status.
"""
from datetime import date as date_type

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from sources.rss.models import RssAudioJob, RssDailyAudio, RssDailySummary
from workflows.tts import generate_section_wav


class Command(BaseCommand):
    help = "Generate per-topic WAV audio briefings for a date (only missing/stale)"

    def add_arguments(self, parser):
        parser.add_argument("--date", default=None, help="Date YYYY-MM-DD (default: today)")
        parser.add_argument("--force", action="store_true",
                            help="Regenerate all sections, even if fresh")
        parser.add_argument("--job-id", type=int, default=None,
                            help="If given, update the RssAudioJob row as progress is made")

    def handle(self, *args, **options):
        if options["date"]:
            try:
                target_date = date_type.fromisoformat(options["date"])
            except ValueError:
                raise CommandError(f"Data non valida: {options['date']}")
        else:
            target_date = timezone.localdate()

        job = None
        if options["job_id"]:
            try:
                job = RssAudioJob.objects.get(pk=options["job_id"])
            except RssAudioJob.DoesNotExist:
                self.stderr.write(f"Job id={options['job_id']} non trovato, proseguo senza tracking")

        try:
            self._run(target_date, options["force"], job)
        except Exception as e:
            if job:
                job.status = RssAudioJob.STATUS_ERROR
                job.error = str(e)[:1000]
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "error", "finished_at", "updated_at"])
            raise

    def _run(self, target_date, force: bool, job):
        summaries = list(
            RssDailySummary.objects
            .filter(date=target_date, article_count__gt=0)
            .select_related("topic")
            .order_by("topic__order")
        )

        if not summaries:
            self.stdout.write(f"Nessun riassunto per {target_date}, nulla da fare.")
            if job:
                job.status = RssAudioJob.STATUS_DONE
                job.total_sections = 0
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "total_sections", "finished_at", "updated_at"])
            return

        existing = {a.topic_id: a for a in RssDailyAudio.objects.filter(date=target_date)}

        to_generate = []
        skipped = 0
        for s in summaries:
            existing_audio = existing.get(s.topic_id)
            if not force and existing_audio and existing_audio.summary_updated_at >= s.updated_at:
                skipped += 1
                continue
            to_generate.append(s)

        self.stdout.write(
            f"Data {target_date}: {len(summaries)} sezioni totali, "
            f"{len(to_generate)} da generare, {skipped} già aggiornate."
        )

        if job:
            job.total_sections = len(to_generate)
            job.completed_sections = 0
            job.current_topic_slug = ""
            job.save(update_fields=["total_sections", "completed_sections",
                                     "current_topic_slug", "updated_at"])

        if not to_generate:
            self.stdout.write("Tutto aggiornato, nessuna nuova notizia da convertire.")
            if job:
                job.status = RssAudioJob.STATUS_DONE
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "finished_at", "updated_at"])
            return

        for s in to_generate:
            if job:
                job.current_topic_slug = s.topic.slug
                job.save(update_fields=["current_topic_slug", "updated_at"])

            self.stdout.write(f"  {s.topic.name}...")
            wav = generate_section_wav(s.topic.name, s.text,
                                         source="rss", ref_id=s.topic.slug)

            audio_obj = existing.get(s.topic_id)
            if audio_obj is None:
                audio_obj = RssDailyAudio(topic=s.topic, date=target_date,
                                          summary_updated_at=s.updated_at)
            else:
                if audio_obj.file:
                    audio_obj.file.delete(save=False)
                audio_obj.summary_updated_at = s.updated_at

            filename = f"{s.topic.slug}.wav"
            audio_obj.file.save(filename, ContentFile(wav), save=False)
            audio_obj.save()

            size_kb = len(wav) // 1024
            self.stdout.write(f"    → {audio_obj.file.name} ({size_kb} KB)")

            if job:
                job.completed_sections = (job.completed_sections or 0) + 1
                job.save(update_fields=["completed_sections", "updated_at"])

        self.stdout.write(f"Generate {len(to_generate)} sezioni in media/rss_audio/{target_date}/")
        if job:
            job.status = RssAudioJob.STATUS_DONE
            job.current_topic_slug = ""
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "current_topic_slug",
                                     "finished_at", "updated_at"])
