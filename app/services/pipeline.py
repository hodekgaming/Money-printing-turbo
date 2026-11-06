"""End-to-end orchestration: topic -> script -> voice -> subtitles -> footage -> final video."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.config import TASKS_DIR
from app.models import VideoParams
from app.services import llm, materials, subtitles, video, voice
from app.utils import require_ffmpeg

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]

# (start, end) share of the overall progress bar for each stage
STAGES = {
    "script": (0.00, 0.10),
    "voice": (0.10, 0.20),
    "subtitles": (0.20, 0.22),
    "materials": (0.22, 0.50),
    "render": (0.50, 1.00),
}


@dataclass
class TaskResult:
    task_id: str
    task_dir: str
    video_path: str
    title: str
    script: str
    search_terms: list[str]
    srt_path: str | None
    duration: float
    clips_used: int
    timings: dict[str, float] = field(default_factory=dict)


def _stage(progress: ProgressFn | None, name: str) -> ProgressFn:
    lo, hi = STAGES[name]

    def cb(frac: float, msg: str) -> None:
        if progress:
            progress(lo + (hi - lo) * max(0.0, min(1.0, frac)), msg)

    return cb


def run(params: VideoParams, cfg: dict, progress: ProgressFn | None = None, task_id: str | None = None) -> TaskResult:
    require_ffmpeg()
    task_id = task_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    task_dir = TASKS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    t0 = time.time()

    # 1. Script --------------------------------------------------------------
    p = _stage(progress, "script")
    title = params.topic
    script = params.script.strip()
    terms = [t for t in params.search_terms if t.strip()]
    if not script:
        if not params.topic.strip():
            raise ValueError("Provide a topic or a script.")
        p(0.1, "Writing the script with AI")
        res = llm.generate_script(params.topic, cfg, params.language, params.paragraphs, params.duration_hint)
        title, script = res.title, res.script
        terms = terms or res.search_terms
    if not terms and params.material_source != "local":
        p(0.6, "Generating footage search terms")
        terms = llm.generate_search_terms(params.topic or title, script, cfg)
    (task_dir / "script.txt").write_text(script, encoding="utf-8")
    p(1.0, "Script ready")
    timings["script"] = time.time() - t0

    # 2. Voiceover -----------------------------------------------------------
    p = _stage(progress, "voice")
    p(0.1, f"Generating voiceover ({params.tts_engine}: {params.voice})")
    t = time.time()
    speech = voice.synthesize(
        script, params.tts_engine, params.voice, str(task_dir / "voice.mp3"), params.voice_rate, cfg
    )
    if not speech.words:
        speech.words = voice.estimate_word_timings(script, speech.duration)
    duration = speech.duration + 0.6
    p(1.0, f"Voiceover ready ({speech.duration:.1f}s)")
    timings["voice"] = time.time() - t

    # 3. Subtitles -----------------------------------------------------------
    p = _stage(progress, "subtitles")
    width, height = params.aspect.resolution
    st = params.subtitle
    max_chars = st.max_chars_per_line or subtitles.auto_chars_per_line(width, st.font_size)
    cues = subtitles.build_cues(speech.words, max_chars, st.max_lines)
    srt_path = task_dir / "subtitles.srt"
    srt_path.write_text(subtitles.to_srt(cues, max_chars), encoding="utf-8")
    ass_path = None
    if st.enabled:
        ass_path = task_dir / "subtitles.ass"
        ass_path.write_text(subtitles.to_ass(cues, st, width, height, max_chars), encoding="utf-8")
    p(1.0, f"{len(cues)} subtitle cues")

    # 4. Footage -------------------------------------------------------------
    p = _stage(progress, "materials")
    t = time.time()
    if params.material_source == "local":
        clips = materials.local_clips(params.local_materials)
    else:
        p(0.02, f"Searching {params.material_source} for: {', '.join(terms)}")
        clips = materials.collect_clips(
            terms, duration, params.aspect, params.material_source, cfg, params.max_clip_duration, progress=p
        )
    p(1.0, f"{len(clips)} clip(s) ready")
    timings["materials"] = time.time() - t

    # 5. Render --------------------------------------------------------------
    p = _stage(progress, "render")
    t = time.time()
    segments = video.plan_segments(clips, duration, params.max_clip_duration, params.shuffle_clips)
    out_path = str(task_dir / "final.mp4")
    video.compose(
        segments,
        speech.audio_path,
        out_path,
        str(task_dir),
        (width, height),
        duration,
        fps=params.fps,
        transition=params.transition,
        voice_volume=params.voice_volume,
        bgm_path=video.pick_bgm(params.bgm),
        bgm_volume=params.bgm_volume,
        ass_path=str(ass_path) if ass_path else None,
        progress=p,
    )
    timings["render"] = time.time() - t
    timings["total"] = time.time() - t0

    result = TaskResult(
        task_id, str(task_dir), out_path, title, script, terms, str(srt_path), duration, len(clips), timings
    )
    meta = {"params": params.to_dict(), "result": result.__dict__}
    (task_dir / "task.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    log.info("Task %s finished in %.1fs -> %s", task_id, timings["total"], out_path)
    return result
