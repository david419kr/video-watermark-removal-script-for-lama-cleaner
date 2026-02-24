from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Optional

from .models import VideoInfo


def _run_capture(command: list[str]) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{stderr}")
    return (result.stdout or "").strip()


def parse_fps(value: str) -> float:
    value = value.strip()
    if not value:
        raise ValueError("Empty FPS value.")
    if "/" in value:
        left, right = value.split("/", 1)
        denominator = float(right)
        if denominator == 0:
            raise ValueError("FPS denominator is zero.")
        return float(left) / denominator
    return float(value)


def format_seconds(seconds: float) -> str:
    total_ms = int(max(0, seconds) * 1000)
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def parse_time_text(value: str) -> float:
    raw = value.strip()
    if not raw:
        raise ValueError("Empty time text.")

    if ":" not in raw:
        return float(raw)

    parts = raw.split(":")
    if len(parts) == 2:
        minutes = int(parts[0])
        sec = float(parts[1])
        return (minutes * 60) + sec
    if len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        sec = float(parts[2])
        return (hours * 3600) + (minutes * 60) + sec
    raise ValueError(f"Invalid time text: {value}")


def frame_to_seconds(frame_index: int, fps: float) -> float:
    frame = max(1, frame_index)
    return (frame - 1) / fps


def frame_to_text(frame_index: int, fps: float) -> str:
    seconds = frame_to_seconds(frame_index, fps)
    return f"{frame_index} ({format_seconds(seconds)})"


def seconds_to_frame(seconds: float, fps: float, total_frames: int) -> int:
    frame = int(round(max(0.0, seconds) * fps)) + 1
    return max(1, min(total_frames, frame))


def ms_to_frame(ms: int, fps: float, total_frames: int) -> int:
    return seconds_to_frame(ms / 1000.0, fps, total_frames)


def frame_to_ms(frame_index: int, fps: float) -> int:
    return int(round(frame_to_seconds(frame_index, fps) * 1000))


def _probe_total_frames(ffprobe_path: Path, video_path: Path) -> Optional[int]:
    text = _run_capture(
        [
            str(ffprobe_path),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_frames",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
    )
    try:
        value = int(text.strip())
        if value > 0:
            return value
    except Exception:
        pass
    return None


def get_video_info(ffprobe_path: Path, video_path: Path) -> VideoInfo:
    duration = _run_capture(
        [
            str(ffprobe_path),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
    )

    fps = _run_capture(
        [
            str(ffprobe_path),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
    )

    resolution = _run_capture(
        [
            str(ffprobe_path),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(video_path),
        ]
    )
    width_raw, height_raw = resolution.split("x")

    total_frames = _probe_total_frames(ffprobe_path, video_path)
    if total_frames is None:
        total_frames = max(1, int(round(float(duration) * parse_fps(fps))))

    audio_codec = _probe_audio_codec(ffprobe_path, video_path)
    return VideoInfo(
        duration_sec=float(duration),
        fps=parse_fps(fps),
        total_frames=total_frames,
        width=int(width_raw),
        height=int(height_raw),
        has_audio=bool(audio_codec),
        audio_codec=audio_codec,
    )


def _probe_audio_codec(ffprobe_path: Path, video_path: Path) -> Optional[str]:
    result = subprocess.run(
        [
            str(ffprobe_path),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    codec = (result.stdout or "").strip()
    return codec or None


def extract_reference_frame(
    ffmpeg_path: Path,
    video_path: Path,
    time_sec: float,
    output_path: Path,
) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(ffmpeg_path),
            "-ss",
            f"{max(0.0, time_sec):.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0
