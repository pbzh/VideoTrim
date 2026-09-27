# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file desktop video trimmer that wraps `ffmpeg`/`ffprobe`:

- `videotrim.py` — Python + PyQt6 (~1500 lines), macOS + Windows

## Commands

```bash
pip install PyQt6 PyQt6-Qt6 PyQt6-sip
python videotrim.py
```

### Standalone executables (PyInstaller, bundles ffmpeg/ffprobe)
```bash
./build-windows.ps1     # -> build\windows-python\dist\VideoTrim\VideoTrim.exe
./build-macos.sh        # -> build/macos-python/dist/VideoTrim.app
.\build-installer.ps1   # -> build\installer\VideoTrim-Setup-1.0.0.exe (Inno Setup, installer.iss)
```
Both build scripts require `ffmpeg`/`ffprobe` on PATH; they locate them via `Get-Command`/`command -v` and bundle them **beside the executable** with `--add-binary "<path>;."` (`:` separator on macOS). `find_tool()` depends on that flat layout — change both together.

There are **no tests** and no linter configured.

## Core behavior

**Encoder detection** — `working_encoders()` runs `ffmpeg -encoders`, string-matches the fixed `_HW_ENCODERS` table (top of [videotrim.py](videotrim.py)): `h264/hevc_videotoolbox` (macOS), `h264/hevc/av1_amf` (AMD AMF), then test-encodes each match (`_encoder_works`: 0.2 s lavfi black frames with the real `quality_args`) in parallel and caches the result for the process. Needed because ffmpeg builds list encoders regardless of the GPU. Only working encoders appear in the UI (`available_encoders()`). Intel QSV was removed deliberately. "Stream Copy" is always present.

**Quality flags** — centralized in `quality_args(encoder)`; each vendor uses a different rate-control knob: videotoolbox `-q:v 80`, amf `-rc cqp -qp_i 16 -qp_p 16 -quality quality` (no `qp_b` — av1_amf rejects it), libx26x `-crf 16 -preset medium`. Single source of truth — keep it that way when adding encoders.

**Smart mode** — `build_trim_args()` resolves `smart` to `copy` when the first frame at/after start is a keyframe (±10 ms), else to `smart_fallback_encoder(info)`. The keyframe comes from `keyframe_before()`: last video keyframe with pts < start + one frame, read from packet flags (`ffprobe -show_entries packet=pts_time,dts_time,flags`, demux only; 10 s back, else from file start).
- ffprobe timestamps are absolute but ffmpeg `-ss` is relative to the container `start_time` (MPEG-TS ≈1.4 s) — `keyframe_before` subtracts `info["start"]`.
- `smart_fallback_encoder` matches the source: HEVC→HW HEVC, AV1→`av1_amf`, else HW H.264, then `libx264`. 10-bit sources (`is_deep`) stay 10-bit via a HW encoder that passes a 10-bit test encode (`encodes_10bit`, lazy + cached), else `libx265`.
- `encode_args()` = `quality_args` + `_depth_args` (`p010le`/`yuv420p10le` + `main10` for 10-bit, else `yuv420p` — H.264 is always 8-bit) + source colour tags (primaries/trc/colorspace). HDR10 mastering-display/CLL side data is **not** carried over.

