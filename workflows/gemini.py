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
            http_options=types.HttpOptions(timeout=60_000),  # 60s — applies to all requests
        )
    return _client


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


def transcribe_audio(file_path: str, model: str = "gemini-2.5-flash") -> str:
    """
    Transcribe an audio/voice file using Gemini File API.
    Returns the transcription text, or empty string on error.
    Raises on API error (60s HTTP timeout set at client level).
    """
    client = _get_client()
    mime_type = _audio_mime_type(file_path)
    uploaded = None
    try:
        with open(file_path, "rb") as f:
            uploaded = client.files.upload(
                file=f,
                config=types.UploadFileConfig(mime_type=mime_type),
            )
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime_type),
                "Trascrivi questo messaggio vocale parola per parola. Rispondi solo con la trascrizione, nessun altro testo.",
            ],
        )
        return response.text.strip()
    finally:
        if uploaded:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


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


def ask(prompt: str, model: str = "gemini-2.5-flash") -> dict:
    """
    Send prompt to Gemini, return parsed JSON extraction result.
    Raises on API error — callers must handle and decide whether to mark processed.
    """
    client = _get_client()
    response = client.models.generate_content(model=model, contents=prompt)
    return extract_json(response.text)
