"""Command-line entry point.

Examples:
    python cli.py "the benefits of reading"
    python cli.py "5 facts about octopuses" --aspect 16:9 --voice en-GB-RyanNeural --bgm-volume 0.1
    python cli.py --script-file my_script.txt --terms "ocean,coral reef,diver" --source pixabay
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.config import load_config
from app.models import Aspect, SubtitleStyle, VideoParams
from app.services import pipeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate a short social video from a topic.")
    ap.add_argument("topic", nargs="?", default="", help="topic or keyword")
    ap.add_argument("--script-file", help="use this script instead of generating one")
    ap.add_argument("--terms", default="", help="comma-separated footage search terms")
    ap.add_argument("--language", default="English")
    ap.add_argument("--length", type=int, default=45, help="target length in seconds")
    ap.add_argument("--aspect", choices=[a.value for a in Aspect], default="9:16")
    ap.add_argument("--source", choices=["pexels", "pixabay", "local"], default="pexels")
    ap.add_argument("--local", nargs="*", default=[], help="local clips/images (with --source local)")
    ap.add_argument("--clip-seconds", type=int, default=4)
    ap.add_argument("--transition", choices=["fade", "none"], default="fade")
    ap.add_argument("--tts", choices=["edge", "openai"], default="edge")
    ap.add_argument("--voice", default="en-US-AriaNeural")
    ap.add_argument("--rate", type=float, default=1.1)
    ap.add_argument("--bgm", default="random", help="'random', 'none' or a path to an audio file")
    ap.add_argument("--bgm-volume", type=float, default=0.15)
    ap.add_argument("--no-subtitles", action="store_true")
    ap.add_argument("--font", default="DejaVu Sans")
    ap.add_argument("--font-size", type=int, default=72)
    ap.add_argument("--sub-position", choices=["bottom", "center", "top"], default="bottom")
    ap.add_argument("--no-karaoke", action="store_true")
    ap.add_argument("--provider", help="override the LLM provider from config.toml")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    if args.provider:
        cfg["llm"]["provider"] = args.provider

    script = open(args.script_file, encoding="utf-8").read() if args.script_file else ""
    if not args.topic and not script:
        ap.error("give a topic or --script-file")

    params = VideoParams(
        topic=args.topic,
        script=script,
        search_terms=[t.strip() for t in args.terms.split(",") if t.strip()],
        language=args.language,
        duration_hint=args.length,
        aspect=Aspect(args.aspect),
        material_source=args.source,
        local_materials=args.local,
        max_clip_duration=args.clip_seconds,
        transition=args.transition,
        tts_engine=args.tts,
        voice=args.voice,
        voice_rate=args.rate,
        bgm=args.bgm,
        bgm_volume=args.bgm_volume,
        subtitle=SubtitleStyle(
            enabled=not args.no_subtitles, font=args.font, font_size=args.font_size,
            position=args.sub_position, karaoke=not args.no_karaoke,
        ),
    )

    def progress(frac: float, msg: str) -> None:
        print(f"[{frac * 100:5.1f}%] {msg}", flush=True)

    result = pipeline.run(params, cfg, progress)
    print(f"\nTitle: {result.title}\nVideo: {result.video_path}\nSubtitles: {result.srt_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
