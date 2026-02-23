# Lama Cleaner Video GUI (refactor/gui)

This branch is GUI-first and no longer uses the legacy batch/PowerShell workflow.

## Overview

- Native desktop GUI built with `PySide6`
- User selects input/output video paths in UI
- Video preview + timeline-based segment setup
- Segment mask assignment:
  - upload mask file
  - draw mask in built-in editor
- App-managed `lama-cleaner` lifecycle:
  - auto-start default instance on GUI startup
  - scale instance count from UI
  - stop managed instances on app close
- Segment-aware processing:
  - frames in masked segments -> inpaint
  - frames outside configured segments -> copied without inpaint

## Project Structure

- `main.py`
- `start_gui.bat`
- `requirements.txt`
- `app/`
  - `main_window.py`
  - `pipeline.py`
  - `mask_editor.py`
  - `lama_manager.py`
  - `media_utils.py`
  - `models.py`
  - `config.py`
- `ffmpeg/` (bundled binaries)
- `workspace/` (runtime artifacts; not committed)

## Run

From repository root:

```bat
start_gui.bat
```

Or manually:

```bat
python -m pip install -r requirements.txt
python main.py
```

## Notes

- For local embedded runtime, `.runtime/python310/python.exe` is used automatically if present.
- If `lama-cleaner` is unavailable, initialize runtime/package setup first (legacy setup script from other branches can still be referenced).
- Expansion features (real-time output preview and automatic watermark detection) are intentionally deferred in this branch.
