# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file desktop video trimmer that wraps `ffmpeg`/`ffprobe`:

- `videotrim.py` — Python + PyQt6 (~1400 lines)

## Commands

```bash
pip install PyQt6 PyQt6-Qt6 PyQt6-sip
python videotrim.py
```

### Standalone executables (PyInstaller, bundles ffmpeg/ffprobe)
```bash
./build-windows.ps1   # -> build\windows-python\dist\VideoTrim\VideoTrim.exe
./build-macos.sh      # -> build/macos-python/dist/VideoTrim.app
```
Both build scripts require `ffmpeg`/`ffprobe` on PATH; they locate them via `Get-Command`/`command -v` and bundle them with `--add-binary "<path>;ffmpeg"` (`:` separator on macOS).

There are **no tests** and no linter configured.

## Core behavior

**Encoder detection** — at startup, run `ffmpeg -encoders` and string-match against the fixed `HW_ENCODERS` table (top of [videotrim.py](videotrim.py)): `h264/hevc_videotoolbox` (macOS), `h264/hevc/av1_qsv` (Intel QSV), `h264/hevc/av1_amf` (AMD AMF). Only matched encoders appear in the UI. "Stream Copy" is always present.

**Quality flags** — centralized in `_encoder_quality_args(enc)`; each vendor uses a different rate-control knob: videotoolbox `-q:v 65`, qsv `-global_quality 18`, amf `-rc cqp -qp_i 18 -qp_p 18`. Both trim sites (BulkDialog and main window) call this helper — keep it the single source of truth when adding encoders.

**Trim ffmpeg args** — two distinct paths:
- **Copy mode**: `-ss`/`-to` *before* `-i` (input seeking) → fast, cuts to nearest keyframe, `-c copy`.
- **Re-encode mode**: `-ss`/`-to` *after* `-i` (output seeking) → frame-accurate. Audio re-encoded to `aac`.
- Both use `-map 0` and `-avoid_negative_ts make_zero`. Output must not equal input path.

**Freeze detection ("Detect" button + Bulk Trim)** — runs `ffmpeg -vf freezedetect=n=-40dB:d=0` and parses stderr for `freeze_start`/`freeze_end`. Logic in `_parse_initial_freeze_end`: only treats a freeze as the intro if `freeze_start < 0.5` (begins at/near t=0); the matching `freeze_end` becomes the suggested start time (first real frame change). The `-40dB` noise threshold is intentionally low-sensitivity.

## Architecture notes

- ffmpeg/ffprobe path resolution (`_find_tool`) checks, in order: PyInstaller bundle dirs (`sys._MEIPASS`, `_internal/ffmpeg`, macOS `Resources`/`Frameworks`), then falls back to PATH. Edit this when changing bundling.
- Long-running ffmpeg calls use `QProcess` (async, non-blocking UI), not `subprocess`. Output accumulates in `self._process_output` / `self._detect_output` and is parsed on the `finished` signal.
- `BulkDialog` (QDialog) drives a per-file state machine: detect freeze → trim → next, with per-item status colouring.
- `VideoTrimWindow` is built by `_build_*` methods; preview via `QMediaPlayer` + `QVideoWidget`. Preview failure is non-fatal (ffmpeg still trims formats the OS can't decode).

## Conventions
- `.gitattributes` enforces **LF line endings repo-wide** — do not introduce CRLF.
- Times in the UI/params are `HH:MM:SS.mmm` (millisecond precision), passed straight to ffmpeg `-ss`/`-to`.
- Output filename auto-derived with `_trimmed` suffix; existing-file overwrite is confirmed in the UI.
