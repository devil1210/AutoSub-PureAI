from dataclasses import dataclass, field
import threading
from typing import Callable, Optional

@dataclass
class SubtitleCue:
    index: int
    start_time: str
    end_time: str
    style: str = "Default"
    speaker: str = ""
    raw_line: str = ""
    raw_text: str = ""
    masked_text: str = ""
    tag_map: dict[str, str] = field(default_factory=dict)
    duration_sec: float = 0.0
    is_translatable: bool = True

@dataclass
class ParsedSubtitle:
    format: str  # 'ass' or 'srt'
    headers: list[str] = field(default_factory=list)
    cues: list[SubtitleCue] = field(default_factory=list)

@dataclass
class CharacterProfile:
    name: str
    gender: str = "Desconocido"
    personality: str = ""
    speech_register: str = "neutral"
    honorifics: list[str] = field(default_factory=list)

@dataclass
class SeriesProfile:
    series_title: str
    official_title: str = ""
    setting: str = ""
    tone_instructions: str = ""
    characters: list[CharacterProfile] = field(default_factory=list)
    glossary: dict[str, str] = field(default_factory=dict)
    episodes: list[dict] = field(default_factory=list)

@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_sec: float = 0.0
    model_used: str = ""

@dataclass
class JobContext:
    video_path: str
    series_name: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    progress_callback: Optional[Callable[[int, int, str], None]] = None
