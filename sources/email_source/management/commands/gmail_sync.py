"""Incremental Gmail sync: import new messages then AI-tag them. For cron every 30 min."""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Incremental Gmail sync: import + analyze (for cron)"

    def handle(self, *args, **options):
        self.stdout.write("=== Import ===")
        call_command("gmail_import", stdout=self.stdout, stderr=self.stderr)
        self.stdout.write("\n=== Analisi ===")
        call_command("gmail_analyze", stdout=self.stdout, stderr=self.stderr)
