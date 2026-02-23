from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import uuid


@dataclass
class Segment:
    start_sec: float
    end_sec: float
    mask_path: Optional[Path] = None
    segment_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def validate(self) -> None:
        if self.start_sec < 0:
            raise ValueError("Segment start must be >= 0.")
        if self.end_sec <= self.start_sec:
            raise ValueError("Segment end must be greater than start.")


@dataclass
class VideoInfo:
    duration_sec: float
    fps: float
    width: int
    height: int
    has_audio: bool
    audio_codec: Optional[str]


@dataclass
class ProcessConfig:
    video_path: Path
    output_path: Path
    segments: list[Segment]
    lama_ports: list[int]
    keep_temp: bool = False
