"""Gemini API client wrapper using google-genai SDK."""
import json
import logging

from google import genai
from decouple import config

logger = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config("GEMINI_API_KEY"))
    return _client


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
    """Send prompt to Gemini, return parsed JSON extraction result."""
    try:
        client = _get_client()
        response = client.models.generate_content(model=model, contents=prompt)
        return extract_json(response.text)
    except Exception:
        logger.exception("Gemini API error")
        return {"contacts": [], "events": [], "todos": []}
