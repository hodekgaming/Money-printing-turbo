"""Streamlit web UI.  Run with:  streamlit run webui.py"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import streamlit as st

from app.config import STORAGE_DIR, load_config, save_config
from app.models import Aspect, SubtitleStyle, VideoParams
from app.services import llm, pipeline, video, voice
from app.utils import list_system_fonts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

st.set_page_config(page_title="Short Video Maker", page_icon="🎬", layout="wide")

UPLOAD_DIR = STORAGE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

LANGUAGES = ["English", "Spanish", "French", "German", "Italian", "Portuguese", "Japanese", "Korean",
             "Chinese", "Hindi", "Arabic", "Russian", "Dutch", "Turkish", "Polish"]

PROVIDER_FIELDS = {
    "openai": [("openai_api_key", "API key", True), ("openai_model", "Model", False)],
    "gemini": [("gemini_api_key", "API key", True), ("gemini_model", "Model", False)],
    "anthropic": [("anthropic_api_key", "API key", True), ("anthropic_model", "Model", False)],
    "deepseek": [("deepseek_api_key", "API key", True), ("deepseek_model", "Model", False)],
    "ollama": [("ollama_base_url", "Base URL", False), ("ollama_model", "Model", False)],
    "openai_compatible": [("compatible_base_url", "Base URL", False), ("compatible_api_key", "API key", True),
                          ("compatible_model", "Model", False)],
}

if "cfg" not in st.session_state:
    st.session_state.cfg = load_config()
cfg = st.session_state.cfg
st.session_state.setdefault("script", "")
st.session_state.setdefault("terms", "")
st.session_state.setdefault("title", "")
st.session_state.setdefault("edge_voices", voice.POPULAR_EDGE_VOICES)


def save_upload(f) -> str:
    data = f.getvalue()
    path = UPLOAD_DIR / (hashlib.md5(data).hexdigest()[:12] + Path(f.name).suffix.lower())
    if not path.exists():
        path.write_bytes(data)
    return str(path)


# --------------------------------------------------------------------------- #
# Sidebar: settings
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("⚙️ Settings")
    st.caption("Saved to `config.toml`. Environment variables (e.g. `OPENAI_API_KEY`) override these.")

    st.subheader("AI script writer")
    provider = st.selectbox("LLM provider", llm.PROVIDERS, index=llm.PROVIDERS.index(cfg["llm"]["provider"])
                            if cfg["llm"]["provider"] in llm.PROVIDERS else 0)
    cfg["llm"]["provider"] = provider
    for key, label, secret in PROVIDER_FIELDS[provider]:
        cfg["llm"][key] = st.text_input(label, cfg["llm"].get(key, ""), type="password" if secret else "default",
                                        key=f"cfg_{key}")

    st.subheader("Free stock footage")
    pexels = st.text_input("Pexels API key(s)", ", ".join(cfg["materials"]["pexels_api_keys"]), type="password",
                           help="Free at https://www.pexels.com/api/ — comma-separate several to rotate.")
    pixabay = st.text_input("Pixabay API key(s)", ", ".join(cfg["materials"]["pixabay_api_keys"]), type="password",
                            help="Free at https://pixabay.com/api/docs/")
    cfg["materials"]["pexels_api_keys"] = [k.strip() for k in pexels.split(",") if k.strip()]
    cfg["materials"]["pixabay_api_keys"] = [k.strip() for k in pixabay.split(",") if k.strip()]

    with st.expander("Advanced"):
        cfg["app"]["proxy"] = st.text_input("HTTP(S) proxy", cfg["app"].get("proxy", ""))
        cfg["tts"]["openai_tts_model"] = st.text_input("OpenAI TTS model", cfg["tts"].get("openai_tts_model", ""))

    if st.button("💾 Save settings", use_container_width=True):
        save_config(cfg)
        st.success("Saved to config.toml")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
st.title("🎬 Short Video Maker")
st.caption("Give it a topic — AI writes the script, finds free stock footage, records a voiceover, "
           "adds subtitles and music, and renders a ready-to-post HD video.")

col_script, col_video, col_subs = st.columns([1.25, 1, 1], gap="large")

# ---- Script ---------------------------------------------------------------
with col_script:
    st.subheader("1 · Script")
    topic = st.text_input("Topic or keyword", placeholder="e.g. the benefits of reading")
    c1, c2 = st.columns(2)
    language = c1.selectbox("Script language", LANGUAGES)
    duration_hint = c2.slider("Target length (s)", 15, 120, 45, 5)
    paragraphs = st.slider("Paragraphs", 1, 5, 1)

    def _gen_script():
        if not topic.strip():
            st.session_state.flash = ("warning", "Enter a topic first.")
            return
        try:
            res = llm.generate_script(topic, cfg, language, paragraphs, duration_hint)
            st.session_state.script = res.script
            st.session_state.terms = ", ".join(res.search_terms)
            st.session_state.title = res.title
        except Exception as e:  # show any provider error in the UI
            st.session_state.flash = ("error", f"Script generation failed: {e}")

    def _gen_terms():
        try:
            terms = llm.generate_search_terms(topic or st.session_state.title, st.session_state.script, cfg)
            st.session_state.terms = ", ".join(terms)
        except Exception as e:
            st.session_state.flash = ("error", f"Search term generation failed: {e}")

    b1, b2 = st.columns(2)
    b1.button("✨ Generate script", on_click=_gen_script, use_container_width=True, type="primary")
    b2.button("🔎 Regenerate keywords", on_click=_gen_terms, use_container_width=True,
              disabled=not st.session_state.script)
    if flash := st.session_state.pop("flash", None):
        getattr(st, flash[0])(flash[1])

    st.text_area("Script (editable — leave empty to auto-generate on render)", key="script", height=260)
    st.text_input("Footage search keywords (comma-separated, English)", key="terms")

# ---- Video + audio --------------------------------------------------------
with col_video:
    st.subheader("2 · Video")
    aspect = Aspect(st.radio("Format", [a.value for a in Aspect], horizontal=True,
                             format_func=lambda v: {"9:16": "9:16 Shorts/TikTok", "16:9": "16:9 YouTube",
                                                    "1:1": "1:1 Square"}[v]))
    source = st.selectbox("Footage source", ["pexels", "pixabay", "local"],
                          format_func=lambda s: {"pexels": "Pexels (free)", "pixabay": "Pixabay (free)",
                                                 "local": "My own files"}[s])
    local_files: list[str] = []
    if source == "local":
        ups = st.file_uploader("Upload clips / images", accept_multiple_files=True,
                               type=["mp4", "mov", "m4v", "webm", "mkv", "jpg", "jpeg", "png", "webp"])
        local_files = [save_upload(u) for u in ups or []]
    c1, c2 = st.columns(2)
    max_clip = c1.slider("Max seconds per clip", 2, 10, 4)
    transition = c2.selectbox("Transition", ["fade", "none"])

    st.subheader("3 · Voice")
    engine = st.radio("TTS engine", ["edge", "openai"], horizontal=True,
                      format_func=lambda e: {"edge": "Edge TTS (free)", "openai": "OpenAI TTS"}[e])
    if engine == "edge":
        voices = st.session_state.edge_voices
        lang_prefix = st.text_input("Filter voices", "en-", help="e.g. en-US, es-, fr-FR")
        shown = [v for v in voices if v.lower().startswith(lang_prefix.lower())] or voices
        voice_name = st.selectbox("Voice", shown)
        if st.button("Load all 400+ voices"):
            st.session_state.edge_voices = voice.list_edge_voices()
            st.rerun()
    else:
        voice_name = st.selectbox("Voice", voice.OPENAI_VOICES, index=voice.OPENAI_VOICES.index("nova"))
    c1, c2 = st.columns(2)
    rate = c1.slider("Speed", 0.5, 2.0, 1.1, 0.05)
    voice_volume = c2.slider("Voice volume", 0.2, 2.0, 1.0, 0.1)
    if st.button("▶️ Preview voice"):
        sample = (st.session_state.script or "Hi! This is how your video's voiceover will sound.")[:200]
        try:
            res = voice.synthesize(sample, engine, voice_name, str(STORAGE_DIR / "preview.mp3"), rate, cfg)
            st.audio(res.audio_path)
        except Exception as e:
            st.error(f"Voice preview failed: {e}")

    st.subheader("4 · Music")
    songs = video.list_songs()
    bgm_opts = ["random", "none", *songs, "upload"]
    bgm_choice = st.selectbox("Background music", bgm_opts,
                              format_func=lambda s: {"random": "Random from resource/songs", "none": "No music",
                                                     "upload": "Upload a track…"}.get(s, Path(s).name))
    if bgm_choice == "random" and not songs:
        st.caption("No tracks in `resource/songs/` yet — run `python scripts/make_demo_music.py` or add your own.")
    if bgm_choice == "upload":
        up = st.file_uploader("Music file", type=["mp3", "m4a", "wav", "ogg", "flac"])
        bgm_choice = save_upload(up) if up else "none"
    bgm_volume = st.slider("Music volume", 0.0, 1.0, 0.15, 0.01)

# ---- Subtitles ------------------------------------------------------------
with col_subs:
    st.subheader("5 · Subtitles")
    sub_on = st.toggle("Burn in subtitles", True)
    fonts = list_system_fonts() or ["DejaVu Sans"]
    default_font = next((f for f in ("Montserrat", "Inter", "Arial", "DejaVu Sans") if f in fonts), fonts[0])
    font = st.selectbox("Font", fonts, index=fonts.index(default_font), disabled=not sub_on,
                        help="Any installed font; drop .ttf/.otf files into resource/fonts/ to add more.")
    c1, c2 = st.columns(2)
    font_size = c1.slider("Size (px)", 30, 140, 72 if aspect != Aspect.LANDSCAPE else 60, disabled=not sub_on)
    position = c2.selectbox("Position", ["bottom", "center", "top"], disabled=not sub_on)
    c1, c2, c3 = st.columns(3)
    color = c1.color_picker("Text", "#FFFFFF", disabled=not sub_on)
    highlight = c2.color_picker("Highlight", "#FFD400", disabled=not sub_on)
    outline_color = c3.color_picker("Outline", "#000000", disabled=not sub_on)
    karaoke = st.checkbox("Highlight each word as it is spoken", True, disabled=not sub_on)
    c1, c2 = st.columns(2)
    outline = c1.slider("Outline width", 0.0, 10.0, 4.0, 0.5, disabled=not sub_on)
    max_lines = c2.slider("Max lines", 1, 3, 2, disabled=not sub_on)
    c1, c2 = st.columns(2)
    bold = c1.checkbox("Bold", True, disabled=not sub_on)
    upper = c2.checkbox("UPPERCASE", False, disabled=not sub_on)

# --------------------------------------------------------------------------- #
# Generate
# --------------------------------------------------------------------------- #
st.divider()
if st.button("🚀 Generate video", type="primary", use_container_width=True):
    params = VideoParams(
        topic=topic,
        script=st.session_state.script,
        search_terms=[t.strip() for t in st.session_state.terms.split(",") if t.strip()],
        language=language,
        paragraphs=paragraphs,
        duration_hint=duration_hint,
        aspect=aspect,
        material_source=source,
        local_materials=local_files,
        max_clip_duration=max_clip,
        transition=transition,
        tts_engine=engine,
        voice=voice_name,
        voice_rate=rate,
        voice_volume=voice_volume,
        bgm=bgm_choice,
        bgm_volume=bgm_volume,
        subtitle=SubtitleStyle(
            enabled=sub_on, font=font, font_size=font_size, color=color, highlight_color=highlight,
            karaoke=karaoke, outline_color=outline_color, outline_width=outline, bold=bold,
            uppercase=upper, position=position, max_lines=max_lines,
        ),
    )
    bar = st.progress(0.0, "Starting…")
    try:
        result = pipeline.run(params, cfg, progress=lambda f, m: bar.progress(min(1.0, f), m))
    except Exception as e:
        bar.empty()
        st.error(f"❌ {e}")
        logging.exception("Generation failed")
    else:
        bar.progress(1.0, f"Done in {result.timings['total']:.0f}s")
        st.session_state.last_result = result

if res := st.session_state.get("last_result"):
    st.subheader(f"✅ {res.title}")
    vcol, icol = st.columns([1, 1.4])
    with vcol:
        st.video(res.video_path)
    with icol:
        st.write(f"**Length:** {res.duration:.1f}s · **Clips:** {res.clips_used} · "
                 f"**Keywords:** {', '.join(res.search_terms) or '—'}")
        st.download_button("⬇️ Download MP4", Path(res.video_path).read_bytes(), file_name=f"{res.task_id}.mp4",
                           mime="video/mp4", use_container_width=True)
        if res.srt_path:
            st.download_button("⬇️ Download subtitles (.srt)", Path(res.srt_path).read_text(encoding="utf-8"),
                               file_name=f"{res.task_id}.srt", use_container_width=True)
        st.caption(f"Files saved in `{res.task_dir}`")
        with st.expander("Script"):
            st.write(res.script)
