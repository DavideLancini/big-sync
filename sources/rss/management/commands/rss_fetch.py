"""Fetch all active RSS feeds and save new articles to the database."""
import logging
from datetime import datetime, timezone

import feedparser
from django.core.management.base import BaseCommand
from django.utils import timezone as tz

from sources.rss.models import RssArticle, RssFeed

logger = logging.getLogger(__name__)

DEFAULT_FEEDS = [
    ("ANSA", "https://www.ansa.it/sito/notizie/topnews/topnews_rss.xml"),
    ("Il Post", "https://www.ilpost.it/feed/"),
    ("Corriere della Sera", "https://rss.corriere.it/rss/homepage.xml"),
    ("la Repubblica", "https://www.repubblica.it/rss/homepage/rss2.0.xml"),
    ("Il Fatto Quotidiano", "https://www.ilfattoquotidiano.it/feed/"),
    ("Wired Italia", "https://www.wired.it/feed/rss"),
]


def _seed_feeds():
    for name, url in DEFAULT_FEEDS:
        RssFeed.objects.get_or_create(url=url, defaults={"name": name})


def _parse_date(entry) -> datetime | None:
    ts = entry.get("published_parsed") or entry.get("updated_parsed")
    if ts:
        return datetime(*ts[:6], tzinfo=timezone.utc)
    return None


def _full_content(entry) -> str:
    content_list = entry.get("content", [])
    if content_list:
        return content_list[0].get("value", "")
    return ""


def _fetch_feed(feed: RssFeed, stdout) -> tuple[int, int]:
    """Returns (saved, skipped)."""
    parsed = feedparser.parse(feed.url)
    if parsed.bozo and not parsed.entries:
        stdout.write(f"  [errore parsing: {parsed.bozo_exception}]")
        return 0, 0

    saved = skipped = 0
    for entry in parsed.entries:
        guid = entry.get("id") or entry.get("link") or entry.get("title", "")
        if not guid:
            continue

        content = _full_content(entry)
        summary = entry.get("summary", "")

        _, created = RssArticle.objects.get_or_create(
            guid=guid,
            defaults={
                "feed": feed,
                "title": entry.get("title", "")[:1024],
                "url": entry.get("link", "")[:2048],
                "summary": summary,
                "content": content,
                "published_at": _parse_date(entry),
            },
        )
        if created:
            saved += 1
        else:
            skipped += 1

    feed.last_fetched = tz.now()
    feed.save(update_fields=["last_fetched"])
    return saved, skipped


class Command(BaseCommand):
    help = "Fetch RSS feeds and save new articles"

    def handle(self, *args, **options):
        _seed_feeds()
        feeds = RssFeed.objects.filter(active=True)
        self.stdout.write(f"Fetching {feeds.count()} feed(s)...")

        total_saved = total_skipped = 0
        for feed in feeds:
            self.stdout.write(f"  {feed.name}... ", ending="")
            self.stdout.flush()
            saved, skipped = _fetch_feed(feed, self.stdout)
            self.stdout.write(f"+{saved} nuovi, {skipped} già presenti")
            total_saved += saved
            total_skipped += skipped

        self.stdout.write(f"\nDone. {total_saved} nuovi articoli salvati.")
