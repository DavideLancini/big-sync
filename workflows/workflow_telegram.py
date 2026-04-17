"""Orchestrate Gemini extraction + Google Workspace writes for Telegram messages."""
import logging

from outputs.contacts import upsert_contact
from outputs.calendar import upsert_event
from outputs.tasks import upsert_task
from workflows.gemini import ask
from workflows.prompts import batch_prompt, single_prompt, realtime_prompt

logger = logging.getLogger(__name__)


def process_batch(chat_name: str, date: str, messages: list[dict]) -> dict:
    """
    Analyze a batch of messages from a single chat on a single day.
    messages: list of dicts with keys: time, sender, text, media_type
    Returns counts: {contacts, events, todos}
    """
    if not messages:
        return {"contacts": 0, "events": 0, "todos": 0}

    prompt = batch_prompt(chat_name, date, messages)
    # Raises on Gemini error — caller must NOT mark messages as processed on exception
    extracted = ask(prompt)
    return _write_extracted(extracted, source=f"batch:{chat_name}:{date}")


def process_message(chat_name: str, sender: str, datetime_str: str,
                    text: str, media_type: str) -> dict:
    """
    Analyze a single real-time message.
    Returns counts: {contacts, events, todos}
    """
    prompt = single_prompt(chat_name, sender, datetime_str, text, media_type)
    extracted = ask(prompt)
    return _write_extracted(extracted, source=f"realtime:{chat_name}")


def process_realtime_message(chat_name: str, new_msg: dict, context_msgs: list[dict]) -> dict:
    """
    Analyze a new real-time message using up to 10 preceding messages as context.
    new_msg / context_msgs: dicts with keys: time, date, sender, text, media_type
    Extract only from new_msg; context aids disambiguation only.
    Returns counts: {contacts, events, todos}
    """
    prompt = realtime_prompt(chat_name, new_msg, context_msgs)
    extracted = ask(prompt)
    return _write_extracted(extracted, source=f"realtime:{chat_name}")


def _write_extracted(extracted: dict, source: str) -> dict:
    contacts_written = 0
    events_written = 0
    todos_written = 0

    for c in extracted.get("contacts") or []:
        try:
            result = upsert_contact(c)
            if result:
                contacts_written += 1
        except Exception:
            logger.exception("[%s] Error writing contact: %s", source, c)

    for e in extracted.get("events") or []:
        try:
            result = upsert_event(e)
            if result:
                events_written += 1
        except Exception:
            logger.exception("[%s] Error writing event: %s", source, e)

    for t in extracted.get("todos") or []:
        try:
            result = upsert_task(t)
            if result:
                todos_written += 1
        except Exception:
            logger.exception("[%s] Error writing task: %s", source, t)

    counts = {"contacts": contacts_written, "events": events_written, "todos": todos_written}
    total = sum(counts.values())
    if total:
        logger.info("[%s] Written: %s", source, counts)
    return counts
