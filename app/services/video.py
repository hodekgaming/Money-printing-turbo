"""Video assembly with FFmpeg: clip normalisation, concatenation, audio mix and subtitle burn-in."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path

from app.config import FONTS_DIR, SONGS_DIR
from app.services.materials import Clip
from app.utils import run_ffmpeg

log = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}
FADE = 0.25  # seconds of dip-to-black at each cut when transition == "fade"


@dataclass
class Segment:
    clip: Clip
    offset: float
    duration: float


def list_songs() -> list[str]:
    return sorted(str(p) for p in SONGS_DIR.iterdir() if p.suffix.lower() in AUDIO_EXTS)


def pick_bgm(choice: str) -> str | None:
    if not choice or choice == "none":
        return None
    if choice == "random":
        songs = list_songs()
        return random.choice(songs) if songs else None
    return choice if Path(choice).exists() else None


def plan_segments(clips: list[Clip], total: float, max_clip: float, shuffle: bool = True) -> list[Segment]:
    """Lay clips end to end until ``total`` seconds are covered.

    Each clip contributes at most ``max_clip`` seconds per appearance. If we run out
    of clips they are reused, starting from a later offset where the clip is long
    enough, so repeats look different.
    """
    if not clips:
        raise ValueError("No clips to plan with")
    order = clips[:]
    if shuffle:
        random.shuffle(order)
    segs: list[Segment] = []
    used: dict[int, int] = {}
    t = 0.0
    i = 0
    while t < total - 0.05:
        clip = order[i % len(order)]
        i += 1
        avail = clip.duration if clip.duration > 0 else max_clip  # images: any length
        seg_len = min(avail, max_clip, total - t)
        if seg_len < 0.5 and segs:
            segs[-1].duration += total - t  # absorb the tiny remainder
            break
        n = used.get(id(clip), 0)
        used[id(clip)] = n + 1
        offset = 0.0
        if clip.duration > 0 and clip.duration - seg_len > 0.5:
            offset = (n * max_clip) % (clip.duration - seg_len)
        segs.append(Segment(clip, round(offset, 3), round(seg_len, 3)))
        t += seg_len
        if i > 10_000:  # safety
            break
    return segs


def _render_segment(seg: Segment, out: str, size: tuple[int, int], fps: int, transition: str) -> None:
    w, h = size
    d = seg.duration
    fades = ""
    if transition == "fade" and d > FADE * 3:
        fades = f",fade=t=in:st=0:d={FADE},fade=t=out:st={d - FADE:.3f}:d={FADE}"
    if seg.clip.source == "image":
        # Ken Burns: cover at 115%, then pan horizontally over the segment.
        sw, sh = int(w * 1.15), int(h * 1.15)
        vf = (
            f"scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
            f"crop={w}:{h}:x='(iw-{w})*t/{d:.3f}':y='(ih-{h})/2',"
            f"fps={fps},setsar=1,format=yuv420p{fades}"
        )
        inputs = ["-loop", "1", "-t", f"{d:.3f}", "-i", seg.clip.path]
    else:
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"fps={fps},setsar=1,format=yuv420p{fades}"
        )
        inputs = ["-ss", f"{seg.offset:.3f}", "-t", f"{d:.3f}", "-i", seg.clip.path]
    run_ffmpeg([
        *inputs, "-vf", vf, "-an", "-t", f"{d:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(fps), out,
    ])


def _escape_filter_path(p: str) -> str:
    return p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def compose(
    segments: list[Segment],
    voice_path: str,
    out_path: str,
    work_dir: str,
    size: tuple[int, int],
    duration: float,
    fps: int = 30,
    transition: str = "fade",
    voice_volume: float = 1.0,
    bgm_path: str | None = None,
    bgm_volume: float = 0.15,
    ass_path: str | None = None,
    progress=None,
) -> str:
    work = Path(work_dir)
    seg_dir = work / "segments"
    seg_dir.mkdir(exist_ok=True)

    files = []
    for i, seg in enumerate(segments):
        out = seg_dir / f"seg_{i:03d}.mp4"
        _render_segment(seg, str(out), size, fps, transition)
        files.append(out)
        if progress:
            progress((i + 1) / (len(segments) + 1), f"Rendered clip {i + 1}/{len(segments)}")

    concat_list = work / "concat.txt"
    concat_list.write_text("".join(f"file '{f.resolve().as_posix()}'\n" for f in files))
    merged = work / "merged.mp4"
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(merged)])

    if progress:
        progress(len(segments) / (len(segments) + 1), "Mixing audio and burning subtitles")

    args = ["-i", str(merged), "-i", voice_path]
    vchain = "[0:v]null"
    if ass_path:
        fontsdir = _escape_filter_path(str(FONTS_DIR.resolve()))
        vchain = f"[0:v]ass='{_escape_filter_path(str(Path(ass_path).resolve()))}':fontsdir='{fontsdir}'"
    filters = [f"{vchain}[v]", f"[1:a]volume={voice_volume:.3f},apad[voice]"]
    if bgm_path:
        args += ["-stream_loop", "-1", "-i", bgm_path]
        fade_st = max(0.0, duration - 2.0)
        filters.append(
            f"[2:a]volume={bgm_volume:.3f},afade=t=in:st=0:d=1,afade=t=out:st={fade_st:.3f}:d=2[bgm]"
        )
        filters.append("[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
    else:
        filters.append("[voice]anull[a]")

    run_ffmpeg([
        *args,
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        out_path,
    ])
    if progress:
        progress(1.0, "Done")
    return out_path
