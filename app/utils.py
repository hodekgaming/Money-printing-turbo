"""Small helpers around ffmpeg/ffprobe."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise FFmpegError(f"'{tool}' was not found on PATH. Install FFmpeg (https://ffmpeg.org/download.html).")


def run_ffmpeg(args: list[str]) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(args))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()[-1500:]}")


def probe_duration(path: str) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True,
        text=True,
    )
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return 0.0


def has_audio(path: str) -> bool:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path],
        capture_output=True,
        text=True,
    )
    return bool(proc.stdout.strip())


def list_system_fonts() -> list[str]:
    """Font family names known to fontconfig (used by libass when burning subtitles)."""
    if not shutil.which("fc-list"):
        return []
    proc = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True)
    fams = {line.split(",")[0].strip() for line in proc.stdout.splitlines() if line.strip()}
    return sorted(fams)
