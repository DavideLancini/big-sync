"""Migrate existing contact notes from Google Contacts biography to Drive .md files."""
import logging

from django.core.management.base import BaseCommand
from googleapiclient.discovery import build

from common.google_auth import get_credentials
from common.models import Contact
from outputs.drive import append_contact_note

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Migrate all contact notes to Google Drive .md files and update biography links."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be done without writing anything",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        qs = Contact.objects.exclude(notes="").filter(notes_url="").order_by("name")
        total = qs.count()

        if total == 0:
            self.stdout.write("No contacts to migrate.")
            return

        self.stdout.write(f"Migrating notes for {total} contacts{' [DRY RUN]' if dry_run else ''}...")

        service = build("people", "v1", credentials=get_credentials())
        ok = 0
        errors = 0

        for contact in qs:
            name = contact.name or f"contact_{contact.pk}"
            self.stdout.write(f"  {name}...", ending="")
            self.stdout.flush()

            if dry_run:
                self.stdout.write(f" [{len(contact.notes)} chars → {name}.md]")
                continue

            try:
                url = append_contact_note(name, contact.notes, "")

                # Update Google Contacts biography to Drive link
                if contact.resource_name:
                    try:
                        existing = service.people().get(
                            resourceName=contact.resource_name,
                            personFields="biographies",
                        ).execute()
                        service.people().updateContact(
                            resourceName=contact.resource_name,
                            updatePersonFields="biographies",
                            body={
                                "etag": existing.get("etag", ""),
                                "biographies": [{"value": f"Note: {url}", "contentType": "TEXT_PLAIN"}],
                            },
                        ).execute()
                    except Exception as e:
                        self.stdout.write(f" [Google Contacts update failed: {e}]", ending="")

                contact.notes_url = url
                contact.save(update_fields=["notes_url"])
                self.stdout.write(f" done")
                ok += 1

            except Exception as e:
                self.stdout.write(f" ERROR: {e}")
                logger.exception("Failed to migrate notes for contact %s", name)
                errors += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {ok} migrated, {errors} errors."
        ))
