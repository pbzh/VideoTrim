# VideoTrim

A simple, fast desktop video trimmer built with Python, PyQt6, and ffmpeg. Supports stream copy for instant lossless cuts and hardware-accelerated re-encoding on Apple Silicon Macs.

## Features

- **Video preview** with built-in player, scrub bar, and frame-stepping controls
- **Millisecond-precision** trim range with manual entry or set-from-playhead buttons
- **Stream copy mode** — instant, lossless trimming with no re-encoding (cuts on nearest keyframe)
- **Hardware-accelerated encoding** — frame-accurate trimming using Apple VideoToolbox (H.264 or HEVC) on Apple Silicon Macs (M1 and newer)
- **Cross-platform** — runs on macOS (ARM and Intel) and Windows
- Displays video format, codec, and audio information
- Auto-generates output filename (`_trimmed` suffix)
- Overwrite confirmation for existing files

## Requirements

- **Python** 3.10+
- **PyQt6** (with multimedia modules)
- **ffmpeg** and **ffprobe** on your system PATH

### Install dependencies

```bash
pip install PyQt6 PyQt6-Qt6 PyQt6-sip
```

### Install ffmpeg

**macOS (Homebrew):**
```bash
brew install ffmpeg
```

**Windows (winget):**
```bash
winget install ffmpeg
```

**Windows (Chocolatey):**
```bash
choco install ffmpeg
```

Or download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to your PATH.

## Usage

```bash
python videotrim.py
```

1. Click **Browse** to select a video file
2. Use the player controls to find your trim points:
   - Play/pause
   - `-1s` / `+1s` to step by one second
   - `<` / `>` to step by one frame
   - Drag the scrub bar to seek
3. Click **Set Start** / **Set End** to capture trim points from the current playhead position, or type times directly in the `HH:mm:ss.zzz` fields
4. Choose an encoding mode:
   - **Stream Copy** — fastest, no quality loss, but trims to the nearest keyframe
   - **H.264 (VideoToolbox)** — hardware-accelerated, frame-accurate *(macOS Apple Silicon only)*
   - **HEVC (VideoToolbox)** — hardware-accelerated, frame-accurate, smaller files *(macOS Apple Silicon only)*
5. Optionally change the output path
6. Click **Trim Video**

## Supported formats

MP4, MKV, AVI, MOV, TS, FLV, WMV, WebM, M4V, MPG, MPEG, 3GP — any format supported by your ffmpeg installation.

## Encoding modes

| Mode | Speed | Accuracy | Quality | Platform |
|------|-------|----------|---------|----------|
| Stream Copy | Instant | Keyframe-aligned | Lossless | All |
| H.264 (VideoToolbox) | Fast | Frame-accurate | High (quality 65) | macOS Apple Silicon |
| HEVC (VideoToolbox) | Fast | Frame-accurate | High (quality 65) | macOS Apple Silicon |

**Stream copy** places `-ss` before `-i` (input seeking) for maximum speed. **Re-encode modes** place `-ss` after `-i` (output seeking) for frame-accurate cuts.

## Packaging

VideoTrim supports bundled ffmpeg binaries for standalone distribution:

**macOS (py2app):** Place `ffmpeg` and `ffprobe` in `YourApp.app/Contents/Resources/ffmpeg/`.

**Windows (PyInstaller):** Place `ffmpeg.exe` and `ffprobe.exe` in a `ffmpeg/` folder next to the executable.

If bundled binaries are not found, the app falls back to the system PATH.

## License

See [LICENSE](LICENSE) for details.
