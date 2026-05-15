"""Gemini TTS — convert text to WAV audio."""
import io
import logging
import wave

from google.genai import types

from workflows.gemini import _get_client

logger = logging.getLogger(__name__)

VOICE = "Charon"
SAMPLE_RATE = 24000
# 0.6 seconds of silence between sections
_SILENCE = b"\x00" * (SAMPLE_RATE * 2 // 2 + SAMPLE_RATE // 5 * 2)


def _text_to_pcm(text: str) -> bytes:
    client = _get_client()
    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-tts",
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
    return response.candidates[0].content.parts[0].inline_data.data


def _pcm_to_wav(pcm_data: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def text_to_wav(text: str) -> bytes:
    """Generate a WAV file from a text string. Returns WAV bytes."""
    pcm = _text_to_pcm(text)
    return _pcm_to_wav(pcm)


def generate_section_wav(topic_name: str, text: str) -> bytes:
    """Generate a WAV for a single topic section, prefixed with the topic name."""
    return text_to_wav(f"Sezione {topic_name}. {text.strip()}")
