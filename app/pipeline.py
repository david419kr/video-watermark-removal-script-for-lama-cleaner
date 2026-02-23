from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import shutil
import subprocess
import threading
from datetime import datetime
from typing import Callable, Optional

import requests

from .config import AppConfig, Paths
from .media_utils import get_video_info
from .models import ProcessConfig, Segment


REQUEST_DATA = {
    "ldmSteps": 25,
    "ldmSampler": "plms",
    "hdStrategy": "Original",
    "zitsWireframe": False,
    "hdStrategyCropMargin": 128,
    "hdStrategyCropTrigerSize": 512,
    "hdStrategyResizeLimit": 1280,
    "prompt": "",
    "negativePrompt": "",
    "useCroper": False,
    "croperX": 0,
    "croperY": 0,
    "croperHeight": 512,
    "croperWidth": 512,
    "sdScale": 1.0,
    "sdMaskBlur": 0,
    "sdStrength": 0.75,
    "sdSteps": 50,
    "sdGuidanceScale": 7.5,
    "sdSampler": "uni_pc",
    "sdSeed": 42,
    "sdMatchHistograms": False,
    "cv2Flag": "INPAINT_NS",
    "cv2Radius": 4,
    "paintByExampleSteps": 50,
    "paintByExampleGuidanceScale": 7.5,
    "paintByExampleMaskBlur": 0,
    "paintByExampleSeed": 42,
    "paintByExampleMatchHistograms": False,
    "paintByExampleExampleImage": "",
    "p2pSteps": 50,
    "p2pImageGuidanceScale": 7.5,
    "p2pGuidanceScale": 7.5,
    "controlnet_conditioning_scale": 0.4,
    "controlnet_method": "control_v11p_sd15_canny",
    "paint_by_example_example_image": "",
}


class PipelineCancelled(RuntimeError):
    pass


