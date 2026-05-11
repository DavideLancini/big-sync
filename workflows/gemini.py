"""Gemini API client wrapper using google-genai SDK."""
import json
import logging
import mimetypes
import os

from google import genai
from google.genai import types
from decouple import config

logger = logging.getLogger(__name__)

_client: genai.Client | None = None

AUDIO_MEDIA_TYPES = {"voice", "audio", "video_note"}


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=config("GEMINI_API_KEY"),
            http_options=types.HttpOptions(timeout=180_000),  # 180s — accommodates File API uploads
        )
    return _client


def _wait_for_active(client: genai.Client, name: str, timeout_s: int = 120) -> None:
    """Poll the File API until the upload reaches ACTIVE state."""
    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        info = client.files.get(name=name)
        state = getattr(info.state, "name", None) or str(info.state)
        if state == "ACTIVE":
            return
        if state == "FAILED":
            raise RuntimeError(f"Gemini File API marked upload as FAILED: {name}")
        time.sleep(2)
    raise TimeoutError(f"Gemini file {name} did not reach ACTIVE within {timeout_s}s")


def _audio_mime_type(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime and mime.startswith("audio/"):
        return mime
    ext = os.path.splitext(path)[1].lower()
    return {
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".mp4": "video/mp4",
    }.get(ext, "audio/ogg")


def transcribe_audio(file_path: str, model: str = "gemini-2.5-flash", retries: int = 4,
                     return_usage: bool = False):
    """
    Transcribe an audio/voice file using Gemini File API.
    Waits for the upload to reach ACTIVE state before calling generate_content,
    and retries 5xx errors with exponential backoff.
    """
    import time
    client = _get_client()
    mime_type = _audio_mime_type(file_path)
    uploaded = None
    try:
        with open(file_path, "rb") as f:
            uploaded = client.files.upload(
                file=f,
                config=types.UploadFileConfig(mime_type=mime_type),
            )
        _wait_for_active(client, uploaded.name)

        last_exc = None
        for attempt in range(retries):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[
                        types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime_type),
                        "Trascrivi questo messaggio vocale parola per parola. Rispondi solo con la trascrizione, nessun altro testo.",
                    ],
                )
                text = response.text.strip()
                if return_usage:
                    usage = getattr(response, "usage_metadata", None)
                    return text, {
                        "prompt": getattr(usage, "prompt_token_count", 0) or 0,
                        "output": getattr(usage, "candidates_token_count", 0) or 0,
                        "total": getattr(usage, "total_token_count", 0) or 0,
                    }
                return text
            except Exception as e:
                last_exc = e
                status = getattr(e, "status_code", None) or getattr(e, "code", None)
                if status and int(status) >= 500 and attempt < retries - 1:
                    wait = 5 * (2 ** attempt)
                    logger.warning("Gemini transcribe %s on attempt %d, retrying in %ds",
                                   status, attempt + 1, wait)
                    time.sleep(wait)
                else:
                    raise
        raise last_exc
    finally:
        if uploaded:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


def summarize_transcription(text: str, model: str = "gemini-2.5-flash") -> tuple[str, str]:
    """
    Given a transcription, produce (title, summary_markdown).
    Title is one short Italian sentence (max 80 chars). Summary is markdown
    with bullet points covering topics/decisions/action items.
    """
    prompt = f"""Sei un assistente che riassume registrazioni audio in italiano.
Ricevi sotto la trascrizione integrale di un audio. Restituisci un JSON con due campi:

- "title": un titolo descrittivo in italiano, max 80 caratteri, frase secca, no virgolette.
- "summary": riassunto in markdown italiano con sezioni concise:
  - "## Argomenti" elenco puntato dei temi principali
  - "## Decisioni" se presenti
  - "## Azioni / Todo" se presenti
  - "## Persone citate" se presenti
  Ogni bullet 1-2 righe. Salta sezioni vuote.

Trascrizione:
\"\"\"
{text}
\"\"\"

Rispondi SOLO con il JSON, niente altro testo, niente markdown fences."""
    data = ask(prompt, model=model)
    title = (data.get("title") or "").strip()[:255]
    summary = (data.get("summary") or "").strip()
    return title, summary


def extract_json(text: str) -> dict:
    """Extract JSON from Gemini response, handling markdown code blocks."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        logger.warning("Could not parse Gemini JSON response: %s", text[:200])
        return {"contacts": [], "events": [], "todos": []}


def ask_text(prompt: str, model: str = "gemini-2.5-flash", retries: int = 3) -> str:
    """Send prompt to Gemini, return raw text response."""
    import time
    client = _get_client()
    last_exc = None
    for attempt in range(retries):
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            return response.text.strip()
        except Exception as e:
            last_exc = e
            status = getattr(e, "status_code", None) or getattr(e, "code", None)
            if status and int(status) >= 500 and attempt < retries - 1:
                wait = 5 * (2 ** attempt)
                logger.warning("Gemini %s on attempt %d, retrying in %ds", status, attempt + 1, wait)
                time.sleep(wait)
            else:
                raise
    raise last_exc


def ask(prompt: str, model: str = "gemini-2.5-flash", retries: int = 3) -> dict:
    """
    Send prompt to Gemini, return parsed JSON extraction result.
    Retries up to `retries` times on 5xx errors with exponential backoff.
    Raises on API error after all retries exhausted.
    """
    import time
    client = _get_client()
    last_exc = None
    for attempt in range(retries):
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            return extract_json(response.text)
        except Exception as e:
            last_exc = e
            status = getattr(e, "status_code", None) or getattr(e, "code", None)
            if status and int(status) >= 500 and attempt < retries - 1:
                wait = 5 * (2 ** attempt)  # 5s, 10s, 20s
                logger.warning("Gemini %s on attempt %d, retrying in %ds", status, attempt + 1, wait)
                time.sleep(wait)
            else:
                raise
    raise last_exc
