"""Prompt templates for Gemini workflows."""

EXTRACTION_SCHEMA = """
Rispondi SOLO con un JSON valido, senza testo aggiuntivo, con questa struttura:
{
  "contacts": [
    {
      "name": "Nome Cognome",
      "phone": "+39...",
      "email": "...",
      "company": "...",
      "role": "...",
      "notes": "..."
    }
  ],
  "events": [
    {
      "title": "...",
      "date": "YYYY-MM-DD",
      "time": "HH:MM",
      "end_date": "YYYY-MM-DD",
      "end_time": "HH:MM",
      "location": "...",
      "description": "...",
      "attendees": ["nome o email"],
      "meet_link": "https://...",
      "confidence": "high|medium|low"
    }
  ],
  "todos": [
    {
      "title": "...",
      "due_date": "YYYY-MM-DD",
      "notes": "...",
      "assigned_to": "me|altro nome"
    }
  ]
}

Regole:
- Estrai solo informazioni esplicitamente presenti o fortemente implicite.
- Usa null per i campi non disponibili.
- Per gli eventi: estrai anche link Google Meet/Zoom/Teams se menzionati.
- Per i todo: "assigned_to" è "me" se il task è per l'utente, altrimenti il nome della persona.
- Se non c'è nulla da estrarre, restituisci array vuoti.
- Le date relative (es. "domani", "giovedì") calcolale rispetto alla data del messaggio fornita.
- Il contesto della chat è: sei l'utente Davide Lancini (@Davidelenc).
"""


def batch_prompt(chat_name: str, date: str, messages: list[dict]) -> str:
    lines = []
    for m in messages:
        time_str = m["time"]
        sender = m["sender"] or "Sconosciuto"
        text = m["text"] or f"[{m['media_type']}]"
        lines.append(f"[{time_str}] {sender}: {text}")

    transcript = "\n".join(lines)

    return f"""Analizza questa conversazione Telegram del {date} nella chat "{chat_name}".
Estrai contatti, eventi e todo/task presenti nei messaggi.

Conversazione:
{transcript}

{EXTRACTION_SCHEMA}"""


def single_prompt(chat_name: str, sender: str, datetime_str: str,
                  text: str, media_type: str) -> str:
    content = text if text else f"[{media_type}]"
    return f"""Analizza questo messaggio Telegram ricevuto il {datetime_str} da "{sender}" nella chat "{chat_name}".
Estrai contatti, eventi e todo/task se presenti.

Messaggio: {content}

{EXTRACTION_SCHEMA}"""
