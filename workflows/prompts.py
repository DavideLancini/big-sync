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
      "start_date": "YYYY-MM-DD",
      "start_time": "HH:MM",
      "duration_minutes": 15,
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
- Per i todo: start_date e start_time sono OBBLIGATORI. Se non puoi inferire quando va fatto il task dal contenuto, usa la data/ora del messaggio stesso.
- Per i todo: duration_minutes è OBBLIGATORIO. Stima una durata realistica in base al tipo di task. Se non puoi stimare, usa 15.
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


def realtime_prompt(chat_name: str, new_msg: dict, context_msgs: list[dict]) -> str:
    """
    Prompt for real-time single-message analysis with preceding context.
    new_msg / context_msgs: dicts with keys: time, date, sender, text, media_type
    Extract only from new_msg; context is for disambiguation only.
    """
    context_block = ""
    if context_msgs:
        lines = []
        for m in context_msgs:
            text = m["text"] or f"[{m['media_type']}]"
            lines.append(f"[{m['time']}] {m['sender'] or 'Sconosciuto'}: {text}")
        context_block = "Messaggi precedenti (solo contesto, NON estrarre da questi):\n" + "\n".join(lines) + "\n\n"

    new_text = new_msg["text"] or f"[{new_msg['media_type']}]"
    new_line = f"[{new_msg['time']}] {new_msg['sender'] or 'Sconosciuto'}: {new_text}"

    return f"""Analizza SOLO l'ultimo messaggio nella chat "{chat_name}" (data: {new_msg['date']}).
I messaggi precedenti sono forniti solo come contesto — non estrarre informazioni da loro.

{context_block}Messaggio da analizzare:
{new_line}

{EXTRACTION_SCHEMA}"""
