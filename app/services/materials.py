"""Free stock footage search + download (Pexels, Pixabay) and local materials."""

from __future__ import annotations

import hashlib
import itertools
import logging
from dataclasses import dataclass
from pathlib import Path

import requests

from app.config import CACHE_DIR, proxies
from app.models import Aspect
from app.utils import probe_duration

log = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}

_key_cycles: dict[str, itertools.cycle] = {}


class MaterialError(RuntimeError):
    pass


@dataclass
class Clip:
    url: str
    duration: float
    width: int
    height: int
    source: str
    term: str = ""
    path: str = ""  # local path once downloaded


def _next_key(name: str, keys: list[str]) -> str:
    keys = [k for k in keys if k]
    if not keys:
        raise MaterialError(f"No {name} API key configured. Get a free one and add it in Settings.")
    sig = name + "|" + "|".join(keys)
    if sig not in _key_cycles:
        _key_cycles[sig] = itertools.cycle(keys)
    return next(_key_cycles[sig])


def _fits(w: int, h: int, aspect: Aspect) -> bool:
    if aspect == Aspect.PORTRAIT:
        return h > w
    if aspect == Aspect.LANDSCAPE:
        return w > h
    return True


def _pick_best(files: list[tuple[str, int, int]], target: tuple[int, int]) -> tuple[str, int, int] | None:
    """Choose the smallest rendition that still covers the target resolution, else the largest."""
    tw, th = target
    files = [f for f in files if f[0] and f[1] and f[2]]
    if not files:
        return None
    covering = [f for f in files if min(f[1], f[2]) >= min(tw, th) * 0.66]
    if covering:
        return min(covering, key=lambda f: f[1] * f[2])
    return max(files, key=lambda f: f[1] * f[2])


def search_pexels(term: str, aspect: Aspect, cfg: dict, per_page: int = 15, min_duration: float = 3) -> list[Clip]:
    key = _next_key("Pexels", cfg["materials"].get("pexels_api_keys", []))
    params = {"query": term, "per_page": per_page, "orientation": aspect.orientation}
    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": key},
        params=params,
        timeout=30,
        proxies=proxies(cfg),
    )
    if r.status_code >= 400:
        raise MaterialError(f"Pexels returned {r.status_code}: {r.text[:300]}")
    clips = []
    for v in r.json().get("videos", []):
        if v.get("duration", 0) < min_duration:
            continue
        files = [
            (f.get("link"), f.get("width") or 0, f.get("height") or 0)
            for f in v.get("video_files", [])
            if f.get("file_type") == "video/mp4"
        ]
        files = [f for f in files if _fits(f[1], f[2], aspect)]
        best = _pick_best(files, aspect.resolution)
        if best:
            clips.append(Clip(best[0], float(v["duration"]), best[1], best[2], "pexels", term))
    return clips


def search_pixabay(term: str, aspect: Aspect, cfg: dict, per_page: int = 20, min_duration: float = 3) -> list[Clip]:
    key = _next_key("Pixabay", cfg["materials"].get("pixabay_api_keys", []))
    params = {"key": key, "q": term[:100], "per_page": per_page, "safesearch": "true"}
    r = requests.get("https://pixabay.com/api/videos/", params=params, timeout=30, proxies=proxies(cfg))
    if r.status_code >= 400:
        raise MaterialError(f"Pixabay returned {r.status_code}: {r.text[:300]}")
    clips = []
    for hit in r.json().get("hits", []):
        if hit.get("duration", 0) < min_duration:
            continue
        files = [
            (f.get("url"), f.get("width") or 0, f.get("height") or 0)
            for f in (hit.get("videos") or {}).values()
        ]
        files = [f for f in files if _fits(f[1], f[2], aspect)]
        best = _pick_best(files, aspect.resolution)
        if best:
            clips.append(Clip(best[0], float(hit["duration"]), best[1], best[2], "pixabay", term))
    return clips


def download(clip: Clip, cfg: dict) -> str:
    name = hashlib.md5(clip.url.encode()).hexdigest() + ".mp4"
    dest = CACHE_DIR / name
    if dest.exists() and dest.stat().st_size > 0:
        return str(dest)
    tmp = dest.with_suffix(".part")
    with requests.get(clip.url, stream=True, timeout=(15, 120), proxies=proxies(cfg),
                      headers={"User-Agent": "Mozilla/5.0 (short-video-maker)"}) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    tmp.rename(dest)
    return str(dest)


def collect_clips(
    terms: list[str],
    needed_seconds: float,
    aspect: Aspect,
    source: str,
    cfg: dict,
    max_clip_duration: float = 5,
    progress=None,
) -> list[Clip]:
    """Search each term and download clips until there is enough footage for the voiceover."""
    search = {"pexels": search_pexels, "pixabay": search_pixabay}.get(source)
    if search is None:
        raise MaterialError(f"Unknown material source: {source}")

    # Round-robin across terms so the visuals follow the variety of the script.
    per_term: list[list[Clip]] = []
    seen: set[str] = set()
    errors = []
    for term in terms:
        try:
            found = [c for c in search(term, aspect, cfg) if c.url not in seen]
        except (MaterialError, requests.RequestException) as e:
            errors.append(f"{term}: {e}")
            log.warning("Search failed for %r: %s", term, e)
            continue
        seen.update(c.url for c in found)
        per_term.append(found)
    candidates = [c for group in itertools.zip_longest(*per_term) for c in group if c]
    if not candidates:
        detail = "; ".join(errors) if errors else "no results"
        raise MaterialError(f"No stock footage found for {terms} ({detail})")

    picked: list[Clip] = []
    total = 0.0
    target = needed_seconds + max_clip_duration  # small margin
    for c in candidates:
        if total >= target:
            break
        try:
            c.path = download(c, cfg)
        except requests.RequestException as e:
            log.warning("Download failed %s: %s", c.url, e)
            continue
        c.duration = probe_duration(c.path) or c.duration
        picked.append(c)
        total += min(c.duration, max_clip_duration)
        if progress:
            progress(min(1.0, total / target), f"Downloaded {len(picked)} clip(s) ({total:.0f}s / {target:.0f}s)")
    if not picked:
        raise MaterialError("All clip downloads failed.")
    return picked


def local_clips(paths: list[str]) -> list[Clip]:
    clips = []
    for p in paths:
        ext = Path(p).suffix.lower()
        if ext in IMAGE_EXTS:
            clips.append(Clip(p, 0.0, 0, 0, "image", path=p))
        elif ext in VIDEO_EXTS:
            clips.append(Clip(p, probe_duration(p), 0, 0, "local", path=p))
    if not clips:
        raise MaterialError("No usable local images/videos were provided.")
    return clips
