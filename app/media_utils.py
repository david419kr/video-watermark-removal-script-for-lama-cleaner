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

    audio_codec = _probe_audio_codec(ffprobe_path, video_path)
    return VideoInfo(
        duration_sec=float(duration),
        fps=parse_fps(fps),
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
