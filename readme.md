# Video Watermark Removal Script (Lama Cleaner)

Windows-focused workflow for removing a watermark from a video by:
1) extracting frames,
2) inpainting each frame through `lama-cleaner`,
3) merging cleaned frames and original audio back into one output video.

This project is currently designed and tested for NVIDIA + CUDA environments.

## What Is Included

- `1.start-lama-cleaner.bat`
  - Sets up a local embedded Python runtime (`.runtime/python310`) automatically.
  - Installs required packages (including CUDA PyTorch) if needed.
  - Starts one or more `lama-cleaner` instances on sequential ports (`8080+`).
- `2.run-video-cleaning.bat`
  - Runs the full video cleaning pipeline.
  - Detects active `lama-cleaner` instances from port `8080` upward.
  - Splits frame inpainting workload evenly across detected instances.
- `scripts/*.ps1` + `scripts/batch.py`
  - Audio extract / frame extract / frame clean / frame merge / audio merge / cleanup.

## Requirements

- Windows PowerShell environment.
- NVIDIA GPU + CUDA-capable driver (recommended).
- Bundled `ffmpeg` folder present in this repository.
- Input files in repository root:
  - `video.mp4`
  - `mask.png`

Important:
- `mask.png` must be the exact same resolution as `video.mp4`.
- Input filename must be exactly `video.mp4`.

## Quick Start

1. Put your files in the project root:
   - `video.mp4`
   - `mask.png` (black background + white masked area)
2. Run `1.start-lama-cleaner.bat`.
3. Enter how many `lama-cleaner` instances to run.
   - Press Enter for default `1`.
   - Example: `3` starts ports `8080`, `8081`, `8082`.
4. Run `2.run-video-cleaning.bat`.
5. Final output is written to:
   - `output/video_final.mp4`

## Installation Details (Automatic)

`1.start-lama-cleaner.bat` builds an isolated local runtime and installs:

- Python `3.10.11` (embedded build)
- `torch==2.10.0`
- `torchvision==0.25.0`
- `torchaudio==2.10.0`
- `lama-cleaner`
- `huggingface_hub==0.14.1`

PyTorch is installed from:
- `https://download.pytorch.org/whl/cu128`

The setup writes a marker file in `.runtime` after successful verification, so later runs skip reinstall unless verification fails.

## Multi-Instance Behavior

- `2.run-video-cleaning.bat` probes from port `8080` upward and counts sequential active `lama-cleaner` listeners.
- Frame inpainting tasks are split as evenly as possible across detected instances.
- Example:
  - 3 instances detected (`8080/8081/8082`)
  - frames are split into 3 near-equal chunks
  - all chunks are processed in parallel

## GPU Acceleration

### Frame Extract (`scripts/02_frame_extract.ps1`)
- Tries CUDA decode path:
  - `ffmpeg -hwaccel cuda ...`
- Falls back to CPU decode automatically if CUDA path fails.

### Frame Merge (`scripts/04_frame_merge.ps1`)
- Tries NVIDIA NVENC encode path:
  - `-c:v h264_nvenc -preset p5 -rc vbr -cq 7 -b:v 0`
- Falls back to CPU `libx264 -crf 7` automatically if NVENC path fails.
- Quality target is tuned to stay close to previous `libx264 -crf 7` behavior.

## Processing Flow

1. Extract original audio stream to `temp/audio.*`
2. Extract frames to `temp/input/`
3. Inpaint frames via `lama-cleaner` API (`/inpaint`) into `temp/output/`
4. Merge cleaned frames into `temp/video_cleaned.mp4`
5. Merge original audio into `output/video_final.mp4`
6. Optional cleanup (`temp/`) prompt at the end

## Troubleshooting

- `No running lama-cleaner instance detected from port 8080`
  - Start instances first with `1.start-lama-cleaner.bat`.
- `video and mask resolution must be exact same`
  - Resize or recreate mask to match input video exactly.
- Inpainting errors on specific worker/port
  - Check that every launched `lama-cleaner` instance is still alive.
  - Reduce instance count if GPU memory is insufficient.
- Slow processing
  - Launch multiple instances (if VRAM allows).
  - Ensure CUDA/NVENC are available in your ffmpeg build.
