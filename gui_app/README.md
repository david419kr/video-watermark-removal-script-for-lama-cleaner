# GUI App (refactor/gui)

This folder contains the new native GUI implementation path (PySide6) without modifying the legacy batch workflow.

## Current Scope

- Native desktop GUI (`PySide6`)
- Select input video/output file from UI
- Video preview + timeline
- Segment-based processing setup
- Segment mask assignment via:
  - file upload
  - built-in drawing tool
- lama-cleaner instance lifecycle managed by app
  - auto-start one instance on app startup
  - adjust instance count from GUI
  - cleanup on app close
- Segment-aware processing:
  - frames inside masked segments -> inpaint
  - frames outside masked segments -> copied (skip inpaint)

## Not Implemented Yet (by design)

- Real-time rendered output preview while processing
- Automatic AI watermark detection + auto mask generation

The architecture keeps these as future extensions.

## Run

From repository root:

```bat
gui_app\start_gui.bat
```

Or manually:

```bat
python -m pip install -r gui_app\requirements.txt
python gui_app\main.py
```