**Trim ffmpeg args** — `build_trim_args()`; `end` may be `''` to trim to EOF. With a known keyframe K: coarse input `-ss` to K − `_SEEK_MARGIN` (1 s), then exact output `-ss`/`-to`. Plain input seeking is **not** safe: MPEG-TS/PS have no index, so an input seek lands mid-GOP and video only starts at the *next* keyframe (up to a GOP of audio-only at the start, wrong first frame) — verified on ffmpeg 8.0.1. Both modes use `-dn` (data tracks like iPhone `mebx`/GoPro `gpmd` break muxing; mov/mp4 regenerate timecode) and `-avoid_negative_ts make_zero`.
- **Copy mode**: `-map 0 -c copy`, output `-ss` = K's **dts** − 1 ms — ffmpeg's streamcopy drops packets whose dts is before the output `-ss`, and K's dts precedes its pts by the B-frame delay. Starts exactly at K (no previous GOP). Exception: sources with cover art (`info["cover"]`) use a plain input seek to K + 1 ms, since output `-ss` would drop the single cover packet.
- **Re-encode mode**: output `-ss` = start → frame-accurate (decodes from K, drops earlier frames), never decodes from 0. Maps `0:V` (video *without* attached-picture cover art, which would hit the video encoder), `0:a?`, `0:s?`; audio → `aac -b:a 192k`, subtitles copied. HEVC into mp4/mov/m4v gets `-tag:v hvc1` (QuickTime/Apple players reject the default `hev1`).
- No keyframe info (probe failed / no flags): falls back to plain input seeking.
- A failed or killed trim deletes its partial output.

**Freeze detection ("Detect" button + Folder Freeze Scan)** — `detect_first_change()` runs `ffmpeg -vf freezedetect` and `parse_initial_freeze()` reads `freeze_start`/`freeze_end` from stderr: only treats a freeze as the intro if it begins at/near t=0; the matching `freeze_end` becomes the suggested start time. Freezes shorter than 1 s are ignored. Detect kills ffmpeg at the first `freeze_end`, or once the stderr `time=` progress passes `_INTRO_GIVEUP_SEC` (3 s) with no intro freeze — it never decodes the whole file.

## Architecture notes

- ffmpeg/ffprobe resolution (`find_tool`) checks the app dir and `sys._MEIPASS`, then PATH, then `EXTRA_PATHS` (Homebrew dirs on macOS; `C:\ffmpeg\bin`, Program Files, WinGet Links on Windows). Edit this when changing bundling.
- App icon via `icon_path()` — bundled `icon.png`/`icon.ico`, else `assets/` in a bare checkout. Icons are generated by [`assets/make_icon.py`](assets/make_icon.py).
- On window close, `_SHUTDOWN` makes `spawn()` refuse new processes, `stop_all()` kills running ones, then threads are joined — no ffmpeg outlives the app.
- Long-running ffmpeg work runs in `QThread` subclasses (`DetectThread`, `TrimThread`, `ScanThread`, `BatchThread`), spawned via `spawn()`/`run()` so every process is registered with the worker registry and killable by `stop_all()` (global Stop All). On Windows `CREATE_NO_WINDOW` suppresses console flashes.
- `LogBus` is a thread-safe signal bus: workers call `log()`/`log_cmd()`/`log_file()`, `LogWindow` displays. Created *after* `QApplication` for cross-thread signals.
- `ScanDialog` drives the folder freeze scan: table of results → batch-trim selected files in place, with live log and Stop.
- `load_video()` resets start/end/slider from **ffprobe's** duration (never carries the previous file's range; works when the preview can't decode); `_on_duration` only fills End if ffprobe had none.
- `VideoTrim` (QMainWindow) is the main window; preview via `QMediaPlayer` + `QVideoWidget`. The player holds the file open, so replace-source trims (single and `ScanDialog` batch via `batch_started`/`batch_finished`) unload it with `_release_preview()` first — otherwise `os.replace` fails on Windows — and `_replace_file()` retries sharing violations briefly. Preview failure is non-fatal (ffmpeg still trims formats the OS can't decode) — `PREVIEWABLE` lists what Qt usually decodes.

## Conventions
- `.gitattributes` enforces **LF line endings repo-wide** — do not introduce CRLF.
- Times in the UI/params are `HH:MM:SS.mmm` (millisecond precision), passed straight to ffmpeg `-ss`/`-to`.
- Time fields reject minutes/seconds ≥ 60. Output == input is detected with `os.path.samefile` (case-insensitive filesystems). Folder scan skips `*.vt_tmp.*` leftovers.
- Output filename auto-derived with `_trimmed` suffix; existing-file overwrite is confirmed in the UI. Replace-source writes to a temp file first, then swaps.
