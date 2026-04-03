# VideoTrim

A simple, fast desktop video trimmer built with Python, PyQt6, and ffmpeg. Supports stream copy for instant lossless cuts and hardware-accelerated re-encoding via Apple VideoToolbox and Intel Quick Sync Video (QSV).

## Features

- **Video preview** with built-in player, scrub bar, and frame-stepping controls
- **Millisecond-precision** trim range with manual entry or set-from-playhead buttons
- **Stream copy mode** — instant, lossless trimming with no re-encoding (cuts on nearest keyframe)
- **Hardware-accelerated encoding** — frame-accurate trimming using Apple VideoToolbox (H.264, HEVC) or Intel Quick Sync Video (H.264, HEVC, AV1)
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
   - **H.264 / HEVC (VideoToolbox)** — hardware-accelerated, frame-accurate *(macOS Apple Silicon)*
   - **H.264 / HEVC / AV1 (Intel QSV)** — hardware-accelerated, frame-accurate *(Intel GPU with QSV support)*
   
   Hardware encoding options are auto-detected at startup and only shown when your system supports them.
5. Optionally change the output path
6. Click **Trim Video**

## Supported formats

MP4, MKV, AVI, MOV, TS, FLV, WMV, WebM, M4V, MPG, MPEG, 3GP — any format supported by your ffmpeg installation.

## Encoding modes

| Mode | Speed | Accuracy | Quality | Requires |
|------|-------|----------|---------|----------|
| Stream Copy | Instant | Keyframe-aligned | Lossless | Any system |
| H.264 (VideoToolbox) | Fast | Frame-accurate | High (q 65) | macOS Apple Silicon |
| HEVC (VideoToolbox) | Fast | Frame-accurate | High (q 65) | macOS Apple Silicon |
| H.264 (Intel QSV) | Fast | Frame-accurate | High (global_quality 18) | Intel GPU with QSV |
| HEVC (Intel QSV) | Fast | Frame-accurate | High (global_quality 18) | Intel GPU with QSV |
| AV1 (Intel QSV) | Fast | Frame-accurate | High (global_quality 18) | Intel GPU with QSV (Arc/DG2+) |

**Stream copy** places `-ss` before `-i` (input seeking) for maximum speed. **Re-encode modes** place `-ss` after `-i` (output seeking) for frame-accurate cuts.

Hardware encoders are auto-detected by probing `ffmpeg -encoders` at startup. Only encoders available on your system are shown in the UI.

### Intel QSV requirements

Intel QSV requires a supported Intel GPU and appropriate drivers:

- **Windows:** Install the latest Intel graphics driver from [intel.com](https://www.intel.com/content/www/us/en/download-center/home.html). QSV is supported on most Intel CPUs with integrated graphics (6th gen+) and Intel Arc discrete GPUs.
- **Linux:** Install the Intel Media SDK or oneVPL runtime (`intel-media-va-driver` or `intel-media-va-driver-non-free` on Debian/Ubuntu). Your ffmpeg must be built with `--enable-libmfx` or `--enable-libvpl`.

AV1 hardware encoding requires Intel Arc (Alchemist/DG2) or newer GPUs, such as the Arc Pro B50.

## Packaging

VideoTrim supports bundled ffmpeg binaries for standalone distribution:

**macOS (py2app):** Place `ffmpeg` and `ffprobe` in `YourApp.app/Contents/Resources/ffmpeg/`.

**Windows (PyInstaller):** Place `ffmpeg.exe` and `ffprobe.exe` in a `ffmpeg/` folder next to the executable.

If bundled binaries are not found, the app falls back to the system PATH.

## License

See [LICENSE](LICENSE) for details.
