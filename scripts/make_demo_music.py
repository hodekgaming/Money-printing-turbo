"""Synthesize a couple of soft, royalty-free ambient loops into resource/songs/
so background music works out of the box. Replace them with your own tracks
(e.g. from the YouTube Audio Library or Pixabay Music) for better results."""

import subprocess
import sys
from pathlib import Path

SONGS = Path(__file__).resolve().parent.parent / "resource" / "songs"

# (name, chord progression as lists of frequencies in Hz), 4 s per chord
TRACKS = {
    "demo_calm_pad": [[220.0, 277.18, 329.63], [196.0, 246.94, 293.66], [174.61, 220.0, 261.63], [196.0, 246.94, 329.63]],
    "demo_uplifting_pad": [[261.63, 329.63, 392.0], [293.66, 369.99, 440.0], [220.0, 277.18, 329.63], [246.94, 311.13, 392.0]],
}
CHORD = 4.0


def expr(chords):
    total = CHORD * len(chords)
    terms = []
    for i, chord in enumerate(chords):
        a, b = i * CHORD, (i + 1) * CHORD
        # smooth 0.8 s cross-fade envelope per chord
        env = f"(clip((t-{a})/0.8,0,1)*clip(({b}+0.8-t)/0.8,0,1))"
        tones = "+".join(f"sin(2*PI*{f}*t)+0.3*sin(2*PI*{f * 2}*t)" for f in chord)
        terms.append(f"if(between(t,{a},{b + 0.8}),{env}*({tones}),0)")
    # gentle tremolo
    return f"0.06*(1+0.15*sin(2*PI*0.25*t))*(" + "+".join(terms) + ")", total


def main() -> int:
    SONGS.mkdir(parents=True, exist_ok=True)
    for name, chords in TRACKS.items():
        e, total = expr(chords)
        out = SONGS / f"{name}.mp3"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"aevalsrc='{e}':s=44100:d={total}",
            "-af", "aecho=0.8:0.7:120|240:0.35|0.2,lowpass=f=3000,afade=t=in:d=0.5,loudnorm=I=-20",
            "-ac", "2", "-b:a", "160k", str(out),
        ], check=True)
        print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