class VideoProcessingPipeline:
    def __init__(
        self,
        paths: Paths,
        log_cb: Callable[[str], None],
        progress_cb: Callable[[int, int], None],
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        self.paths = paths
        self.log = log_cb
        self.progress = progress_cb
        self.cancel_event = cancel_event or threading.Event()
        self._progress_lock = threading.Lock()
        self._done_count = 0
        self._total_count = 0

    def run(self, config: ProcessConfig) -> Path:
        self._ensure_preconditions(config)
        self._check_cancelled()

        video_info = get_video_info(self.paths.ffprobe, config.video_path)
        self.log(
            f"Video info: {video_info.width}x{video_info.height}, "
            f"{video_info.fps:.3f} fps, {video_info.duration_sec:.2f}s"
        )
        self._validate_segments(config.segments, video_info.duration_sec)

        job_root = self._prepare_job_folder()
        input_dir = job_root / "input"
        output_dir = job_root / "output"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        self._extract_frames(config.video_path, input_dir)
        self._check_cancelled()

        frame_files = self._collect_frames(input_dir)
        if not frame_files:
            raise RuntimeError("No frames extracted from input video.")

        self._total_count = len(frame_files)
        self._done_count = 0
        self.progress(0, self._total_count)

        masked_tasks, copied_count = self._prepare_tasks(
            frame_files=frame_files,
            output_dir=output_dir,
            fps=video_info.fps,
            segments=config.segments,
        )
        self._update_progress_bulk(copied_count)
        self.log(
            f"Frame dispatch: total={len(frame_files)}, "
            f"masked={len(masked_tasks)}, copied={copied_count}"
        )

        if masked_tasks:
            self._process_masked_tasks(masked_tasks, config.lama_ports)

        self._check_cancelled()
        merged_video = job_root / "video_cleaned.mp4"
        self._merge_frames(output_dir, merged_video, video_info.fps)
        self._check_cancelled()

        config.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._merge_audio(config.video_path, merged_video, config.output_path)
        self.log(f"Output saved: {config.output_path}")

        if not config.keep_temp:
            shutil.rmtree(job_root, ignore_errors=True)
            self.log(f"Temporary job folder removed: {job_root}")
        else:
            self.log(f"Temporary job folder kept: {job_root}")

        return config.output_path

    def _ensure_preconditions(self, config: ProcessConfig) -> None:
        if not self.paths.ffmpeg.exists():
            raise FileNotFoundError(f"ffmpeg not found: {self.paths.ffmpeg}")
        if not self.paths.ffprobe.exists():
            raise FileNotFoundError(f"ffprobe not found: {self.paths.ffprobe}")
        if not config.video_path.exists():
            raise FileNotFoundError(f"Video file not found: {config.video_path}")
        if not config.lama_ports:
            raise ValueError("No running lama-cleaner ports available.")
        for segment in config.segments:
            segment.validate()
            if segment.mask_path and not segment.mask_path.exists():
                raise FileNotFoundError(f"Segment mask not found: {segment.mask_path}")

    def _prepare_job_folder(self) -> Path:
        self.paths.workspace_jobs.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        job_root = self.paths.workspace_jobs / f"job-{timestamp}"
        job_root.mkdir(parents=True, exist_ok=True)
        return job_root

    def _extract_frames(self, video_path: Path, input_dir: Path) -> None:
        output_pattern = input_dir / "%d.jpg"
        self.log("Extracting frames...")

        cuda_cmd = [
            str(self.paths.ffmpeg),
            "-hwaccel",
            "cuda",
            "-i",
            str(video_path),
            "-q:v",
            "1",
            str(output_pattern),
        ]
        cpu_cmd = [
            str(self.paths.ffmpeg),
            "-i",
            str(video_path),
            "-q:v",
            "1",
            str(output_pattern),
        ]

        if self._has_cuda_hwaccel():
            self.log("Frame extraction: CUDA decode path.")
            if self._run_command(cuda_cmd, allow_fail=True):
                return
            self.log("CUDA extraction failed. Falling back to CPU path.")

        self.log("Frame extraction: CPU path.")
        ok = self._run_command(cpu_cmd, allow_fail=False)
        if not ok:
            raise RuntimeError("Frame extraction failed.")

    def _collect_frames(self, frame_dir: Path) -> list[Path]:
        frame_files = [path for path in frame_dir.iterdir() if path.is_file()]

        def sort_key(path: Path):
            try:
                return 0, int(path.stem)
            except ValueError:
                return 1, path.stem

        frame_files.sort(key=sort_key)
        return frame_files

    def _prepare_tasks(
        self,
        frame_files: list[Path],
        output_dir: Path,
        fps: float,
        segments: list[Segment],
    ) -> tuple[list[tuple[Path, Path, Path]], int]:
        masked_tasks: list[tuple[Path, Path, Path]] = []
        copied_count = 0

        sorted_segments = sorted(segments, key=lambda seg: seg.start_sec)

        for frame_path in frame_files:
            index = int(frame_path.stem) if frame_path.stem.isdigit() else None
            if index is None:
                raise RuntimeError(f"Unexpected frame filename: {frame_path.name}")

            time_sec = (index - 1) / fps
            mask_path = self._mask_for_time(time_sec, sorted_segments)
            output_path = output_dir / frame_path.name

            if mask_path is None:
                shutil.copy2(frame_path, output_path)
                copied_count += 1
            else:
                masked_tasks.append((frame_path, output_path, mask_path))

        return masked_tasks, copied_count

    @staticmethod
    def _mask_for_time(time_sec: float, segments: list[Segment]) -> Optional[Path]:
        for segment in segments:
            if segment.start_sec <= time_sec < segment.end_sec:
                return segment.mask_path
        return None

    def _process_masked_tasks(
        self,
        masked_tasks: list[tuple[Path, Path, Path]],
        lama_ports: list[int],
    ) -> None:
        self._check_cancelled()

        if not lama_ports:
            raise RuntimeError("Masked tasks exist but no lama-cleaner ports were provided.")

        self.log(f"Processing masked frames using {len(lama_ports)} lama-cleaner instance(s)...")
        assignments: list[list[tuple[Path, Path, Path]]] = [[] for _ in lama_ports]
        for index, task in enumerate(masked_tasks):
            assignments[index % len(lama_ports)].append(task)

        mask_cache: dict[Path, bytes] = {}
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=len(lama_ports)) as executor:
            future_map = {}
            for worker_idx, port in enumerate(lama_ports):
                assigned = assignments[worker_idx]
                if not assigned:
                    continue
                future = executor.submit(
                    self._worker_process_tasks,
                    worker_idx + 1,
                    port,
                    assigned,
                    mask_cache,
                )
                future_map[future] = (worker_idx + 1, port)

            for future in as_completed(future_map):
                worker_id, port = future_map[future]
                try:
                    future.result()
                except Exception as exc:
                    errors.append(f"worker {worker_id} (port {port}): {exc}")

        if errors:
            message = "\n".join(errors)
            raise RuntimeError(f"Masked frame processing failed:\n{message}")

    def _worker_process_tasks(
        self,
        worker_id: int,
        port: int,
        tasks: list[tuple[Path, Path, Path]],
        mask_cache: dict[Path, bytes],
    ) -> None:
        session = requests.Session()
        url = f"http://127.0.0.1:{port}/inpaint"
        self.log(f"Worker {worker_id} started on port {port}, frames={len(tasks)}.")

        for frame_path, output_path, mask_path in tasks:
            self._check_cancelled()

            if mask_path not in mask_cache:
                mask_cache[mask_path] = mask_path.read_bytes()

            image_bytes = frame_path.read_bytes()
            files = {
                "image": (frame_path.name, image_bytes, "image/jpeg"),
                "mask": ("mask.png", mask_cache[mask_path], "image/png"),
            }
            response = session.post(
                url,
                files=files,
                data=REQUEST_DATA,
                timeout=AppConfig.REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code != 200:
                text_preview = response.text.replace("\r", " ").replace("\n", " ")
                text_preview = text_preview[:240]
                raise RuntimeError(f"HTTP {response.status_code} {text_preview}")

            output_path.write_bytes(response.content)
            self._update_progress_one()

        self.log(f"Worker {worker_id} finished on port {port}.")

    def _merge_frames(self, output_dir: Path, merged_video: Path, fps: float) -> None:
        self.log("Merging cleaned frames...")
        input_pattern = output_dir / "%d.jpg"
        fps_text = f"{fps:.6f}"

        if self._has_nvenc():
            self.log("Frame merge: NVENC path.")
            nvenc_cmd = [
                str(self.paths.ffmpeg),
                "-framerate",
                fps_text,
                "-i",
                str(input_pattern),
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p5",
                "-rc",
                "vbr",
                "-cq",
                "7",
                "-b:v",
                "0",
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(merged_video),
            ]
            if self._run_command(nvenc_cmd, allow_fail=True):
                return
            self.log("NVENC merge failed. Falling back to libx264.")

        cpu_cmd = [
            str(self.paths.ffmpeg),
            "-framerate",
            fps_text,
            "-i",
            str(input_pattern),
            "-c:v",
            "libx264",
            "-crf",
            "7",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(merged_video),
        ]
        self._run_command(cpu_cmd, allow_fail=False)

    def _merge_audio(self, source_video: Path, merged_video: Path, output_path: Path) -> None:
        video_info = get_video_info(self.paths.ffprobe, source_video)
        if not video_info.has_audio:
            shutil.copy2(merged_video, output_path)
            self.log("Input video has no audio stream. Video output copied without audio merge.")
            return

        codec = video_info.audio_codec or "audio"
        ext = "ogg" if codec.startswith("vor") else codec[:3]
        audio_path = merged_video.parent / f"audio.{ext}"

        self.log(f"Extracting original audio stream ({codec})...")
        extract_audio_cmd = [
            str(self.paths.ffmpeg),
            "-i",
            str(source_video),
            "-vn",
            "-acodec",
            "copy",
            str(audio_path),
        ]
        self._run_command(extract_audio_cmd, allow_fail=False)

        self.log("Merging cleaned video + original audio...")
        merge_audio_cmd = [
            str(self.paths.ffmpeg),
            "-i",
            str(merged_video),
            "-i",
            str(audio_path),
            "-c:v",
            "copy",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-y",
            str(output_path),
        ]
        self._run_command(merge_audio_cmd, allow_fail=False)

    def _run_command(self, command: list[str], allow_fail: bool) -> bool:
        self._check_cancelled()
        self.log(f"$ {' '.join(command)}")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0:
            return True

        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        message = stderr if stderr else stdout
        self.log(f"Command failed ({result.returncode}): {message[:2000]}")
        if allow_fail:
            return False
        raise RuntimeError(message or f"Command failed with exit code {result.returncode}.")

    def _has_cuda_hwaccel(self) -> bool:
        result = subprocess.run(
            [str(self.paths.ffmpeg), "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        return "cuda" in text.lower()

    def _has_nvenc(self) -> bool:
        result = subprocess.run(
            [str(self.paths.ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        return "h264_nvenc" in text.lower()

    def _validate_segments(self, segments: list[Segment], duration_sec: float) -> None:
        ordered = sorted(segments, key=lambda seg: seg.start_sec)
        for seg in ordered:
            if seg.end_sec > duration_sec:
                raise ValueError(
                    f"Segment exceeds video duration: start={seg.start_sec:.3f}, end={seg.end_sec:.3f}, "
                    f"duration={duration_sec:.3f}"
                )

        for idx in range(1, len(ordered)):
            left = ordered[idx - 1]
            right = ordered[idx]
            if left.end_sec > right.start_sec:
                raise ValueError(
                    f"Overlapping segments detected: "
                    f"[{left.start_sec:.3f}, {left.end_sec:.3f}] overlaps "
                    f"[{right.start_sec:.3f}, {right.end_sec:.3f}]"
                )

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise PipelineCancelled("Operation cancelled by user.")

    def _update_progress_one(self) -> None:
        with self._progress_lock:
            self._done_count += 1
            self.progress(self._done_count, self._total_count)

    def _update_progress_bulk(self, amount: int) -> None:
        if amount <= 0:
            return
        with self._progress_lock:
            self._done_count += amount
            self.progress(self._done_count, self._total_count)
