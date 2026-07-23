#!/usr/bin/env bash
# Build a self-contained VideoTrim.app (Wails/Go) for macOS.
#
# Bundles ffmpeg + ffprobe and all their dynamic libraries into the .app so it
# runs on any Mac — no Homebrew or system ffmpeg required on the target machine.
#
# Requires:
#   go install github.com/wailsapp/wails/v2/cmd/wails@latest
#   brew install ffmpeg dylibbundler
#
# Produces: build/bin/VideoTrim.app
set -euo pipefail

cd "$(dirname "$0")"

# Locate tools (wails is often only on GOPATH/bin).
export PATH="$PATH:$(go env GOPATH)/bin"

command -v wails        >/dev/null || { echo "wails not found — go install github.com/wailsapp/wails/v2/cmd/wails@latest" >&2; exit 1; }
command -v dylibbundler >/dev/null || { echo "dylibbundler not found — brew install dylibbundler" >&2; exit 1; }

FFMPEG="$(command -v ffmpeg  || true)"
FFPROBE="$(command -v ffprobe || true)"
[[ -n "$FFMPEG" && -n "$FFPROBE" ]] || { echo "ffmpeg/ffprobe not found — brew install ffmpeg" >&2; exit 1; }

ARCH="${1:-darwin/arm64}"   # pass darwin/universal for Intel+ARM

echo "==> Building Wails app ($ARCH)…"
wails build -platform "$ARCH" -clean

APP="build/bin/VideoTrim.app"
MACOS="$APP/Contents/MacOS"
LIBS="$APP/Contents/libs"

echo "==> Bundling ffmpeg/ffprobe…"
cp "$FFMPEG"  "$MACOS/ffmpeg"
cp "$FFPROBE" "$MACOS/ffprobe"
chmod +x "$MACOS/ffmpeg" "$MACOS/ffprobe"
mkdir -p "$LIBS"

# Gather every non-system dylib the two binaries need into Contents/libs and
# rewrite their load paths to @executable_path/../libs so the app is portable.
dylibbundler --overwrite-files --bundle-deps --create-dir \
  -x "$MACOS/ffmpeg" \
  -x "$MACOS/ffprobe" \
  -d "$LIBS/" \
  -p "@executable_path/../libs/"

echo "==> Ad-hoc code-signing (deep)…"
codesign --force --deep --sign - "$APP"

echo
echo "Built self-contained: $APP"
