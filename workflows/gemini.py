"""Gemini API client wrapper."""
import json
import logging

import google.generativeai as genai
from decouple import config

logger = logging.getLogger(__name__)

_client_initialized = False


def _init():
    global _client_initialized
    if not _client_initialized:
        genai.configure(api_key=config("GEMINI_API_KEY"))
        _client_initialized = True


def extract_json(text: str) -> dict:
    """Extract JSON from Gemini response, handling markdown code blocks."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        logger.warning("Could not parse Gemini JSON response: %s", text[:200])
        return {"contacts": [], "events": [], "todos": []}


def ask(prompt: str, model: str = "gemini-2.0-flash-lite") -> dict:
    """Send prompt to Gemini, return parsed JSON extraction result."""
    _init()
    try:
        model_obj = genai.GenerativeModel(model)
        response = model_obj.generate_content(prompt)
        return extract_json(response.text)
    except Exception:
        logger.exception("Gemini API error")
        return {"contacts": [], "events": [], "todos": []}
