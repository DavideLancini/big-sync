"""Centralized pricing for LLM/AI calls. USD per 1M tokens.

When prices change, edit this file. To add a new provider/model,
add a row. Operation == 'tts' rows use audio output tokens.
"""

# (provider, model) -> {input, output} in USD per 1M tokens
PRICING = {
    ("gemini", "gemini-2.5-flash"): {
        "input": 0.30,
        "output": 2.50,
    },
    ("gemini", "gemini-2.5-flash-preview-tts"): {
        "input": 0.50,
        "output": 10.00,  # audio output tokens
    },
    ("gemini", "gemini-2.5-pro"): {
        "input": 1.25,
        "output": 10.00,
    },
}

# For TTS we don't get token counts back from the API directly:
# we estimate audio tokens from raw PCM bytes.
# 24 kHz mono 16-bit -> 48000 bytes/sec; ~32 audio tokens/sec.
TTS_AUDIO_TOKENS_PER_BYTE = 32 / 48000  # ~6.67e-4


def estimate_cost_usd(provider: str, model: str, prompt_tokens: int, output_tokens: int) -> float:
    p = PRICING.get((provider, model))
    if not p:
        return 0.0
    return (prompt_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000


def estimate_tts_audio_tokens(pcm_bytes: int) -> int:
    return int(pcm_bytes * TTS_AUDIO_TOKENS_PER_BYTE)
