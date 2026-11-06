import pytest

from app.models import Aspect, SubtitleStyle
from app.services import llm, materials, subtitles, video, voice
from app.services.voice import Word


def words(text, step=0.4):
    return [Word(t, i * step, i * step + step * 0.8) for i, t in enumerate(text.split())]


# ---------------------------------------------------------------- llm parsing
def test_extract_json_handles_fences_and_chatter():
    assert llm.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.extract_json('Sure! Here it is: {"script": "hi"} hope that helps') == {"script": "hi"}
    assert llm.extract_json('["x", "y"]') == ["x", "y"]
    with pytest.raises(llm.LLMError):
        llm.extract_json("no json here")


def test_clean_script_strips_markdown_and_cues():
    out = llm.clean_script("Narrator: **Reading** is great. [music swells]\n\n\n\nThe end.")
    assert out == "Reading is great. \n\nThe end."


def test_generate_script_uses_provider(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda prompt, cfg: '{"title":"T","script":"Hello world.","search_terms":["Books!","books","library"]}')
    res = llm.generate_script("reading", {"llm": {}})
    assert res.title == "T" and res.script == "Hello world."
    assert res.search_terms == ["books", "library"]


def test_missing_key_is_reported():
    with pytest.raises(llm.LLMError, match="openai_api_key"):
        llm.complete("hi", {"llm": {"provider": "openai", "openai_api_key": "", "openai_model": "x"}, "app": {}})


# ---------------------------------------------------------------- subtitles
def test_cues_break_on_sentences_and_length():
    cues = subtitles.build_cues(words("Reading is great. It builds focus and calm every single day."), max_chars=12, max_lines=2)
    assert cues[0].text == "Reading is great."
    assert all(len(c.text) <= 24 for c in cues)
    # cues are contiguous / non overlapping
    for a, b in zip(cues, cues[1:]):
        assert a.end <= b.start + 1e-9


def test_srt_and_ass_output():
    ws = words("Hello there friend. Second line here.")
    cues = subtitles.build_cues(ws, 20)
    srt = subtitles.to_srt(cues, 20)
    assert srt.startswith("1\n00:00:00,000 --> ")
    ass = subtitles.to_ass(cues, SubtitleStyle(), 1080, 1920, 20)
    assert "PlayResX: 1080" in ass and ass.count("Dialogue:") == 2
    assert "{\\k" in ass
    plain = subtitles.to_ass(cues, SubtitleStyle(karaoke=False, uppercase=True), 1080, 1920, 20)
    assert "HELLO THERE FRIEND." in plain and "\\k" not in plain


def test_karaoke_durations_match_word_starts():
    ws = [Word("a", 0.0, 0.2), Word("b", 0.5, 0.7), Word("c", 1.234, 1.5)]
    cue = subtitles.Cue(0.0, 1.5, ws)
    text = subtitles._karaoke_text(cue, [ws], SubtitleStyle())
    import re
    ks = [int(k) for k in re.findall(r"\\k(\d+)", text)]
    assert sum(ks) == 150  # ends exactly at last word end


def test_ass_color():
    assert subtitles.ass_color("#FF8800") == "&H000088FF"


# ---------------------------------------------------------------- voice timing
def test_estimate_word_timings_spans_duration():
    ws = voice.estimate_word_timings("One, two three. Four!", 4.0)
    assert len(ws) == 4 and ws[0].start == pytest.approx(0.1)
    assert ws[-1].end < 4.0
    assert all(a.start < b.start for a, b in zip(ws, ws[1:]))


def test_attach_punctuation():
    out = voice._attach_punctuation("Hello, world! Bye.", [Word("Hello", 0, 1), Word("world", 1, 2), Word("Bye", 2, 3)])
    assert [w.text for w in out] == ["Hello,", "world!", "Bye."]


# ---------------------------------------------------------------- planning
def test_plan_segments_covers_total_and_reuses_clips():
    clips = [materials.Clip("a", 3.0, 0, 0, "local", path="a"), materials.Clip("b", 20.0, 0, 0, "local", path="b")]
    segs = video.plan_segments(clips, 23.3, max_clip=5, shuffle=False)
    assert sum(s.duration for s in segs) == pytest.approx(23.3, abs=0.01)
    assert all(s.duration <= 5.0 + 0.5 for s in segs)
    b_offsets = [s.offset for s in segs if s.clip.url == "b"]
    assert len(set(b_offsets)) == len(b_offsets)  # repeated clip starts at different points


def test_pick_best_rendition():
    files = [("s", 360, 640), ("m", 720, 1280), ("l", 2160, 3840)]
    assert materials._pick_best(files, Aspect.PORTRAIT.resolution)[0] == "m"


def test_pexels_parsing(monkeypatch):
    class R:
        status_code = 200
        def json(self):
            return {"videos": [
                {"duration": 10, "video_files": [
                    {"link": "p1", "width": 1080, "height": 1920, "file_type": "video/mp4"},
                    {"link": "l1", "width": 1920, "height": 1080, "file_type": "video/mp4"}]},
                {"duration": 1, "video_files": [{"link": "short", "width": 1080, "height": 1920, "file_type": "video/mp4"}]},
            ]}
    monkeypatch.setattr(materials.requests, "get", lambda *a, **k: R())
    clips = materials.search_pexels("x", Aspect.PORTRAIT, {"materials": {"pexels_api_keys": ["k"]}, "app": {}})
    assert [c.url for c in clips] == ["p1"]
