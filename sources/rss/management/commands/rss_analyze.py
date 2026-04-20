"""Classify unanalyzed RSS articles and merge them into daily topic summaries."""
from datetime import timedelta

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
    del_art, _ = RssArticle.objects.filter(
        published_at__date__lt=cutoff
    ).delete()
    del_sum, _ = RssDailySummary.objects.filter(date__lt=cutoff).delete()
    if del_art or del_sum:
        stdout.write(f"Cleanup: rimossi {del_art} articoli e {del_sum} riassunti > {RETENTION_DAYS}gg")


class Command(BaseCommand):
    help = "Analyze unprocessed RSS articles and update daily topic summaries"

    def handle(self, *args, **options):
        _seed_topics()
        _cleanup(self.stdout)

        articles = list(
            RssArticle.objects.filter(analyzed=False)
            .select_related("feed")
            .order_by("published_at", "created_at")
        )
        total = len(articles)

        if not total:
            self.stdout.write("Nessun articolo da analizzare.")
            return

        self.stdout.write(f"Analisi di {total} articoli...")

        ok = errors = 0
        for article in articles:
            try:
                text = (article.summary or article.content)[:800]
                topic_name = classify_article(article.title, text)
                topic = RssTopic.objects.get(name=topic_name)

                article_date = (
                    article.published_at.date()
                    if article.published_at
                    else timezone.localdate()
                )

                summary_obj, _ = RssDailySummary.objects.get_or_create(
                    topic=topic,
                    date=article_date,
                    defaults={"text": "", "article_count": 0},
                )

                new_text = merge_into_summary(
                    topic_name,
                    summary_obj.text,
                    article.title,
                    article.feed.name,
                    text,
                )
                summary_obj.text = new_text
                summary_obj.article_count += 1
                summary_obj.save()

                article.analyzed = True
                article.save(update_fields=["analyzed"])

                self.stdout.write(f"  [{topic_name}] {article.title[:70]}")
                ok += 1
            except Exception as e:
                self.stdout.write(f"  ERRORE: {article.title[:60]}: {e}")
                errors += 1

        self.stdout.write(f"\nDone. {ok} analizzati, {errors} errori.")
