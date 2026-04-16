"""Write contacts to Google Contacts (People API)."""
import logging
import re

from googleapiclient.discovery import build

from common.google_auth import get_credentials

logger = logging.getLogger(__name__)


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _build_service():
    return build("people", "v1", credentials=get_credentials())


def upsert_contact(data: dict) -> str | None:
    """
    Create or update a Google Contact.
    Returns the resource name (e.g. 'people/c12345') or None on error.
    data keys: name, phone, email, company, role, notes
    """
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()

    if not name and not phone and not email:
        return None

    service = _build_service()

    # Search for existing contact
    existing = _find_existing(service, name, phone, email)

    if existing:
        return _enrich_contact(service, existing, data)
    else:
        return _create_contact(service, data)


def _find_existing(service, name, phone, email) -> dict | None:
    """Search existing contacts by name, phone or email."""
    queries = [q for q in [name, phone, email] if q]
    for query in queries:
        try:
            result = service.people().searchContacts(
                query=query,
                readMask="names,phoneNumbers,emailAddresses,organizations,biographies",
            ).execute()
            results = result.get("results", [])
            if results:
                return results[0]["person"]
        except Exception:
            logger.exception("Error searching contact: %s", query)
    return None


def _create_contact(service, data: dict) -> str | None:
    body = _build_body(data)
    try:
        result = service.people().createContact(body=body).execute()
        logger.info("Created contact: %s", data.get("name"))
        return result.get("resourceName")
    except Exception:
        logger.exception("Error creating contact: %s", data)
        return None


def _enrich_contact(service, existing: dict, data: dict) -> str | None:
    """Add new fields to existing contact without overwriting existing ones."""
    resource_name = existing.get("resourceName")
    update_mask_fields = []
    body = {"etag": existing.get("etag", "")}

    # Add phone if not present
    existing_phones = {_normalize_phone(p.get("value", ""))
                       for p in existing.get("phoneNumbers", [])}
    new_phone = _normalize_phone(data.get("phone") or "")
    if new_phone and new_phone not in existing_phones:
        body["phoneNumbers"] = existing.get("phoneNumbers", []) + [
            {"value": data["phone"], "type": "other"}
        ]
        update_mask_fields.append("phoneNumbers")

    # Add email if not present
    existing_emails = {e.get("value", "").lower()
                       for e in existing.get("emailAddresses", [])}
    new_email = (data.get("email") or "").lower()
    if new_email and new_email not in existing_emails:
        body["emailAddresses"] = existing.get("emailAddresses", []) + [
            {"value": data["email"], "type": "other"}
        ]
        update_mask_fields.append("emailAddresses")

    # Add org/role if not present
    if data.get("company") and not existing.get("organizations"):
        body["organizations"] = [{"name": data["company"], "title": data.get("role") or ""}]
        update_mask_fields.append("organizations")

    if not update_mask_fields:
        logger.debug("Contact already up to date: %s", data.get("name"))
        return resource_name

    try:
        service.people().updateContact(
            resourceName=resource_name,
            updatePersonFields=",".join(update_mask_fields),
            body=body,
        ).execute()
        logger.info("Enriched contact: %s (%s)", data.get("name"), update_mask_fields)
        return resource_name
    except Exception:
        logger.exception("Error enriching contact: %s", resource_name)
        return resource_name


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
        body["biographies"] = [{"value": data["notes"], "contentType": "TEXT_PLAIN"}]
    return body
