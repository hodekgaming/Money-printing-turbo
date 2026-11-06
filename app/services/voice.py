"""Text-to-speech with word-level timings.

* ``edge``   - Microsoft Edge neural voices via ``edge-tts`` (free, no key, 400+ voices).
               Returns exact word boundaries, used for perfectly synced subtitles.
* ``openai`` - OpenAI TTS (needs an API key). No timings are returned, so word
               timings are estimated proportionally to word length.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import requests

from app.config import proxies
from app.utils import probe_duration

log = logging.getLogger(__name__)

OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"]

# A curated set so the UI works offline; the full list can be fetched live.
POPULAR_EDGE_VOICES = [
    "en-US-AriaNeural", "en-US-JennyNeural", "en-US-GuyNeural", "en-US-AndrewNeural",
    "en-US-EmmaNeural", "en-US-BrianNeural", "en-US-ChristopherNeural", "en-US-MichelleNeural",
    "en-GB-SoniaNeural", "en-GB-RyanNeural", "en-AU-NatashaNeural", "en-AU-WilliamNeural",
    "en-IN-NeerjaNeural", "es-ES-ElviraNeural", "es-MX-JorgeNeural", "fr-FR-DeniseNeural",
    "fr-FR-HenriNeural", "de-DE-KatjaNeural", "de-DE-ConradNeural", "it-IT-ElsaNeural",
    "pt-BR-FranciscaNeural", "pt-BR-AntonioNeural", "ja-JP-NanamiNeural", "ko-KR-SunHiNeural",
    "zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "hi-IN-SwaraNeural", "ar-SA-ZariyahNeural",
    "ru-RU-SvetlanaNeural", "nl-NL-ColetteNeural", "tr-TR-EmelNeural", "pl-PL-ZofiaNeural",
]


class TTSError(RuntimeError):
    pass


@dataclass
class Word:
    text: str
    start: float  # seconds
    end: float


@dataclass
class SpeechResult:
    audio_path: str
    duration: float
    words: list[Word]


def _rate_str(rate: float) -> str:
    pct = round((rate - 1.0) * 100)
    return f"{pct:+d}%"


def _volume_str(volume: float) -> str:
    pct = max(-100, min(100, round((volume - 1.0) * 100)))
    return f"{pct:+d}%"


# --------------------------------------------------------------------------- #
# Edge TTS
# --------------------------------------------------------------------------- #


async def _edge_async(text: str, voice: str, rate: float, out_path: str, proxy: str | None) -> list[Word]:
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=_rate_str(rate), boundary="WordBoundary", proxy=proxy)
    words: list[Word] = []
    with open(out_path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append(Word(chunk["text"], start, start + chunk["duration"] / 1e7))
    return words


def edge_tts_synthesize(text: str, voice: str, out_path: str, rate: float = 1.0, cfg: dict | None = None) -> SpeechResult:
    proxy = ((cfg or {}).get("app") or {}).get("proxy") or None
    last: Exception | None = None
    for attempt in range(3):
        try:
            words = asyncio.run(_edge_async(text, voice, rate, out_path, proxy))
            dur = probe_duration(out_path)
            if dur <= 0:
                raise TTSError("Edge TTS returned no audio (is the voice name valid?).")
            return SpeechResult(out_path, dur, _attach_punctuation(text, words))
        except Exception as e:  # network hiccups are common; retry
            last = e
            log.warning("edge-tts attempt %d failed: %s", attempt + 1, e)
    raise TTSError(f"Edge TTS failed: {last}")


async def _list_edge_voices() -> list[dict]:
    import edge_tts

    return await edge_tts.list_voices()


def list_edge_voices() -> list[str]:
    """Fetch every available Edge voice (requires network). Falls back to the curated list."""
    try:
        voices = asyncio.run(_list_edge_voices())
        return sorted(v["ShortName"] for v in voices)
    except Exception as e:
        log.warning("Could not fetch Edge voice list: %s", e)
        return POPULAR_EDGE_VOICES


# --------------------------------------------------------------------------- #
# OpenAI TTS
# --------------------------------------------------------------------------- #


def openai_tts_synthesize(text: str, voice: str, out_path: str, rate: float, cfg: dict) -> SpeechResult:
    key = cfg["llm"].get("openai_api_key")
    if not key:
        raise TTSError("OpenAI TTS needs 'openai_api_key' (Settings).")
    r = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": cfg["tts"].get("openai_tts_model", "gpt-4o-mini-tts"),
            "voice": voice,
            "input": text,
            "speed": max(0.25, min(4.0, rate)),
            "response_format": "mp3",
        },
        timeout=300,
        proxies=proxies(cfg),
    )
    if r.status_code >= 400:
        raise TTSError(f"OpenAI TTS returned {r.status_code}: {r.text[:300]}")
    with open(out_path, "wb") as f:
        f.write(r.content)
    dur = probe_duration(out_path)
    return SpeechResult(out_path, dur, estimate_word_timings(text, dur))


# --------------------------------------------------------------------------- #
# Timing helpers
# --------------------------------------------------------------------------- #


def estimate_word_timings(text: str, duration: float, lead: float = 0.1, tail: float = 0.2) -> list[Word]:
    """Distribute words over ``duration`` by character weight; punctuation adds a pause."""
    tokens = text.split()
    if not tokens or duration <= 0:
        return []
    weights = []
    for t in tokens:
        w = len(re.sub(r"\W", "", t)) + 2
        if re.search(r"[.!?]$", t):
            w += 6
        elif re.search(r"[,;:]$", t):
            w += 3
        weights.append(w)
    span = max(0.1, duration - lead - tail)
    scale = span / sum(weights)
    words, cur = [], lead
    for tok, w in zip(tokens, weights):
        d = w * scale
        words.append(Word(tok, cur, cur + d * 0.85))
        cur += d
    return words


def _attach_punctuation(text: str, words: list[Word]) -> list[Word]:
    """Edge word boundaries drop punctuation; re-attach it from the source text so
    subtitles read naturally and line breaking can respect sentence ends."""
    tokens = text.split()
    out: list[Word] = []
    ti = 0
    for w in words:
        core = re.sub(r"\W", "", w.text).lower()
        match = None
        for j in range(ti, min(ti + 4, len(tokens))):
            if re.sub(r"\W", "", tokens[j]).lower() == core and core:
                match = j
                break
        if match is not None:
            out.append(Word(tokens[match], w.start, w.end))
            ti = match + 1
        else:
            out.append(w)
    return out


def synthesize(text: str, engine: str, voice: str, out_path: str, rate: float, cfg: dict) -> SpeechResult:
    if engine == "edge":
        return edge_tts_synthesize(text, voice, out_path, rate, cfg)
    if engine == "openai":
        return openai_tts_synthesize(text, voice, out_path, rate, cfg)
    raise TTSError(f"Unknown TTS engine: {engine}")
