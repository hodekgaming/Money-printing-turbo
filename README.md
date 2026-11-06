# 🎬 Short Video Maker

Turn a topic like **"the benefits of reading"** into a ready-to-post HD short video —
fully automated:

1. **Script** — an LLM (OpenAI, Gemini, Claude, DeepSeek, Ollama or any OpenAI-compatible API) writes a hook-driven voiceover script plus footage search keywords.
2. **Footage** — free stock clips are pulled from **Pexels** or **Pixabay** (or your own uploads), matched to the format's orientation.
3. **Voiceover** — free **Edge TTS** neural voices (400+ voices, 70+ languages) or **OpenAI TTS**.
4. **Subtitles** — word-synced, styled captions burned in, with optional TikTok-style word-by-word highlighting. An `.srt` is exported too.
5. **Music** — background track mixed under the voice at your chosen volume, looped and faded out.
6. **Render** — FFmpeg assembles a 1080×1920 (9:16), 1920×1080 (16:9) or 1080×1080 (1:1) H.264/AAC MP4.

![pipeline](https://img.shields.io/badge/topic%20→%20script%20→%20voice%20→%20subtitles%20→%20footage%20→%20mp4-informational)

## Quick start

Requirements: **Python 3.11+** and **FFmpeg** (with libass, which standard builds include).

```bash
# macOS: brew install ffmpeg      Ubuntu/Debian: sudo apt install ffmpeg      Windows: winget install ffmpeg
pip install -r requirements.txt
python scripts/make_demo_music.py      # optional: two royalty-free demo music loops
streamlit run webui.py                 # opens http://localhost:8501
```

Then in the sidebar:

* pick an **LLM provider** and paste its API key (OpenAI / Gemini / Anthropic / DeepSeek — or use a local **Ollama** with no key),
* paste a free **Pexels** key (<https://www.pexels.com/api/>) and/or **Pixabay** key (<https://pixabay.com/api/docs/>),
* click **Save settings** (writes `config.toml`).

Type a topic and press **🚀 Generate video**. Optionally press **✨ Generate script** first to review/edit the script and keywords before rendering.

### Docker

```bash
docker build -t short-video-maker .
docker run -p 8501:8501 -v "$PWD/storage:/app/storage" -e OPENAI_API_KEY=sk-... -e PEXELS_API_KEY=... short-video-maker
```

## Command line

```bash
python cli.py "the benefits of reading"
python cli.py "5 facts about octopuses" --aspect 16:9 --voice en-GB-RyanNeural --bgm-volume 0.1
python cli.py --script-file my_script.txt --terms "ocean,coral reef,diver" --source pixabay
python cli.py "morning routine" --source local --local clip1.mp4 photo.jpg --bgm none
python cli.py --help
```

## Configuration

Settings come from `config.toml` (see `config.example.toml`); these environment variables override it:
`OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`
(comma-separated for several keys, which are rotated), and `MPT_LLM_PROVIDER`.

| UI option | What it does |
|---|---|
| Format | 9:16 Shorts/TikTok, 16:9 YouTube, 1:1 square — HD output, clips are cropped to fill |
| Footage source | Pexels, Pixabay, or your own uploaded clips/images (images get a slow Ken Burns pan) |
| Max seconds per clip | How long each shot stays on screen before cutting |
| Transition | `fade` (quick dip to black between shots) or hard cuts |
| TTS engine / voice | Edge TTS (free) or OpenAI TTS; filter voices by locale; preview before rendering |
| Speed / voice volume | Speech rate and loudness |
| Background music | Random from `resource/songs/`, a specific track, an upload, or none; volume slider |
| Subtitles | Font, size, position, text/highlight/outline colours, outline width, max lines, bold, uppercase, word highlighting |

## Project layout

```
webui.py                  Streamlit UI
cli.py                    command-line interface
app/config.py             config.toml + env loading
app/models.py             VideoParams / SubtitleStyle
app/services/llm.py       multi-provider script + keyword generation
app/services/materials.py Pexels / Pixabay search, download cache, local files
app/services/voice.py     Edge / OpenAI TTS with word timings
app/services/subtitles.py cue building, SRT + styled ASS (karaoke) generation
app/services/video.py     FFmpeg clip normalising, concat, audio mix, subtitle burn-in
app/services/pipeline.py  orchestrates the whole run with progress reporting
storage/tasks/<id>/       per-run output: final.mp4, voice.mp3, subtitles.srt/.ass, script.txt, task.json
```

## Tests

```bash
pip install pytest
pytest -q
```

The suite includes an end-to-end render using synthetic footage and a stubbed voice, so it runs offline.

## Notes

* Stock clips are cached in `storage/cache_videos/`, so re-rendering a topic is fast.
* Pexels and Pixabay content is free to use under their licenses; check them before commercial use and credit creators where appropriate.
* Edge TTS uses Microsoft's public read-aloud service; it needs internet access and no key.
