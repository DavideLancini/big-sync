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


def generate_daily_briefing(date_label: str, summaries: list[dict], stdout=None) -> bytes:
    """
    Generate a WAV audio file reading all topic summaries for a day.
    summaries: list of {'topic': str, 'text': str}
    Returns WAV bytes.
    """
    def log(msg):
        if stdout:
            stdout.write(msg)

    pcm_chunks = []

    intro = f"Briefing del {date_label}."
    log(f"  Intro...")
    pcm_chunks.append(_text_to_pcm(intro))
    pcm_chunks.append(_SILENCE)

    for s in summaries:
        text = s["text"].strip()
        if not text:
            continue
        topic = s["topic"]
        log(f"  {topic}...")
        section_text = f"Sezione {topic}. {text}"
        pcm_chunks.append(_text_to_pcm(section_text))
        pcm_chunks.append(_SILENCE)

    log("  Composizione file WAV...")
    combined = b"".join(pcm_chunks)
    return _pcm_to_wav(combined)
