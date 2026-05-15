"""Gemini TTS — convert text to WAV audio."""
import io
import logging
import time
import wave

from google.genai import types

from workflows.gemini import _get_client
from workflows.pricing import estimate_tts_audio_tokens
from workflows.usage_logger import log_usage

logger = logging.getLogger(__name__)

VOICE = "Charon"
SAMPLE_RATE = 24000
TTS_MODEL = "gemini-2.5-flash-preview-tts"
# 0.6 seconds of silence between sections
_SILENCE = b"\x00" * (SAMPLE_RATE * 2 // 2 + SAMPLE_RATE // 5 * 2)


def _text_to_pcm(text: str, source: str = "unknown",
                 operation: str = "tts", ref_id: str | int = "") -> bytes:
    client = _get_client()
    t0 = time.time()
    try:
        response = client.models.generate_content(
            model=TTS_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE)
                    )
                ),
            ),
        )
    except Exception as e:
        log_usage(provider="gemini", model=TTS_MODEL, operation=operation,
                  source=source, duration_ms=int((time.time() - t0) * 1000),
                  ref_type="audio", ref_id=ref_id, error=str(e))
        raise

    pcm = response.candidates[0].content.parts[0].inline_data.data

    # Token accounting: text in is roughly len(text)/4. Audio out is
    # estimated from PCM bytes (no reliable usage_metadata for TTS preview).
    prompt_tokens = max(1, len(text) // 4)
    audio_tokens = estimate_tts_audio_tokens(len(pcm))
    log_usage(provider="gemini", model=TTS_MODEL, operation=operation,
              source=source, prompt_tokens=prompt_tokens,
              output_tokens=audio_tokens, total_tokens=prompt_tokens + audio_tokens,
              duration_ms=int((time.time() - t0) * 1000),
              ref_type="audio", ref_id=ref_id)
    return pcm


def _pcm_to_wav(pcm_data: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def text_to_wav(text: str, source: str = "unknown",
                operation: str = "tts", ref_id: str | int = "") -> bytes:
    """Generate a WAV file from a text string. Returns WAV bytes."""
    pcm = _text_to_pcm(text, source=source, operation=operation, ref_id=ref_id)
    return _pcm_to_wav(pcm)


def generate_section_wav(topic_name: str, text: str,
                          source: str = "rss", ref_id: str | int = "") -> bytes:
    """Generate a WAV for a single topic section, prefixed with the topic name."""
    return text_to_wav(f"Sezione {topic_name}. {text.strip()}",
                        source=source, operation="tts_section", ref_id=ref_id)
