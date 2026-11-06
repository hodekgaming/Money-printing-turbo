"""Configuration loading/saving.

Settings live in ``config.toml`` at the project root (created from the web UI or
copied from ``config.example.toml``). Environment variables override API keys so
secrets never have to be written to disk.
"""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.toml"
EXAMPLE_CONFIG_PATH = ROOT_DIR / "config.example.toml"
STORAGE_DIR = ROOT_DIR / "storage"
TASKS_DIR = STORAGE_DIR / "tasks"
CACHE_DIR = STORAGE_DIR / "cache_videos"
SONGS_DIR = ROOT_DIR / "resource" / "songs"
FONTS_DIR = ROOT_DIR / "resource" / "fonts"

DEFAULTS: dict[str, Any] = {
    "llm": {
        "provider": "openai",
        "openai_api_key": "",
        "openai_model": "gpt-4o-mini",
        "gemini_api_key": "",
        "gemini_model": "gemini-2.5-flash",
        "anthropic_api_key": "",
        "anthropic_model": "claude-sonnet-5-5",
        "deepseek_api_key": "",
        "deepseek_model": "deepseek-chat",
        "ollama_base_url": "http://localhost:11434/v1",
        "ollama_model": "llama3.1",
        "compatible_base_url": "",
        "compatible_api_key": "",
        "compatible_model": "",
    },
    "materials": {"pexels_api_keys": [], "pixabay_api_keys": []},
    "tts": {"openai_tts_model": "gpt-4o-mini-tts"},
    "app": {"proxy": ""},
}

# env var -> (section, key)
ENV_OVERRIDES = {
    "OPENAI_API_KEY": ("llm", "openai_api_key"),
    "GEMINI_API_KEY": ("llm", "gemini_api_key"),
    "ANTHROPIC_API_KEY": ("llm", "anthropic_api_key"),
    "DEEPSEEK_API_KEY": ("llm", "deepseek_api_key"),
    "MPT_LLM_PROVIDER": ("llm", "provider"),
}
LIST_ENV_OVERRIDES = {
    "PEXELS_API_KEY": ("materials", "pexels_api_keys"),
    "PIXABAY_API_KEY": ("materials", "pixabay_api_keys"),
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or CONFIG_PATH
    cfg = copy.deepcopy(DEFAULTS)
    if path.exists():
        with open(path, "rb") as f:
            cfg = _merge(cfg, tomllib.load(f))
    for env, (section, key) in ENV_OVERRIDES.items():
        if os.environ.get(env):
            cfg[section][key] = os.environ[env]
    for env, (section, key) in LIST_ENV_OVERRIDES.items():
        if os.environ.get(env):
            cfg[section][key] = [k.strip() for k in os.environ[env].split(",") if k.strip()]
    return cfg


def save_config(cfg: dict[str, Any], path: Path | None = None) -> None:
    path = path or CONFIG_PATH
    with open(path, "wb") as f:
        tomli_w.dump(cfg, f)


def proxies(cfg: dict[str, Any]) -> dict[str, str] | None:
    p = (cfg.get("app") or {}).get("proxy") or ""
    return {"http": p, "https": p} if p else None


for _d in (TASKS_DIR, CACHE_DIR, SONGS_DIR, FONTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
