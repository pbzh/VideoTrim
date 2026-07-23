# VideoTrim — Session Handoff

Context handoff so a fresh Claude Code session (esp. on Windows) can continue
seamlessly. Branch: **`feat/amd-hw-smart-trim`** (tip `05b48fa`).

## What this project is

Simple, fast desktop video trimmer with **minimal quality loss**, built on
ffmpeg. Two implementations exist:

- **`videotrim.py`** — PyQt6 app. **This is the active, most-featured impl** and
  the one to work on. Cross-platform (macOS + Windows).
- **`videotrim-wails/`** — older Go + Wails app. Left as-is, not being developed.
  Do not invest here unless explicitly asked.

The app is a thin GUI wrapper; all heavy lifting is ffmpeg + the GPU hardware
encoder. Language/perf of the wrapper is irrelevant.

## Why we're on Windows now

User's MacBook M2 is underpowered for batch-processing many video files. Moving
to a Windows box with an **AMD GPU** to use the **AMF hardware encoder**. The
Python app was made cross-platform + AMD-aware for this.

## Current state of `videotrim.py` (all verified on macOS)

Single-file PyQt6 app. Key pieces:

- **Tool resolution** (`find_tool`, `FFMPEG`, `FFPROBE`): platform-aware
  (`ffmpeg.exe` on Windows), prefers a bundled binary next to the exe /
  `_MEIPASS`, else PATH, else common install dirs. Windows console-flash
  suppressed via `CREATE_NO_WINDOW` in `spawn()`.
- **Encoders** (`available_encoders`, `quality_args`, `smart_fallback_encoder`):
  list is availability-filtered against `ffmpeg -encoders`.
  - macOS → VideoToolbox (`h264/hevc_videotoolbox`), quality `-q:v 80`.
  - Windows AMD → AMF (`h264/hevc/av1_amf`), quality `-rc cqp -qp_i 16 -qp_p 16
    -quality quality` (no `-qp_b`; av1_amf rejects it).
  - Windows Intel → QSV (`h264/hevc/av1_qsv`), quality `-global_quality 16`.
  - Fallback software `libx264` `-crf 16 -preset medium`.
  - `smart_fallback_encoder`: darwin→videotoolbox, else→amf then qsv, else x264.
- **Smart mode** (`build_trim_args`): lossless stream-copy when the start lands
  on a keyframe (`start_on_keyframe` via ffprobe), else a frame-accurate
  near-lossless HW re-encode. `EndTime` optional ("" = trim to EOF).
- **Trim** (`trim_video`): builds args, replace-source writes a `.vt_tmp` sibling
  then `os.replace()` (atomic) over the source. Returns `(ok, message)`.
- **Detect Start** (`detect_first_change`): streams `freezedetect=n=-40dB:d=1`
  and kills ffmpeg at the first `freeze_end` — fast, only decodes to first
  frame change. `d=1` ⇒ ignores freezes < 1 second.
- **Folder Freeze Scan** (`ScanThread`, `_scan_one`): concurrent (≤6 workers)
  freezedetect over first N seconds of every video in a folder; classifies
  frozen-intro / first-change / freeze length. `>HH:MM:SS` means frozen for the
  whole scanned window. Results shown in `ScanDialog` (table + per-file
  checkboxes + select-all).
- **Batch trim** (`BatchThread`): trims each selected file from its detected
  start to EOF, **replacing the source**, sequentially, cancelable.
- **ffmpeg log window** (`LogBus`, `LogWindow`, View menu / Ctrl+L): thread-safe
  log of every ffmpeg/ffprobe command (labelled probe/encoders/keyframe/detect/
  scan/trim) + a one-line file summary (codec, WxH, fps, duration, size) +
  result. Off until enabled. Clear / Save.
- **Stop All ffmpeg** button + global process registry (`spawn`/`_release`,
  `_PROCS`) and worker registry (`register_worker`, `stop_all()`): one click
  kills every running ffmpeg and cancels scan/batch threads.

## Build / run

- Deps: `pip install PyQt6` and ffmpeg on PATH (or bundled). Run:
  `python videotrim.py`.
- **Windows exe**: `./build-windows.ps1` (PyInstaller, `--onedir`, bundles
  ffmpeg/ffprobe at the app root so `find_tool` picks them up). Output:
  `build\windows-python\dist\VideoTrim\VideoTrim.exe`.
- **macOS .app**: `./build-macos.sh` (PyInstaller, bundles ffmpeg at app root).

## VERIFIED vs NOT

Verified on macOS: encoder detection (VT only, AMF/QSV correctly filtered out),
detect (2.0s intro → `00:00:02.000`, moving clip → none), replace-source trim
(5s→3s, no leftover temp), scan classification, batch trim (2/2, sources
replaced), log capture (command + file summary + result), Stop All (kills a live
ffmpeg, returncode -9), GUI launches.

**NOT yet tested on real hardware — do this first on Windows:**
- AMD **AMF** encode path (`h264_amf` etc.). Syntax is standard but unproven on a
  real AMD GPU. Open the ffmpeg log window (Ctrl+L), run a Smart re-encode on a
  non-keyframe start, confirm the `-c:v h264_amf -rc cqp ...` command succeeds
  and output plays. If AMF errors, likely culprits: driver/ffmpeg build lacks
  AMF, or a param needs tweaking for your GPU.
- Windows preview: Qt multimedia backend differs; MP4/MOV should preview, others
  fall back to "trim still works".
- Windows console-flash suppression and bundled-ffmpeg lookup in a PyInstaller
  build.

## IMPORTANT: `main` has divergent parallel work

`origin/main` is **ahead of this branch by 2 commits the branch does NOT have**:
- `e31fbb3` "Add AMD AMF encoders, Windows installer, drop Wails impl"
- `d2d1595` "Use absolute paths for build assets"

The user independently added AMD AMF + a **Windows installer** + dropped Wails on
`main`. This branch is a **parallel** AMD implementation with extra features
(log window, Stop All) that `main` may lack. They overlap and were never merged.

**Decision pending:** reconcile branch ↔ main. If merging, expect conflicts in
`videotrim.py` and build scripts (both touch AMD/encoders). Likely want: keep
main's Windows **installer**, keep this branch's **log window + Stop All +
cross-platform encoder logic**. Ask the user before merging — do not force-push
`main`.

## Open threads / ideas

- Scan speed: investigated on M2 — decode was already ~20× realtime, so the
  streaming/early-kill rewrite was *slower* (progress-pipe + teardown overhead)
  and was reverted; hwaccel gave no win there. On slower Windows decode or
  large/4K/HEVC files, an `-hwaccel` decode or early-kill may help — re-measure
  on the actual Windows files before optimizing.
- Possible next: show live ffmpeg progress (frame/time) in the log window for the
  running encode.

## Git

- Remote: `origin` → https://github.com/pbzh/VideoTrim.git
- Work here: `git checkout feat/amd-hw-smart-trim`
- Commits end with the Co-Authored-By / session trailer used throughout.
