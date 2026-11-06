"""Parameter objects shared by the UI, CLI and pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class Aspect(str, Enum):
    PORTRAIT = "9:16"
    LANDSCAPE = "16:9"
    SQUARE = "1:1"

    @property
    def resolution(self) -> tuple[int, int]:
        return {
            Aspect.PORTRAIT: (1080, 1920),
            Aspect.LANDSCAPE: (1920, 1080),
            Aspect.SQUARE: (1080, 1080),
        }[self]

    @property
    def orientation(self) -> str:
        return {
            Aspect.PORTRAIT: "portrait",
            Aspect.LANDSCAPE: "landscape",
            Aspect.SQUARE: "square",
        }[self]


@dataclass
class SubtitleStyle:
    enabled: bool = True
    font: str = "DejaVu Sans"
    font_size: int = 72  # pixels, relative to the output resolution
    color: str = "#FFFFFF"
    highlight_color: str = "#FFD400"  # karaoke colour for the word being spoken
    karaoke: bool = True
    outline_color: str = "#000000"
    outline_width: float = 4.0
    shadow: float = 1.0
    bold: bool = True
    uppercase: bool = False
    position: str = "bottom"  # bottom | center | top
    margin_v: int = 0  # 0 = automatic
    max_chars_per_line: int = 0  # 0 = automatic based on resolution
    max_lines: int = 2


@dataclass
class VideoParams:
    topic: str = ""
    script: str = ""  # leave empty to have the LLM write it
    search_terms: list[str] = field(default_factory=list)  # empty -> LLM
    language: str = "English"
    paragraphs: int = 1
    duration_hint: int = 45  # target length in seconds for the script

    aspect: Aspect = Aspect.PORTRAIT
    material_source: str = "pexels"  # pexels | pixabay | local
    local_materials: list[str] = field(default_factory=list)
    max_clip_duration: int = 5
    transition: str = "fade"  # none | fade
    shuffle_clips: bool = True
    fps: int = 30

    tts_engine: str = "edge"  # edge | openai
    voice: str = "en-US-AriaNeural"
    voice_rate: float = 1.0  # 1.0 = normal
    voice_volume: float = 1.0

    bgm: str = "random"  # none | random | <path to file>
    bgm_volume: float = 0.15

    subtitle: SubtitleStyle = field(default_factory=SubtitleStyle)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["aspect"] = self.aspect.value
        return d
