"""Script + stock-footage search term generation using pluggable LLM providers.

All providers are called over plain HTTPS with ``requests`` so no vendor SDKs
are required. Supported providers:

* ``openai``            - OpenAI Chat Completions
* ``gemini``            - Google Gemini (generativelanguage API)
* ``anthropic``         - Anthropic Claude Messages API
* ``deepseek``          - DeepSeek (OpenAI-compatible)
* ``ollama``            - local Ollama (OpenAI-compatible endpoint)
* ``openai_compatible`` - any other OpenAI-compatible endpoint
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import requests

from app.config import proxies

log = logging.getLogger(__name__)

PROVIDERS = ["openai", "gemini", "anthropic", "deepseek", "ollama", "openai_compatible"]
TIMEOUT = 120


class LLMError(RuntimeError):
    pass


@dataclass
class ScriptResult:
    title: str
    script: str
    search_terms: list[str]


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #

SCRIPT_PROMPT = """You are a scriptwriter for viral short-form vertical videos (TikTok, YouTube Shorts, Reels).

Write a voiceover script about: "{topic}"

Requirements:
- Language: {language}
- About {words} words (roughly {seconds} seconds when read aloud), split into {paragraphs} paragraph(s).
- Open with a strong hook in the first sentence. End with a short memorable takeaway.
- Plain spoken text only: no markdown, no emojis, no hashtags, no stage directions,
  no speaker labels, no "[music]" cues, no numbered headings.

Also produce {n_terms} search terms for finding matching free stock VIDEO footage.
Each term must be 1-3 words, in ENGLISH, concrete and visual (e.g. "reading book",
"library shelves", "sunrise city"), and closely related to the script.

Respond with ONLY a JSON object, no code fences:
{{"title": "<catchy title>", "script": "<the script>", "search_terms": ["term1", "term2", ...]}}
"""

TERMS_PROMPT = """Generate {n_terms} search terms for finding free stock VIDEO footage that matches
this voiceover script about "{topic}". Each term must be 1-3 English words, concrete and visual.

Script:
{script}

Respond with ONLY a JSON array of strings, no code fences."""


# --------------------------------------------------------------------------- #
# Provider calls
# --------------------------------------------------------------------------- #


def _openai_compatible(base_url: str, api_key: str, model: str, prompt: str, cfg: dict) -> str:
    if not model:
        raise LLMError("No model configured for the selected provider.")
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.8}
    r = requests.post(url, headers=headers, json=body, timeout=TIMEOUT, proxies=proxies(cfg))
    if r.status_code >= 400:
        raise LLMError(f"{url} returned {r.status_code}: {r.text[:500]}")
    return r.json()["choices"][0]["message"]["content"]


def _gemini(api_key: str, model: str, prompt: str, cfg: dict) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.8},
    }
    r = requests.post(
        url,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=body,
        timeout=TIMEOUT,
        proxies=proxies(cfg),
    )
    if r.status_code >= 400:
        raise LLMError(f"Gemini returned {r.status_code}: {r.text[:500]}")
    data = r.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected Gemini response: {json.dumps(data)[:500]}") from e
    return "".join(p.get("text", "") for p in parts)


def _anthropic(api_key: str, model: str, prompt: str, cfg: dict) -> str:
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={"model": model, "max_tokens": 2048, "messages": [{"role": "user", "content": prompt}]},
        timeout=TIMEOUT,
        proxies=proxies(cfg),
    )
    if r.status_code >= 400:
        raise LLMError(f"Anthropic returned {r.status_code}: {r.text[:500]}")
    return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")


def complete(prompt: str, cfg: dict) -> str:
    """Send a single-turn prompt to the configured provider and return the text."""
    llm = cfg["llm"]
    provider = llm.get("provider", "openai")

    def need(key: str) -> str:
        v = llm.get(key, "")
        if not v:
            raise LLMError(f"'{key}' is not set. Add it in Settings or config.toml.")
        return v

    if provider == "openai":
        return _openai_compatible("https://api.openai.com/v1", need("openai_api_key"), llm["openai_model"], prompt, cfg)
    if provider == "deepseek":
        return _openai_compatible("https://api.deepseek.com/v1", need("deepseek_api_key"), llm["deepseek_model"], prompt, cfg)
    if provider == "ollama":
        return _openai_compatible(llm["ollama_base_url"], "", llm["ollama_model"], prompt, cfg)
    if provider == "openai_compatible":
        return _openai_compatible(need("compatible_base_url"), llm.get("compatible_api_key", ""), llm["compatible_model"], prompt, cfg)
    if provider == "gemini":
        return _gemini(need("gemini_api_key"), llm["gemini_model"], prompt, cfg)
    if provider == "anthropic":
        return _anthropic(need("anthropic_api_key"), llm["anthropic_model"], prompt, cfg)
    raise LLMError(f"Unknown LLM provider: {provider}")


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #


def extract_json(text: str):
    """Pull the first JSON object/array out of an LLM reply (tolerates code fences / chatter)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = text.find(open_c), text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Could not parse JSON from model reply: {text[:300]}")


def clean_script(script: str) -> str:
    script = re.sub(r"[*#_`]+", "", script)  # markdown
    script = re.sub(r"\[[^\]]*\]|\([^)]*(music|pause|sfx)[^)]*\)", "", script, flags=re.I)
    script = re.sub(r"^\s*(narrator|voiceover|vo|host)\s*:\s*", "", script, flags=re.I | re.M)
    script = re.sub(r"[ \t]+", " ", script)
    script = re.sub(r"\n{3,}", "\n\n", script)
    return script.strip()


def _clean_terms(terms, limit: int) -> list[str]:
    out: list[str] = []
    for t in terms or []:
        t = re.sub(r"[^\w\s-]", "", str(t)).strip().lower()
        if t and t not in out:
            out.append(t)
    return out[:limit]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def generate_script(
    topic: str,
    cfg: dict,
    language: str = "English",
    paragraphs: int = 1,
    seconds: int = 45,
    n_terms: int = 6,
) -> ScriptResult:
    words = max(30, int(seconds * 2.5))
    prompt = SCRIPT_PROMPT.format(
        topic=topic, language=language, words=words, seconds=seconds, paragraphs=paragraphs, n_terms=n_terms
    )
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            data = extract_json(complete(prompt, cfg))
            if not isinstance(data, dict) or not data.get("script"):
                raise LLMError("Model reply had no 'script' field.")
            terms = _clean_terms(data.get("search_terms"), n_terms) or [topic.lower()]
            return ScriptResult(
                title=str(data.get("title") or topic).strip(),
                script=clean_script(str(data["script"])),
                search_terms=terms,
            )
        except LLMError as e:
            last_err = e
            log.warning("Script generation attempt %d failed: %s", attempt + 1, e)
            if "not set" in str(e) or "returned 4" in str(e):
                break  # configuration problem - retrying won't help
    raise LLMError(str(last_err))


def generate_search_terms(topic: str, script: str, cfg: dict, n_terms: int = 6) -> list[str]:
    prompt = TERMS_PROMPT.format(topic=topic, script=script, n_terms=n_terms)
    data = extract_json(complete(prompt, cfg))
    if isinstance(data, dict):
        data = data.get("search_terms") or next(iter(data.values()), [])
    return _clean_terms(data, n_terms) or [topic.lower()]
