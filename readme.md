# Lama Cleaner Video GUI

[한국어 README 보기](readme.ko.md)  
Get on Releases: [GUI Release ZIP](https://github.com/david419kr/lama-cleaner-video-gui/releases/download/v0.1.0/gui-release-v0.1.0.zip)

Legacy CLI/script version is preserved in `legacy/cli-script`.

Native Windows GUI for segment-based video watermark/object removal using `lama-cleaner`.

This repository is now GUI-first. Legacy batch/PowerShell pipeline flow is not used.

<img width="1185" height="1100" alt="image" src="https://github.com/user-attachments/assets/fb17ddd6-9356-4db7-bfb0-43568aedce69" />

## Highlights
- Frame-accurate timeline workflow
  - left/right key frame stepping
  - held key = accelerated stepping
  - hover preview on seekbar
- Segment editor with frame/time editing
- Per-segment mask workflow
  - assign external mask file
  - draw mask in built-in mask editor (brush, eraser, rectangle, ellipse)
- Multi-instance `lama-cleaner` orchestration from GUI (base port `8080`, sequential)
- Pause/Resume processing with persisted job state

## System Requirements

- Windows 10/11 (x64)
- NVIDIA GPU + CUDA-capable driver (required for `--device=cuda`)
- Internet access on first setup (Python/pip/packages/model download)

## Quick Start

From repo root:

```bat
start_gui.bat
```

### What `start_gui.bat` does

On first run (or when runtime is missing), it bootstraps everything automatically:

1. Downloads and extracts embedded Python `3.10.11` into `.runtime/python310`
2. Enables `site-packages` in embedded Python
3. Installs `pip` via `get-pip.py`
4. Installs GUI requirements from `requirements.txt`
5. Validates or installs `lama-cleaner` runtime:
   - `torch==2.10.0`, `torchvision==0.25.0`, `torchaudio==2.10.0` from `cu128` index
   - `lama-cleaner`
   - force pins `huggingface_hub==0.14.1` for compatibility
6. Verifies `lama-cleaner` command availability
7. Launches `main.py` using embedded Python

If runtime is already valid, installation is skipped.

## GUI Workflow

1. Load video
   - `Browse Video`, or drag-and-drop into the app window.
2. Define segment range
   - set `Start Frame` / `End Frame` directly
   - or use `Set Start = Current Frame` / `Set End = Current Frame`
   - click `Add Segment`
3. Configure per-segment mask
   - folder icon: assign existing mask file
   - pencil icon: draw mask in editor
   - red `X`: remove segment row
4. Set `Instances` and click `Apply Instance Count`
5. Click `Start Processing`

## Segment and Mask Rules

- Segments must not overlap.
- Frame handling:
  - frame in segment + mask: sent to `lama-cleaner`
  - frame in segment + no mask: copied (skip inpaint)
  - frame outside all segments: copied
- Built-in mask editor saves binary PNG masks (black/white only).
- External mask files should also be binary-style masks for predictable results.

## Processing State Model

- `Start Processing` switches to `Pause Processing` while running.
- `Cancel` cancels current run.
- On pause:
  - in-flight requests are allowed to finish safely
  - resumable job state is saved to `workspace/paused_job.json`
  - next app launch auto-restores paused job and button becomes `Resume Processing`

## Multi-Instance Behavior

- Instance count range: `1` to `8`
- Base port: `8080`
- Sequential allocation: `8080`, `8081`, `8082`, ...
- Instance count preference is persisted in `workspace/ui_settings.json`
- App auto-starts configured instances on launch and stops managed instances on exit

## Performance Notes

- Frame extraction:
  - tries CUDA decode path first
  - falls back to CPU if unavailable/fails
- Frame merge:
  - tries `h264_nvenc` first
  - falls back to `libx264` if unavailable/fails
- Masked frames are distributed across active `lama-cleaner` ports for parallel processing.

## Workspace Layout

- `workspace/jobs/job-*/input` extracted frames
- `workspace/jobs/job-*/output` cleaned/copied frames
- `workspace/jobs/job-*/video_cleaned.mp4` merged video before audio remux
- `workspace/masks/` generated masks/reference frames
- `workspace/lama_logs/` `lama-cleaner` startup/runtime logs
- `workspace/ui_settings.json` persisted UI preferences
- `workspace/paused_job.json` paused/resumable job metadata

## Troubleshooting

- `Failed to start lama-cleaner automatically`
  - check `workspace/lama_logs/*.log`
  - first launch can take longer due model initialization/download
- `Cannot start lama-cleaner on port 8080`
  - another process is already using the port
  - free the port or reduce instance count
- `No lama-cleaner instances running`
  - click `Apply Instance Count` first
- `ffmpeg not found` / `ffprobe not found`
  - verify `ffmpeg/bin/ffmpeg.exe` and `ffmpeg/bin/ffprobe.exe` exist

## Clean Reset

To fully reset local runtime and job state:

1. Close the app
2. Delete `.runtime/`
3. Delete `workspace/`
4. Run `start_gui.bat` again
