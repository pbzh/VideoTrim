#!/usr/bin/env bash
# Build VideoTrim.app on macOS using PyInstaller.
#
# Requires:
#   python3 -m pip install PyQt6 pyinstaller
#   brew install ffmpeg
#
# Produces: build/macos-python/dist/VideoTrim.app

set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

FFMPEG="$(command -v ffmpeg)"
FFPROBE="$(command -v ffprobe)"

if [[ -z "$FFMPEG" || -z "$FFPROBE" ]]; then
  echo "ffmpeg/ffprobe not found on PATH. Install with: brew install ffmpeg" >&2
  exit 1
fi

rm -rf build/macos-python/dist build/macos-python/work

python3 -m PyInstaller --noconfirm --windowed --onedir --name VideoTrim \
  --distpath build/macos-python/dist \
  --workpath build/macos-python/work \
  --specpath build/macos-python \
  --icon "$ROOT/assets/icon.icns" \
  --add-binary "${FFMPEG}:." \
  --add-binary "${FFPROBE}:." \
  --add-data "$ROOT/assets/icon.png:." \
  --osx-bundle-identifier com.pblab.videotrim \
  videotrim.py

echo
echo "Built: build/macos-python/dist/VideoTrim.app"
