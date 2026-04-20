"""Fetch RSS feeds then analyze new articles — suitable for cron every 30 minutes."""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Fetch RSS feeds then analyze new articles (fetch + analyze)"

    def handle(self, *args, **options):
        self.stdout.write("=== Fetch ===")
        call_command("rss_fetch", stdout=self.stdout, stderr=self.stderr)
        self.stdout.write("\n=== Analisi ===")
        call_command("rss_analyze", stdout=self.stdout, stderr=self.stderr)
