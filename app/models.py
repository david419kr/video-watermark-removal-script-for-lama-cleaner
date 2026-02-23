from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import uuid


@dataclass
class Segment:
    start_frame: int
    end_frame: int
    mask_path: Optional[Path] = None
    segment_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def validate(self) -> None:
        if self.start_frame < 1:
            raise ValueError("Segment start frame must be >= 1.")
        if self.end_frame < self.start_frame:
            raise ValueError("Segment end frame must be >= start frame.")


@dataclass
class VideoInfo:
    duration_sec: float
    fps: float
    total_frames: int
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
