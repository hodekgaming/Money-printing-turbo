"""End-to-end render using local synthetic footage and a stubbed TTS (no network needed)."""

import shutil
import subprocess

import pytest

from app.models import Aspect, VideoParams
from app.services import pipeline, voice
from app.utils import has_audio, probe_duration

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def ff(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


@pytest.mark.parametrize("aspect", [Aspect.PORTRAIT, Aspect.LANDSCAPE])
def test_full_pipeline(tmp_path, monkeypatch, aspect):
    clip, img, wav, music = (tmp_path / n for n in ("c.mp4", "i.png", "v.wav", "m.mp3"))
    ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25", "-t", "3", str(clip))
    ff("-f", "lavfi", "-i", "color=c=navy:s=640x640", "-frames:v", "1", str(img))
    ff("-f", "lavfi", "-i", "sine=f=200:d=6", str(wav))
    ff("-f", "lavfi", "-i", "sine=f=500:d=2", str(music))

    def fake_tts(text, engine, v, out, rate, cfg):
        shutil.copy(wav, out)
        return voice.SpeechResult(out, 6.0, [])

    monkeypatch.setattr(voice, "synthesize", fake_tts)
    monkeypatch.setattr(pipeline, "TASKS_DIR", tmp_path / "tasks")
    params = VideoParams(
        script="Short test script. It has two sentences!",
        material_source="local",
        local_materials=[str(clip), str(img)],
        aspect=aspect,
        max_clip_duration=2,
        bgm=str(music),
    )
    res = pipeline.run(params, {"llm": {}, "materials": {}, "tts": {}, "app": {}})
    out = res.video_path
    assert probe_duration(out) == pytest.approx(6.6, abs=0.15)
    assert has_audio(out)
    w, h = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height", "-of", "csv=p=0", out],
        capture_output=True, text=True,
    ).stdout.strip().split(",")
    assert (int(w), int(h)) == aspect.resolution
