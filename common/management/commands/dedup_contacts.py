"""Group local contacts that represent the same person and merge them.

Buckets candidates by lowercase first-name token, then asks Gemini for each
bucket: "which of these are duplicates of which canonical contact?". For
each merge group:
  - canonical contact accumulates phones/emails/aliases/notes from the others
  - the merged-away contacts get merged_into=canonical and stay as tombstones
    so future writes are auto-routed to the canonical record.

Default is --dry-run. Pass --apply to write the merges. Does NOT touch
Google People API yet — only the local cache is updated, so any new alias
write hits the canonical contact in Google.
"""
import json
import logging
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from common.models import Contact
from workflows.gemini import ask_text

logger = logging.getLogger(__name__)


_BUCKET_PROMPT = """Sei un assistente che identifica contatti duplicati.
Ti do un elenco di contatti che condividono lo stesso primo nome o iniziale.
Decidi quali rappresentano LA STESSA persona, anche scritta diversamente
(diminutivi, soprannomi, varianti).

Contatti:
{contacts_json}

Rispondi SOLO con JSON:
{{"merges": [
  {{"canonical_id": <id da tenere>,
    "merge_ids": [<id1>, <id2>, ...],
    "aliases_to_add": ["<alias1>", "<alias2>", ...],
    "reason": "<frase breve>"}}
]}}

Regole:
- canonical = quello con più informazioni (telefono, email, organizzazione)
- merge_ids = SOLO contatti che sono CHIARAMENTE la stessa persona
- aliases_to_add = nomi/diminutivi distinti dei merged_ids da preservare sul canonical
- se non sei sicuro non includere quel contatto nei merge_ids
- se nessun gruppo di duplicati: {{"merges": []}}
"""


def _contact_brief(c: Contact) -> dict:
    return {
        "id": c.pk,
        "name": c.name,
        "aliases": c.aliases or [],
        "phones": c.phones or [],
        "emails": c.emails or [],
        "company": c.company or "",
        "role": c.role or "",
        "has_notes": bool(c.notes),
    }


class Command(BaseCommand):
    help = "Find and merge duplicate contacts in the local cache"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Actually write merges (default: dry run)")
        parser.add_argument("--min-bucket", type=int, default=2,
                            help="Skip buckets smaller than this size")
        parser.add_argument("--max-bucket", type=int, default=20,
                            help="Skip buckets larger than this (too noisy for the AI)")

    def handle(self, *args, **opts):
        contacts = list(
            Contact.objects.filter(merged_into__isnull=True).exclude(name="")
        )

        buckets: dict[str, list[Contact]] = defaultdict(list)
        for c in contacts:
            first = (c.name or "").strip().split()[:1]
            if not first:
                continue
            buckets[first[0].lower()].append(c)

        eligible = [
            (k, v) for k, v in sorted(buckets.items())
            if opts["min_bucket"] <= len(v) <= opts["max_bucket"]
        ]
        self.stdout.write(
            f"Contatti totali: {len(contacts)}  "
            f"buckets per primo nome: {len(buckets)}  "
            f"da analizzare: {len(eligible)}"
        )

        all_merges = []
        for first_name, items in eligible:
            payload = json.dumps([_contact_brief(c) for c in items],
                                  ensure_ascii=False, indent=2)
            try:
                raw = ask_text(_BUCKET_PROMPT.format(contacts_json=payload),
                                source="dedup_contacts", operation="dedup_bucket")
            except Exception:
                logger.exception("dedup_bucket failed for %s", first_name)
                continue

            decision = self._parse(raw)
            for m in decision.get("merges") or []:
                canonical_id = m.get("canonical_id")
                merge_ids = m.get("merge_ids") or []
                if not canonical_id or not merge_ids:
                    continue
                # Filter out the canonical itself if AI included it by mistake
                merge_ids = [int(i) for i in merge_ids if int(i) != int(canonical_id)]
                if not merge_ids:
                    continue
                all_merges.append({
                    "first_name": first_name,
                    "canonical_id": int(canonical_id),
                    "merge_ids": merge_ids,
                    "aliases_to_add": [a.lower() for a in (m.get("aliases_to_add") or []) if a],
                    "reason": m.get("reason", ""),
                })

        if not all_merges:
            self.stdout.write(self.style.SUCCESS("Nessun duplicato identificato."))
            return

        for m in all_merges:
            try:
                canonical = Contact.objects.get(pk=m["canonical_id"])
            except Contact.DoesNotExist:
                continue
            others = list(Contact.objects.filter(pk__in=m["merge_ids"]))
            self.stdout.write(f"\n[{m['first_name']}] CANONICAL: {canonical.name} (id={canonical.pk})")
            for o in others:
                self.stdout.write(f"  ← MERGE: {o.name} (id={o.pk})")
            if m["aliases_to_add"]:
                self.stdout.write(f"  + aliases: {m['aliases_to_add']}")
            self.stdout.write(f"  reason: {m['reason']}")

        self.stdout.write(self.style.NOTICE(
            f"\nGruppi di merge identificati: {len(all_merges)}  "
            f"contatti da fondere: {sum(len(m['merge_ids']) for m in all_merges)}"
        ))

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING(
                "(dry-run, nessun merge applicato. Riesegui con --apply)"
            ))
            return

        applied = 0
        for m in all_merges:
            with transaction.atomic():
                try:
                    canonical = Contact.objects.select_for_update().get(pk=m["canonical_id"])
                except Contact.DoesNotExist:
                    continue
                others = list(
                    Contact.objects.select_for_update().filter(pk__in=m["merge_ids"])
                )
                phones = list(canonical.phones or [])
                emails = list(canonical.emails or [])
                aliases = list(canonical.aliases or [])
                notes = canonical.notes or ""

                for o in others:
                    for p in (o.phones or []):
                        if p and p not in phones:
                            phones.append(p)
                    for e in (o.emails or []):
                        if e and e not in emails:
                            emails.append(e)
                    other_name = (o.name or "").lower().strip()
                    if other_name and other_name != (canonical.name or "").lower() and other_name not in aliases:
                        aliases.append(other_name)
                    for a in (o.aliases or []):
                        a = (a or "").lower().strip()
                        if a and a not in aliases and a != (canonical.name or "").lower():
                            aliases.append(a)
                    if o.notes and o.notes not in notes:
                        sep = "\n---\n" if notes else ""
                        notes = f"{notes}{sep}[merged from {o.name}]\n{o.notes}"
                    o.merged_into = canonical
                    o.save(update_fields=["merged_into", "updated_at"])

                for a in m["aliases_to_add"]:
                    if a and a not in aliases and a != (canonical.name or "").lower():
                        aliases.append(a)

                canonical.phones = phones
                canonical.emails = emails
                canonical.aliases = aliases
                if notes != canonical.notes:
                    canonical.notes = notes
                canonical.save()
                applied += 1

        self.stdout.write(self.style.SUCCESS(f"Applicati {applied} merge."))

    @staticmethod
    def _parse(raw: str) -> dict:
        raw = (raw or "").strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(raw[start:end])
                except json.JSONDecodeError:
                    pass
        return {}
