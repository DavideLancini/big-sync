"""Push every local Contact.aliases entry to Google as a People `nicknames` field.

Run once after introducing alias support, or any time you bulk-edit aliases in
the local DB and want Google to reflect them. Idempotent: nicknames already
present on Google are skipped.

Default is --dry-run; pass --apply to actually write.
"""
import logging

from django.core.management.base import BaseCommand
from googleapiclient.discovery import build

from common.google_auth import get_credentials
from common.models import Contact

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Push local Contact.aliases to Google Contacts as nicknames"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Actually write to Google (default: dry run)")

    def handle(self, *args, **opts):
        svc = build("people", "v1", credentials=get_credentials())

        qs = (
            Contact.objects
            .filter(merged_into__isnull=True)
            .exclude(aliases=[])
            .exclude(resource_name="")
        )
        self.stdout.write(f"Contatti con alias locali: {qs.count()}")

        pushed = 0
        skipped = 0
        failed = 0
        for c in qs:
            try:
                existing = svc.people().get(
                    resourceName=c.resource_name,
                    personFields="nicknames",
                ).execute()
            except Exception:
                logger.exception("get failed for %s", c.resource_name)
                failed += 1
                continue

            google_nicks_lc = {
                (n.get("value") or "").lower().strip()
                for n in existing.get("nicknames", []) if n.get("value")
            }
            new_aliases = [a for a in (c.aliases or [])
                            if a and a.lower().strip() not in google_nicks_lc]
            if not new_aliases:
                skipped += 1
                continue

            merged_nicks = (existing.get("nicknames", []) or []) + [
                {"value": a, "type": "DEFAULT"} for a in new_aliases
            ]
            label = c.name or c.resource_name
            self.stdout.write(f"  + {label}: aggiungo {new_aliases}")

            if not opts["apply"]:
                continue

            try:
                svc.people().updateContact(
                    resourceName=c.resource_name,
                    updatePersonFields="nicknames",
                    body={"etag": existing.get("etag", ""), "nicknames": merged_nicks},
                ).execute()
                pushed += 1
            except Exception as e:
                logger.warning("updateContact failed for %s: %s", c.resource_name, e)
                failed += 1

        self.stdout.write(self.style.NOTICE(
            f"\nDa spingere: {qs.count() - skipped}  già a posto: {skipped}"
        ))
        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("(dry-run, niente scritto)"))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Scritti su Google: {pushed}  errori: {failed}"
            ))
