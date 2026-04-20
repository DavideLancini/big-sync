"""Classify unanalyzed RSS articles and merge them into daily topic summaries."""
from datetime import timedelta

from django.db import transaction
from django.core.management.base import BaseCommand
from django.utils import timezone

from sources.rss.models import TOPICS, RssArticle, RssDailySummary, RssTopic
from workflows.workflow_rss import classify_article, merge_into_summary

RETENTION_DAYS = 10


def _seed_topics():
    for slug, name, order in TOPICS:
        RssTopic.objects.get_or_create(slug=slug, defaults={"name": name, "order": order})


def _cleanup(stdout):
    cutoff = timezone.localdate() - timedelta(days=RETENTION_DAYS)
    del_art, _ = RssArticle.objects.filter(published_at__date__lt=cutoff).delete()
    del_sum, _ = RssDailySummary.objects.filter(date__lt=cutoff).delete()
    if del_art or del_sum:
        stdout.write(f"Cleanup: rimossi {del_art} articoli e {del_sum} riassunti > {RETENTION_DAYS}gg")


def _claim_article(article_id: int) -> bool:
    """Atomically mark one article as analyzed. Returns True if this process claimed it."""
    updated = RssArticle.objects.filter(id=article_id, analyzed=False).update(analyzed=True)
    return bool(updated)


def _merge_into_summary_locked(topic: RssTopic, article_date, title: str, source: str, text: str):
    """Merge article into daily summary under a row-level DB lock to avoid concurrent overwrites."""
    with transaction.atomic():
        summary_obj, _ = RssDailySummary.objects.select_for_update().get_or_create(
            topic=topic,
            date=article_date,
            defaults={"text": "", "article_count": 0},
        )
        new_text = merge_into_summary(topic.name, summary_obj.text, title, source, text)
        summary_obj.text = new_text
        summary_obj.article_count += 1
        summary_obj.save()


class Command(BaseCommand):
    help = "Analyze unprocessed RSS articles and update daily topic summaries"

    def handle(self, *args, **options):
        _seed_topics()
        _cleanup(self.stdout)

        article_ids = list(
            RssArticle.objects.filter(analyzed=False)
            .order_by("published_at", "created_at")
            .values_list("id", flat=True)
        )
        total = len(article_ids)

        if not total:
            self.stdout.write("Nessun articolo da analizzare.")
            return

        self.stdout.write(f"Analisi di {total} articoli...")

        ok = skipped = errors = 0
        for article_id in article_ids:
            # Claim the article atomically — skip if another process already took it
            if not _claim_article(article_id):
                skipped += 1
                continue

            try:
                article = RssArticle.objects.select_related("feed").get(id=article_id)
                text = (article.summary or article.content)[:800]
                topic_name = classify_article(article.title, text)
                topic = RssTopic.objects.get(name=topic_name)

                article_date = (
                    article.published_at.date()
                    if article.published_at
                    else timezone.localdate()
                )

                _merge_into_summary_locked(topic, article_date, article.title, article.feed.name, text)

                self.stdout.write(f"  [{topic_name}] {article.title[:70]}")
                ok += 1
            except Exception as e:
                # Release the claim so the article can be retried next run
                RssArticle.objects.filter(id=article_id).update(analyzed=False)
                self.stdout.write(f"  ERRORE: {article_id}: {e}")
                errors += 1

        msg = f"\nDone. {ok} analizzati"
        if skipped:
            msg += f", {skipped} già presi da altro processo"
        if errors:
            msg += f", {errors} errori"
        self.stdout.write(msg + ".")
