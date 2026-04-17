"""Sync all Google Contacts into the local Contact cache."""
import logging
import re

from django.core.management.base import BaseCommand
from googleapiclient.discovery import build

from common.google_auth import get_credentials
from common.models import Contact, ContactsSyncLog

logger = logging.getLogger(__name__)

READ_MASK = "names,phoneNumbers,emailAddresses,organizations,biographies"


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _sync():
    service = build("people", "v1", credentials=get_credentials())
    page_token = None
    total = 0
    upserted = 0

    while True:
        kwargs = {
            "resourceName": "people/me",
            "pageSize": 1000,
            "personFields": READ_MASK,
        }
        if page_token:
            kwargs["pageToken"] = page_token

        result = service.people().connections().list(**kwargs).execute()
        connections = result.get("connections", [])

        for person in connections:
            total += 1
            resource_name = person.get("resourceName", "")

            names = person.get("names", [])
            name = names[0].get("displayName", "") if names else ""

            phones = [
                _normalize_phone(p.get("value", ""))
                for p in person.get("phoneNumbers", [])
                if p.get("value")
            ]
            phones = [p for p in phones if p]

            emails = [
                e.get("value", "").lower()
                for e in person.get("emailAddresses", [])
                if e.get("value")
            ]

            orgs = person.get("organizations", [])
            company = orgs[0].get("name", "") if orgs else ""
            role = orgs[0].get("title", "") if orgs else ""

            bios = person.get("biographies", [])
            bio = bios[0].get("value", "") if bios else ""

            # Extract Drive URL if biography is a link we manage
            notes_url = ""
            if bio.startswith("Note: https://drive.google.com/"):
                notes_url = bio[len("Note: "):]

            # Build defaults — preserve local notes/notes_url if Drive is the source of truth
            defaults = {
                "name": name,
                "phones": phones,
                "emails": emails,
                "company": company,
                "role": role,
            }

            existing = Contact.objects.filter(resource_name=resource_name).first()
            if existing and existing.notes_url:
                # Notes live on Drive — don't overwrite local full-text notes
                if notes_url and notes_url != existing.notes_url:
                    defaults["notes_url"] = notes_url
            else:
                # No Drive file — sync notes from Google biography
                defaults["notes"] = bio
                if notes_url:
                    defaults["notes_url"] = notes_url

            Contact.objects.update_or_create(
                resource_name=resource_name,
                defaults=defaults,
            )
            upserted += 1

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    ContactsSyncLog.objects.create(contacts_count=total)
    return total


class Command(BaseCommand):
    help = "Sync all Google Contacts into the local cache (common_contact table)."

    def handle(self, *args, **options):
        self.stdout.write("Syncing contacts from Google...")
        try:
            total = _sync()
            self.stdout.write(self.style.SUCCESS(f"Done. {total} contacts synced."))
        except Exception:
            logger.exception("Error syncing contacts")
            self.stderr.write("Sync failed — check logs.")
