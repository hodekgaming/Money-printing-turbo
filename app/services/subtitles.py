"""Turn word timings into styled subtitles (ASS for burning in, SRT for upload)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import SubtitleStyle
from app.services.voice import Word


@dataclass
class Cue:
    start: float
    end: float
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def auto_chars_per_line(width: int, font_size: int) -> int:
    # Average glyph width is ~0.55em for typical sans fonts; keep a side margin.
    return max(10, int((width * 0.86) / (font_size * 0.55)))


def build_cues(words: list[Word], max_chars: int, max_lines: int = 2, max_gap: float = 0.6) -> list[Cue]:
    """Group words into on-screen cues of at most ``max_lines`` x ``max_chars``.

    A new cue starts at sentence ends, on long pauses, or when the cue is full.
    """
    cues: list[Cue] = []
    cur: list[Word] = []
    budget = max_chars * max_lines

    def flush():
        if cur:
            cues.append(Cue(cur[0].start, cur[-1].end, list(cur)))
            cur.clear()

    for w in words:
        if cur:
            length = len(" ".join(x.text for x in cur)) + 1 + len(w.text)
            gap = w.start - cur[-1].end
            if length > budget or gap > max_gap:
                flush()
        cur.append(w)
        if re.search(r"[.!?…]['\"”’)]*$", w.text):
            flush()
    flush()

    # Extend each cue up to the next one so the text doesn't flicker between words.
    for a, b in zip(cues, cues[1:]):
        if b.start - a.end < 0.5:
            a.end = b.start
        else:
            a.end += 0.3
    if cues:
        cues[-1].end += 0.4
    return cues


def wrap_words(words: list[Word], max_chars: int) -> list[list[Word]]:
    """Split a cue's words into balanced lines."""
    total = len(" ".join(w.text for w in words))
    if total <= max_chars:
        return [words]
    n_lines = -(-total // max_chars)
    target = total / n_lines
    lines: list[list[Word]] = [[]]
    length = 0
    for w in words:
        add = len(w.text) + (1 if lines[-1] else 0)
        if lines[-1] and length + add > target + 2 and len(lines) < n_lines:
            lines.append([])
            length = 0
            add = len(w.text)
        lines[-1].append(w)
        length += add
    return lines


# --------------------------------------------------------------------------- #
# SRT
# --------------------------------------------------------------------------- #


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: list[Cue], max_chars: int) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        text = "\n".join(" ".join(w.text for w in line) for line in wrap_words(c.words, max_chars))
        out.append(f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{text}\n")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# ASS
# --------------------------------------------------------------------------- #


def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_color(hex_color: str, alpha: int = 0) -> str:
    """'#RRGGBB' -> ASS '&HAABBGGRR'."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def to_ass(cues: list[Cue], style: SubtitleStyle, width: int, height: int, max_chars: int) -> str:
    alignment = {"bottom": 2, "center": 5, "top": 8}.get(style.position, 2)
    margin_v = style.margin_v or (int(height * 0.18) if style.position == "bottom" else int(height * 0.10))
    if style.position == "center":
        margin_v = 0
    primary = style.highlight_color if style.karaoke else style.color
    # In karaoke mode, \k sweeps from SecondaryColour (not yet spoken) to PrimaryColour (spoken).
    secondary = style.color
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style.font},{style.font_size},{ass_color(primary)},{ass_color(secondary)},{ass_color(style.outline_color)},{ass_color("#000000", 0x80)},{-1 if style.bold else 0},0,0,0,100,100,0,0,1,{style.outline_width},{style.shadow},{alignment},{int(width * 0.07)},{int(width * 0.07)},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines_out = []
    for c in cues:
        lines = wrap_words(c.words, max_chars)
        text = _karaoke_text(c, lines, style) if style.karaoke else "\\N".join(
            " ".join(_escape(_case(w.text, style)) for w in line) for line in lines
        )
        lines_out.append(f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,{text}")
    return header + "\n".join(lines_out) + "\n"


def _case(text: str, style: SubtitleStyle) -> str:
    return text.upper() if style.uppercase else text


def _cs(t: float) -> int:
    return int(round(t * 100))


def _karaoke_text(c: Cue, lines: list[list[Word]], style: SubtitleStyle) -> str:
    """Karaoke markup where each \\k spans from a word's start to the next word's start,
    computed on absolute centiseconds so rounding never drifts from the voice."""
    flat = [w for line in lines for w in line]
    breaks, idx = set(), 0
    for line in lines[:-1]:
        idx += len(line)
        breaks.add(idx)
    out: list[str] = []
    cursor = _cs(c.start)
    for i, w in enumerate(flat):
        s = _cs(w.start)
        if s > cursor:
            out.append(f"{{\\k{s - cursor}}}")
            cursor = s
        end = _cs(flat[i + 1].start) if i + 1 < len(flat) else _cs(w.end)
        k = max(1, end - cursor)
        out.append(f"{{\\k{k}}}{_escape(_case(w.text, style))}")
        cursor += k
        if i + 1 in breaks:
            out.append("\\N")
        elif i + 1 < len(flat):
            out.append(" ")
    return "".join(out)
