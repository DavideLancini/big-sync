"""AI-assisted deduplication helpers.

These wrap Gemini calls to answer three questions consistently across
the application:

  - is_same_event(new, candidates)        : do we already have this event?
  - is_useful_todo(text, context)         : is this todo worth tracking?
  - resolve_contact_alias(name, candidates): is "Ghira" the same person
                                             as "Ghiraffa Rossi"?

All helpers are conservative: when in doubt they return "no/unsure" so
duplicates are favored over wrong merges.
"""
import json
import logging

from workflows.gemini import ask_text

logger = logging.getLogger(__name__)


def _safe_json(raw: str) -> dict:
    raw = raw.strip()
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


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def is_same_event(new_event: dict, candidates: list[dict]) -> str | None:
    """Decide whether `new_event` is already represented by one of the candidates.

    new_event keys: title, date, time, location, description, attendees
    candidates: list of dicts with the same shape PLUS an "id" field

    Returns the matching candidate id, or None if no match.
    """
    if not candidates:
        return None

    cand_brief = [
        {
            "id": c.get("id"),
            "title": c.get("title", ""),
            "date": c.get("date", ""),
            "time": c.get("time", ""),
            "location": c.get("location", ""),
        }
        for c in candidates
    ]

    prompt = f"""Sei un assistente che decide se un nuovo evento è un duplicato di uno esistente.

Nuovo evento:
{json.dumps(new_event, ensure_ascii=False, indent=2)}

Eventi esistenti già nel calendario (potenziali duplicati):
{json.dumps(cand_brief, ensure_ascii=False, indent=2)}

Considera duplicati gli eventi che descrivono LA STESSA cosa, anche se:
- titoli in lingue diverse (es. "Compleanno di Gray" == "Gray's Birthday")
- titoli più o meno descrittivi (es. "Cena al Mr Donkey" == "Cena con Arina al Mr Donkey")
- orari leggermente diversi (es. 19:00 vs 19:30)
- uno è generico ("Call") e l'altro è specifico ("Call con Francesco")

NON considerare duplicati eventi che capitano per coincidenza nello stesso giorno
ma sono attività diverse (es. "Riunione di lavoro" e "Cena con amici").

Rispondi SOLO con JSON in questo formato esatto:
{{"duplicate_of_id": "<id dell'evento esistente>" oppure null,
  "confidence": "high"|"medium"|"low",
  "reason": "<una frase breve in italiano>"}}"""

    try:
        raw = ask_text(prompt, source="dedup", operation="is_same_event")
        data = _safe_json(raw)
        match_id = data.get("duplicate_of_id")
        confidence = (data.get("confidence") or "").lower()
        if match_id and confidence in ("high", "medium"):
            return str(match_id)
    except Exception:
        logger.exception("is_same_event failed")
    return None


# ---------------------------------------------------------------------------
# Todos
# ---------------------------------------------------------------------------

def is_useful_todo(title: str, context_chat: str = "", context_text: str = "") -> tuple[bool, str]:
    """Filter out low-quality todos before they get created.

    Returns (keep, reason). When in doubt, keep=True.
    """
    if not (title or "").strip():
        return False, "empty title"

    prompt = f"""Sei un filtro di qualità per todo personali. Rispondi se il todo merita
di essere salvato nel calendario o se è rumore.

Sono RUMORE (rispondi keep=false):
- istruzioni tecniche del sistema (es. "Copy and paste plAUD transcriptions")
- azioni triviali e quotidiane (es. "Bere acqua", "Aprire il browser")
- frasi che descrivono cosa è successo, non cosa fare (es. "Abbiamo parlato di X")
- todo vaghissimi senza azione concreta (es. "Pensare", "Riflettere")
- todo che ripetono parole della trascrizione senza essere veri todo

Sono UTILI (rispondi keep=true):
- azioni concrete da fare con un destinatario o oggetto chiaro
- promesse fatte ("Mando la mail a X")
- impegni con scadenze
- chiamate, riunioni, follow-up specifici

Todo proposto: "{title}"
Contesto chat/registrazione: "{context_chat}"
Estratto del messaggio: "{context_text[:300]}"

Rispondi SOLO con JSON:
{{"keep": true|false, "reason": "<una frase in italiano>"}}"""

    try:
        raw = ask_text(prompt, source="dedup", operation="is_useful_todo")
        data = _safe_json(raw)
        keep = data.get("keep")
        if isinstance(keep, bool):
            return keep, data.get("reason", "")
    except Exception:
        logger.exception("is_useful_todo failed")
    return True, "filter unavailable, keeping"


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

def resolve_contact_alias(name: str, phone: str, email: str,
                          candidates: list[dict]) -> dict:
    """Decide whether `name` is an alias of one of the existing contacts.

    candidates: list of dicts with id, name, aliases, phones, emails, company
    Returns: {"match_id": int|None, "alias_to_add": str|None,
              "reason": str, "confidence": str}
    """
    if not candidates:
        return {"match_id": None, "alias_to_add": None, "reason": "no candidates"}

    prompt = f"""Sei un assistente che decide se una persona menzionata è già nei contatti.

Nuova menzione:
- nome: "{name}"
- telefono: "{phone}"
- email: "{email}"

Contatti esistenti potenzialmente correlati (stesso primo nome o iniziale):
{json.dumps(candidates, ensure_ascii=False, indent=2)}

Considera lo STESSO contatto se:
- è uno stesso nome scritto diversamente (es. "Ghira" e "Ghiraffa Rossi")
- è un soprannome o diminutivo (es. "Ale" e "Alessandro Bianchi")
- è solo il primo nome di un contatto già completo (e non ci sono altri "Alessandro" plausibili)

NON considerare lo stesso contatto se:
- ci sono più contatti con lo stesso primo nome e nessun indizio aggiuntivo
- email/telefono divergono chiaramente
- il contesto suggerisce una persona diversa (azienda diversa, ecc.)

Rispondi SOLO con JSON:
{{"match_id": <id> oppure null,
  "alias_to_add": "<alias minuscolo da aggiungere>" oppure null,
  "confidence": "high"|"medium"|"low",
  "reason": "<una frase breve in italiano>"}}

Aggiungi alias_to_add solo se confidence=high e il nome menzionato non è già fra
nome o aliases del contatto."""

    try:
        raw = ask_text(prompt, source="dedup", operation="resolve_contact_alias")
        data = _safe_json(raw)
        match_id = data.get("match_id")
        if match_id and (data.get("confidence") or "").lower() in ("high", "medium"):
            return {
                "match_id": int(match_id),
                "alias_to_add": (data.get("alias_to_add") or "").lower().strip() or None,
                "reason": data.get("reason", ""),
                "confidence": data.get("confidence", "low"),
            }
    except Exception:
        logger.exception("resolve_contact_alias failed")
    return {"match_id": None, "alias_to_add": None, "reason": "no confident match"}
