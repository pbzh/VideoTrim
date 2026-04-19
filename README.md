# VideoTrim

A simple, fast desktop video trimmer with two implementations:

- **Python + PyQt6** — `videotrim.py`
- **Go + Wails** — `videotrim-wails/` (native desktop app with a web-based UI)

Both use ffmpeg under the hood and share the same feature set.

## Features

- **Video preview** with built-in player, scrub bar, and frame-stepping controls
- **Millisecond-precision** trim range with manual entry or set-from-playhead buttons
- **Stream copy mode** — instant, lossless trimming with no re-encoding (cuts on nearest keyframe)
- **Hardware-accelerated encoding** — frame-accurate trimming using Apple VideoToolbox (H.264, HEVC) or Intel Quick Sync Video (H.264, HEVC, AV1)
- **Cross-platform** — runs on macOS (ARM and Intel) and Windows
- Displays video format, codec, and audio information
- Auto-generates output filename (`_trimmed` suffix)
- Overwrite confirmation for existing files
- Graceful preview error handling — unsupported formats show a warning but trimming still works via ffmpeg

---

## Go + Wails version (`videotrim-wails/`)

### Requirements

- **Go** 1.22+
- **Wails CLI** v2
- **ffmpeg** and **ffprobe** on your system PATH

### Install

```bash
# Install Go (macOS)
brew install go

# Install Wails CLI
go install github.com/wailsapp/wails/v2/cmd/wails@latest
```

### Build

```bash
cd videotrim-wails
go mod tidy
wails build
```

The compiled app is output to `build/bin/VideoTrim.app` (macOS) or `build/bin/VideoTrim.exe` (Windows).

### Development (live reload)

```bash
cd videotrim-wails
wails dev
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

---

## Python + PyQt6 version (`videotrim.py`)

### Requirements

- **Python** 3.10+
- **PyQt6** (with multimedia modules)
- **ffmpeg** and **ffprobe** on your system PATH

### Install dependencies

```bash
pip install PyQt6 PyQt6-Qt6 PyQt6-sip
```

### Run

```bash
python videotrim.py
```

---

## Building a standalone executable (PyInstaller)

Both scripts bundle ffmpeg/ffprobe into the output directory so the app runs
self-contained — no Python or ffmpeg installation required on the target machine.

### Windows — `build-windows.ps1`

**Requirements:** Python 3.10+, PyInstaller, ffmpeg on PATH

```powershell
pip install PyQt6 pyinstaller
winget install ffmpeg   # or drop ffmpeg.exe / ffprobe.exe onto your PATH manually
```

Run the build script from a PowerShell prompt:

```powershell
.\build-windows.ps1
```

Output: `build\windows-python\dist\VideoTrim\VideoTrim.exe`

To run on another machine, copy the entire `VideoTrim\` folder — the exe and
the `_internal\` directory alongside it must stay together.

### macOS — `build-macos.sh`

**Requirements:** Python 3.10+, PyInstaller, ffmpeg via Homebrew

```bash
pip3 install PyQt6 pyinstaller
brew install ffmpeg
```

```bash
chmod +x build-macos.sh
./build-macos.sh
```

Output: `build/macos-python/dist/VideoTrim.app`

---

## Usage

1. Click **Browse** to select a video file
2. Use the player controls to find your trim points:
   - Play/pause
   - `-1s` / `+1s` to step by one second
   - `<` / `>` to step by one frame
   - Drag the scrub bar to seek
3. Click **Set Start** / **Set End** to capture trim points from the current playhead position, or type times directly in the `HH:MM:SS.mmm` fields
4. Choose an encoding mode:
   - **Stream Copy** — fastest, no quality loss, but trims to the nearest keyframe
   - **H.264 / HEVC (VideoToolbox)** — hardware-accelerated, frame-accurate *(macOS)*
   - **H.264 / HEVC / AV1 (Intel QSV)** — hardware-accelerated, frame-accurate *(Intel GPU)*

   Hardware encoding options are auto-detected at startup and only shown when your system supports them.
5. Optionally change the output path
6. Click **Trim Video**

> **Note:** If the built-in preview shows an error (e.g. HEVC on Windows without
> the HEVC Video Extensions codec pack), the status bar will say so but trimming
> still works — ffmpeg handles all formats regardless of what the OS decoder supports.

## Supported formats

MP4, MKV, AVI, MOV, TS, FLV, WMV, WebM, M4V, MPG, MPEG, 3GP — any format supported by your ffmpeg installation.

## Encoding modes

| Mode | Speed | Accuracy | Quality | Requires |
|------|-------|----------|---------|----------|
| Stream Copy | Instant | Keyframe-aligned | Lossless | Any system |
| H.264 (VideoToolbox) | Fast | Frame-accurate | High (q 65) | macOS |
| HEVC (VideoToolbox) | Fast | Frame-accurate | High (q 65) | macOS |
| H.264 (Intel QSV) | Fast | Frame-accurate | High (global_quality 18) | Intel GPU with QSV |
| HEVC (Intel QSV) | Fast | Frame-accurate | High (global_quality 18) | Intel GPU with QSV |
| AV1 (Intel QSV) | Fast | Frame-accurate | High (global_quality 18) | Intel Arc/DG2+ |

**Stream copy** places `-ss` before `-i` (input seeking) for maximum speed. **Re-encode modes** place `-ss` after `-i` (output seeking) for frame-accurate cuts.

Hardware encoders are auto-detected by probing `ffmpeg -encoders` at startup. Only encoders available on your system are shown in the UI.

### Intel QSV requirements

- **Windows:** Install the latest Intel graphics driver. QSV is supported on most Intel CPUs with integrated graphics (6th gen+) and Intel Arc discrete GPUs.
- **Linux:** Install the Intel Media SDK or oneVPL runtime (`intel-media-va-driver` on Debian/Ubuntu). Your ffmpeg must be built with `--enable-libmfx` or `--enable-libvpl`.

AV1 hardware encoding requires Intel Arc (Alchemist/DG2) or newer.

## License

See [LICENSE](LICENSE) for details.