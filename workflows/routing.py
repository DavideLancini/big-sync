"""AI calendar router: decide which calendar an event should live on.

Two destinations are currently routable:
  * "work"    → events about clients, projects, business meetings.
  * "chiara"  → events explicitly involving Chiara.
Everything else stays "personal" (primary calendar).

The classifier is conservative: when in doubt it falls back to "personal".
Both single-event and batch APIs are exposed; bulk classification is much
cheaper because Gemini handles many events per call.
"""
import json
import logging

from workflows.gemini import ask_text

logger = logging.getLogger(__name__)

VALID_ROUTES = ("work", "chiara", "personal")


_BATCH_PROMPT = """Sei un assistente che decide su quale calendario Google
mettere ogni evento dell'utente Davide Lancini.

Categorie disponibili:
- "work"     → riunioni di lavoro, call con clienti, deliverable, progetti
               aziendali, contatti professionali. Esempi: Erregame, Inspireng,
               Grimaldi, Polverini, Onecpas, Affri, Marvin, "Project Update
               Meeting", "Weekly Project Alignment", call con Francesco
               Circosta/Ace/Gray G/Angelo su progetti, fatture, proposte.
- "chiara"   → SOLO eventi che menzionano esplicitamente "Chiara" nel titolo,
               nella descrizione o tra i partecipanti.
- "personal" → tutto il resto: cene, compleanni, eventi familiari, amici,
               attività personali quotidiane, todo personali, viaggi non
               lavorativi, hobby.

Regole importanti:
- Sii CONSERVATIVO: se non sei sicuro, scegli "personal".
- "Compleanno di X" → "personal" (anche se X è un collega).
- "Chiamata con un amico" → "personal".
- "Call generica" senza contesto → "personal".
- Eventi che menzionano Chiara assieme al lavoro → "chiara".
- Todo lavorativi (es. "[todo] Inviare proposta Inspireng") → "work".

Eventi da classificare:
{events_json}

Rispondi SOLO con JSON, una entry per ogni evento nell'ordine dato:
{{"items": [
  {{"id": "<id>", "route": "work"|"chiara"|"personal",
    "confidence": "high"|"medium"|"low",
    "reason": "<una frase brevissima>"}}
]}}"""


def _safe_json(raw: str) -> dict:
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


def classify_events_batch(events: list[dict]) -> dict[str, dict]:
    """Classify many events with a single Gemini call.

    events: list of dicts with at least {id, title, attendees?, description?, location?}
    Returns: {event_id: {"route": str, "confidence": str, "reason": str}}
    """
    if not events:
        return {}

    payload = []
    for e in events:
        payload.append({
            "id": str(e.get("id", "")),
            "title": (e.get("title") or "")[:200],
            "description": (e.get("description") or "")[:200],
            "location": (e.get("location") or "")[:120],
            "attendees": [a for a in (e.get("attendees") or [])[:5]],
            "is_todo": bool(e.get("is_todo")),
        })

    prompt = _BATCH_PROMPT.format(events_json=json.dumps(payload, ensure_ascii=False, indent=2))
    try:
        raw = ask_text(prompt, source="routing", operation="classify_batch")
    except Exception:
        logger.exception("classify_events_batch failed (%d events)", len(events))
        return {}

    decision = _safe_json(raw)
    out: dict[str, dict] = {}
    for item in decision.get("items") or []:
        ev_id = str(item.get("id", ""))
        route = (item.get("route") or "").lower()
        if route not in VALID_ROUTES:
            continue
        out[ev_id] = {
            "route": route,
            "confidence": (item.get("confidence") or "low").lower(),
            "reason": item.get("reason", ""),
        }
    return out


def classify_event(event: dict) -> str:
    """Single-event classifier. Returns 'work'|'chiara'|'personal'.

    Defaults to 'personal' on any error or low confidence.
    """
    decisions = classify_events_batch([dict(event, id="single")])
    d = decisions.get("single")
    if not d:
        return "personal"
    if d["confidence"] in ("high", "medium"):
        return d["route"]
    return "personal"
