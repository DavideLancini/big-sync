"""Write contacts to Google Contacts (People API), using local cache for dedup.

Matching policy
---------------
A new contact is treated as the same person as an existing one only when one of
the following is true (in priority order):

  1. Email matches exactly (lowercase).
  2. Phone matches exactly (digits-only normalization).
  3. Name (or any nickname / alias) is near-exact: same string, OR differs by
     at most NEAR_EXACT_MAX_DIST characters and is at least NEAR_EXACT_MIN_LEN
     long. Levenshtein distance, lowercase + collapsed whitespace.

Anything looser creates a new contact. Aliases are roundtripped through Google
as `nicknames` on the People resource, so the alias graph is visible to anyone
opening Google Contacts and survives a cache wipe.
"""
import logging
import re

from googleapiclient.discovery import build

from common.google_auth import get_credentials
from common.models import Contact, WriteLog
from outputs.drive import append_contact_note

logger = logging.getLogger(__name__)

NEAR_EXACT_MAX_DIST = 2
NEAR_EXACT_MIN_LEN = 4  # shorter strings must match exactly


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def _build_service():
    return build("people", "v1", credentials=get_credentials())


def _edit_distance(a: str, b: str) -> int:
    """Classic Levenshtein. Short inputs only; we don't need optimization."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > NEAR_EXACT_MAX_DIST:
        return NEAR_EXACT_MAX_DIST + 1
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * lb
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[lb]


def _name_matches(query_norm: str, candidate: str) -> bool:
    """True if `query_norm` matches `candidate` exactly or within NEAR_EXACT_MAX_DIST."""
    cand = _norm_name(candidate)
    if not query_norm or not cand:
        return False
    if query_norm == cand:
        return True
    # Require a minimum length on the shorter of the two before allowing fuzz.
    if min(len(query_norm), len(cand)) < NEAR_EXACT_MIN_LEN:
        return False
    return _edit_distance(query_norm, cand) <= NEAR_EXACT_MAX_DIST


def _find_existing_local(name: str, phone: str, email: str) -> Contact | None:
    """Look up an existing local contact by email, phone, name or alias.

    Skips merged contacts (merged_into set). Returns the resolved canonical
    contact, never a tombstone.
    """
    norm_phone = _normalize_phone(phone)
    norm_email = (email or "").lower().strip()
    norm_name = _norm_name(name)

    base = Contact.objects.filter(merged_into__isnull=True)

    if norm_email:
        c = base.filter(emails__contains=[norm_email]).first()
        if c:
            return c.resolve()
    if norm_phone:
        c = base.filter(phones__contains=[norm_phone]).first()
        if c:
            return c.resolve()
    if norm_name:
        # Exact match shortcuts a Levenshtein scan.
        c = base.filter(name__iexact=norm_name).first()
        if c:
            return c.resolve()
        c = base.filter(aliases__contains=[norm_name]).first()
        if c:
            return c.resolve()
        # Near-exact: scan candidates whose name shares the first letter
        # of the query. Keeps the cost bounded even when the pool grows.
        initial = norm_name[0]
        candidates = base.filter(name__istartswith=initial).only(
            "id", "name", "aliases", "merged_into"
        )
        for cand in candidates:
            if _name_matches(norm_name, cand.name):
                return cand.resolve()
            for alias in cand.aliases or []:
                if _name_matches(norm_name, alias):
                    return cand.resolve()
    return None


# ---------------------------------------------------------------------------
# Google People API payloads
# ---------------------------------------------------------------------------

def _nicknames_payload(aliases: list[str]) -> list[dict]:
    seen = set()
    out = []
    for a in aliases or []:
        a_norm = _norm_name(a)
        if not a_norm or a_norm in seen:
            continue
        seen.add(a_norm)
        out.append({"value": a, "type": "DEFAULT"})
    return out


def _build_body(data: dict, notes_url: str = "", aliases: list[str] | None = None) -> dict:
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
    if notes_url:
        body["biographies"] = [{"value": f"Note: {notes_url}", "contentType": "TEXT_PLAIN"}]
    if aliases:
        nicks = _nicknames_payload(aliases)
        if nicks:
            body["nicknames"] = nicks
    return body


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def _create_contact(service, data: dict) -> str | None:
    new_note = (data.get("notes") or "").strip()
    notes_url = ""

    if new_note:
        name = (data.get("name") or "unknown").strip()
        notes_url = append_contact_note(name, new_note, "")

    body = _build_body(data, notes_url)
    try:
        result = service.people().createContact(body=body).execute()
        resource_name = result.get("resourceName", "")
        logger.info("Created contact: %s", data.get("name"))

        norm_phone = _normalize_phone(data.get("phone") or "")
        norm_email = (data.get("email") or "").lower().strip()
        Contact.objects.create(
            resource_name=resource_name,
            name=(data.get("name") or "").strip(),
            phones=[norm_phone] if norm_phone else [],
            emails=[norm_email] if norm_email else [],
            company=(data.get("company") or "").strip(),
            role=(data.get("role") or "").strip(),
            notes=new_note,
            notes_url=notes_url,
        )
        WriteLog.objects.create(type=WriteLog.TYPE_CONTACT, title=data.get("name") or "", detail="created")
        return resource_name
    except Exception:
        logger.exception("Error creating contact: %s", data)
        return None


# ---------------------------------------------------------------------------
# Enrich
# ---------------------------------------------------------------------------

def _enrich_contact(service, local: Contact, data: dict) -> str | None:
    resource_name = local.resource_name
    if not resource_name:
        return None

    try:
        existing = service.people().get(
            resourceName=resource_name,
            personFields="names,nicknames,phoneNumbers,emailAddresses,organizations,biographies",
        ).execute()
    except Exception:
        logger.exception("Error fetching contact for enrichment: %s", resource_name)
        return resource_name

    update_mask_fields = []
    body = {"etag": existing.get("etag", "")}
    local_changed = False

    # Nickname: if the incoming name differs from the canonical name (or any
    # existing alias) but matched via near-exact, record it as a new nickname.
    incoming_name = _norm_name(data.get("name") or "")
    canonical_name = _norm_name(local.name)
    aliases_lc = {(_norm_name(a)) for a in (local.aliases or [])}
    if incoming_name and incoming_name != canonical_name and incoming_name not in aliases_lc:
        new_aliases = list(local.aliases or []) + [incoming_name]
        existing_nicks = existing.get("nicknames", []) or []
        body["nicknames"] = existing_nicks + [{"value": data.get("name", "").strip(), "type": "DEFAULT"}]
        update_mask_fields.append("nicknames")
        local.aliases = new_aliases
        local_changed = True

    # Phone
    new_phone = _normalize_phone(data.get("phone") or "")
    if new_phone and new_phone not in local.phones:
        body["phoneNumbers"] = existing.get("phoneNumbers", []) + [{"value": data["phone"], "type": "other"}]
        update_mask_fields.append("phoneNumbers")
        local.phones = local.phones + [new_phone]
        local_changed = True

    # Email
    new_email = (data.get("email") or "").lower().strip()
    if new_email and new_email not in local.emails:
        body["emailAddresses"] = existing.get("emailAddresses", []) + [{"value": data["email"], "type": "other"}]
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

    # Notes — always go to Drive
    new_note = (data.get("notes") or "").strip()
    if new_note and new_note not in local.notes:
        separator = "\n---\n" if local.notes else ""
        full_notes = local.notes + separator + new_note
        url = append_contact_note(local.name or data.get("name", "unknown"), full_notes, local.notes_url)
        drive_bio = f"Note: {url}"

        if url != local.notes_url or not local.notes_url:
            body["biographies"] = [{"value": drive_bio, "contentType": "TEXT_PLAIN"}]
            update_mask_fields.append("biographies")

        local.notes = full_notes
        local.notes_url = url
        local_changed = True
        logger.info("Appended note to Drive for contact: %s", data.get("name"))

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
        WriteLog.objects.create(type=WriteLog.TYPE_CONTACT, title=local.name, detail="enriched")
        logger.info("Enriched contact: %s (%s)", data.get("name"), update_mask_fields)
        return resource_name
    except Exception:
        logger.exception("Error enriching contact: %s", resource_name)
        return resource_name


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def upsert_contact(data: dict) -> str | None:
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()

    if not name and not phone and not email:
        return None

    local = _find_existing_local(name, phone, email)
    service = _build_service()

    if local:
        return _enrich_contact(service, local, data)
    return _create_contact(service, data)
