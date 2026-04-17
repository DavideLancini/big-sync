"""Write contacts to Google Contacts (People API), using local cache for dedup."""
import logging
import re

from googleapiclient.discovery import build

from common.google_auth import get_credentials
from common.models import Contact

logger = logging.getLogger(__name__)


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _build_service():
    return build("people", "v1", credentials=get_credentials())


def _find_existing_local(name: str, phone: str, email: str) -> Contact | None:
    """Look up an existing contact in the local cache."""
    norm_phone = _normalize_phone(phone)
    norm_email = (email or "").lower().strip()
    norm_name = (name or "").strip()

    # Try email first (most reliable)
    if norm_email:
        c = Contact.objects.filter(emails__contains=[norm_email]).first()
        if c:
            return c

    # Try phone
    if norm_phone:
        c = Contact.objects.filter(phones__contains=[norm_phone]).first()
        if c:
            return c

    # Try name (case-insensitive exact match)
    if norm_name:
        c = Contact.objects.filter(name__iexact=norm_name).first()
        if c:
            return c

    return None


def _build_body(data: dict) -> dict:
    body = {}
    if data.get("name"):
        parts = data["name"].strip().split(" ", 1)
        body["names"] = [{"givenName": parts[0], "familyName": parts[1] if len(parts) > 1 else ""}]
    if data.get("phone"):
        body["phoneNumbers"] = [{"value": data["phone"], "type": "other"}]
    if data.get("email"):
        body["emailAddresses"] = [{"value": data["email"], "type": "other"}]
    if data.get("company"):
        body["organizations"] = [{"name": data["company"], "title": data.get("role") or ""}]
    if data.get("notes"):
        notes = data["notes"]
        if len(notes) > 2048:
            from outputs.drive import upsert_notes_file
            url = upsert_notes_file((data.get("name") or "unknown"), notes)
            body["biographies"] = [{"value": f"Note: {url}", "contentType": "TEXT_PLAIN"}]
        else:
            body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]
    return body


def _create_contact(service, data: dict) -> str | None:
    body = _build_body(data)
    try:
        result = service.people().createContact(body=body).execute()
        resource_name = result.get("resourceName", "")
        logger.info("Created contact: %s", data.get("name"))

        # Save to local cache
        norm_phone = _normalize_phone(data.get("phone") or "")
        norm_email = (data.get("email") or "").lower().strip()
        notes = (data.get("notes") or "").strip()
        notes_url = ""
        if len(notes) > 2048:
            from outputs.drive import upsert_notes_file
            notes_url = upsert_notes_file((data.get("name") or "unknown"), notes)
        Contact.objects.create(
            resource_name=resource_name,
            name=(data.get("name") or "").strip(),
            phones=[norm_phone] if norm_phone else [],
            emails=[norm_email] if norm_email else [],
            company=(data.get("company") or "").strip(),
            role=(data.get("role") or "").strip(),
            notes=notes,
            notes_url=notes_url,
        )
        return resource_name
    except Exception:
        logger.exception("Error creating contact: %s", data)
        return None


def _enrich_contact(service, local: Contact, data: dict) -> str | None:
    """Add new fields to existing contact without overwriting, both on Google and locally."""
    resource_name = local.resource_name
    if not resource_name:
        return None

    # Fetch current Google state for etag
    try:
        existing = service.people().get(
            resourceName=resource_name,
            personFields="names,phoneNumbers,emailAddresses,organizations,biographies",
        ).execute()
    except Exception:
        logger.exception("Error fetching contact for enrichment: %s", resource_name)
        return resource_name

    update_mask_fields = []
    body = {"etag": existing.get("etag", "")}
    local_changed = False

    # Phone
    new_phone = _normalize_phone(data.get("phone") or "")
    if new_phone and new_phone not in local.phones:
        body["phoneNumbers"] = existing.get("phoneNumbers", []) + [
            {"value": data["phone"], "type": "other"}
        ]
        update_mask_fields.append("phoneNumbers")
        local.phones = local.phones + [new_phone]
        local_changed = True

    # Email
    new_email = (data.get("email") or "").lower().strip()
    if new_email and new_email not in local.emails:
        body["emailAddresses"] = existing.get("emailAddresses", []) + [
            {"value": data["email"], "type": "other"}
        ]
        update_mask_fields.append("emailAddresses")
        local.emails = local.emails + [new_email]
        local_changed = True

    # Company/role
    if data.get("company") and not existing.get("organizations"):
        body["organizations"] = [{"name": data["company"], "title": data.get("role") or ""}]
        update_mask_fields.append("organizations")
        local.company = data["company"]
        local.role = data.get("role") or ""
        local_changed = True

    # Notes — append to existing biography, offload to Drive if too long
    NOTES_MAX = 2048
    new_note = (data.get("notes") or "").strip()
    if new_note:
        bios = existing.get("biographies", [])
        current_notes = bios[0].get("value", "") if bios else ""
        separator = "\n---\n" if current_notes else ""
        appended = current_notes + separator + new_note

        if new_note not in current_notes:
            if len(appended) > NOTES_MAX or local.notes_url:
                # Offload to Google Drive
                from outputs.drive import upsert_notes_file
                full_notes = local.notes + separator + new_note if local.notes else new_note
                url = upsert_notes_file(local.name or data.get("name", "unknown"), full_notes)
                drive_bio = f"Note: {url}"
                body["biographies"] = [{"value": drive_bio, "contentType": "TEXT_PLAIN"}]
                update_mask_fields.append("biographies")
                local.notes = full_notes
                local.notes_url = url
                local_changed = True
                logger.info("Offloaded notes to Drive for contact: %s", data.get("name"))
            else:
                body["biographies"] = [{"value": appended, "contentType": "TEXT_PLAIN"}]
                update_mask_fields.append("biographies")
                local.notes = appended
                local_changed = True

    if not update_mask_fields:
        logger.debug("Contact already up to date: %s", data.get("name"))
        return resource_name

    try:
        service.people().updateContact(
            resourceName=resource_name,
            updatePersonFields=",".join(update_mask_fields),
            body=body,
        ).execute()
        if local_changed:
            local.save()
        logger.info("Enriched contact: %s (%s)", data.get("name"), update_mask_fields)
        return resource_name
    except Exception:
        logger.exception("Error enriching contact: %s", resource_name)
        return resource_name


def upsert_contact(data: dict) -> str | None:
    """
    Create or enrich a Google Contact, using local cache for dedup.
    Returns resource name or None on error.
    data keys: name, phone, email, company, role, notes
    """
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()

    if not name and not phone and not email:
        return None

    local = _find_existing_local(name, phone, email)

    if local:
        service = _build_service()
        return _enrich_contact(service, local, data)
    else:
        service = _build_service()
        return _create_contact(service, data)
