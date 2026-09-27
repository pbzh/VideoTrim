#!/usr/bin/env python3
"""VideoTrim — a simple, fast video trimmer for macOS (Apple Silicon).

Minimal-quality-loss trimming built on ffmpeg:
  * Smart mode      — lossless stream-copy when the cut is on a keyframe,
                      otherwise a frame-accurate near-lossless VideoToolbox
                      re-encode.
  * Stream Copy     — instant, lossless, keyframe-aligned.
  * VideoToolbox    — hardware H.264 / HEVC, frame-accurate, near-lossless.

Hardware encoders are auto-detected per machine (each is test-encoded once at
startup): Apple VideoToolbox on macOS, AMD AMF on Windows (software x264 is the
fallback).

Extras:
  * Detect Start    — find the first real frame change (skip a frozen intro).
  * Overwrite source — replace the opened file with the trimmed result.
  * Folder Freeze Scan — check every video in a folder for a frozen intro,
                      show a table, and batch-trim the selected files in place.
  * ffmpeg log window (View menu) and a global Stop All button.

Cross-platform (macOS / Windows). Needs ffmpeg and ffprobe on PATH, or bundled
alongside the executable (see build-macos.sh / build-windows.ps1).
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import shlex
import shutil
import threading
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QAction, QIcon
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox,
    QSlider, QSpinBox, QFileDialog, QMessageBox, QPlainTextEdit, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QSizePolicy,
)

# --------------------------------------------------------------------------
# Tool resolution — cross-platform; prefer a bundled ffmpeg, else PATH.
# --------------------------------------------------------------------------

IS_WIN = os.name == "nt"
_EXE = ".exe" if IS_WIN else ""

# Suppress console-window flashes for every ffmpeg spawn on Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0

if IS_WIN:
    EXTRA_PATHS = [
        r"C:\ffmpeg\bin",
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "ffmpeg", "bin"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links"),
    ]
else:
    EXTRA_PATHS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".ts", ".flv",
    ".wmv", ".webm", ".m4v", ".mpg", ".mpeg", ".3gp",
}
# Formats the Qt preview backend can usually decode; others still trim via ffmpeg.
PREVIEWABLE = {".mp4", ".m4v", ".mov"}


def _app_dir() -> Path:
    # PyInstaller onefile unpacks to _MEIPASS; onedir puts binaries beside exe.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def icon_path() -> str:
    """Path to the bundled app icon, or "" when running from a bare checkout."""
    bases = [_app_dir(), Path(getattr(sys, "_MEIPASS", _app_dir()))]
    if not getattr(sys, "frozen", False):
        bases.append(Path(__file__).resolve().parent / "assets")
    else:
        bases.append(_app_dir() / "_internal")     # PyInstaller 6.x onedir
    for base in bases:
        for name in ("icon.png", "icon.ico"):
            cand = base / name
            if cand.is_file():
                return str(cand)
    return ""


def find_tool(base_name: str) -> str:
    name = base_name + _EXE
    for base in (_app_dir(), Path(getattr(sys, "_MEIPASS", _app_dir()))):
        cand = base / name
        if cand.exists():
            return str(cand)
    which = shutil.which(name) or shutil.which(base_name)
    if which:
        return which
    for d in EXTRA_PATHS:
        if not d:
            continue
        cand = Path(d) / name
        if cand.exists():
            return str(cand)
    return name


FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")


# --------------------------------------------------------------------------
# ffmpeg log bus — thread-safe; worker threads emit, the log window displays.
# --------------------------------------------------------------------------

class LogBus(QObject):
    line = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.enabled = False
        self._buf: list[str] = []
        self._lock = threading.Lock()

    def log(self, msg: str):
        if not self.enabled:
            return
        text = f"[{time.strftime('%H:%M:%S')}] {msg}"
        with self._lock:
            self._buf.append(text)
            if len(self._buf) > 5000:
                del self._buf[:1000]
        self.line.emit(text)

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._buf)


LOGBUS: "LogBus | None" = None


def log(msg: str) -> None:
    if LOGBUS is not None:
        LOGBUS.log(msg)


def log_cmd(label: str, args: list[str]) -> None:
    if LOGBUS is None or not LOGBUS.enabled or not args:
        return
    cmd = os.path.basename(args[0]) + " " + " ".join(shlex.quote(a) for a in args[1:])
    log(f"$ [{label}] {cmd}")


def log_file(action: str, path: str) -> None:
    """Log a one-line summary of the file about to be processed."""
    if LOGBUS is None or not LOGBUS.enabled:
        return
    info = _probe(path)
    base = os.path.basename(path)
    if info.get("error"):
        log(f"• {action}: {base} — {info['error']}")
        return
    try:
        size_mb = os.path.getsize(path) / (1024 * 1024)
    except OSError:
        size_mb = 0.0
    log(f"• {action}: {base} — {info['vcodec'] or '?'} "
        f"{info['w']}x{info['h']} {info['fps']:.3g}fps "
        f"{seconds_to_time(info['duration_ms'] / 1000)} {size_mb:.1f}MB")


# --------------------------------------------------------------------------
# Process registry — lets a single Stop cancel every running ffmpeg.
# --------------------------------------------------------------------------

_PROCS: set[subprocess.Popen] = set()
_PROCS_LOCK = threading.Lock()
_WORKERS: set = set()               # QThreads exposing .cancel()
_WORKERS_LOCK = threading.Lock()
_SHUTDOWN = threading.Event()       # set on app close: refuse new processes


def spawn(args: list[str], **kw) -> subprocess.Popen:
    """Start a tracked subprocess so Stop All can kill it."""
    if _SHUTDOWN.is_set():
        raise RuntimeError("shutting down")
    if _NO_WINDOW:
        kw.setdefault("creationflags", _NO_WINDOW)
    p = subprocess.Popen(args, **kw)
    with _PROCS_LOCK:
        _PROCS.add(p)
    return p


def _release(p: subprocess.Popen) -> None:
    with _PROCS_LOCK:
        _PROCS.discard(p)


def register_worker(w) -> None:
    with _WORKERS_LOCK:
        _WORKERS.add(w)


def unregister_worker(w) -> None:
    with _WORKERS_LOCK:
        _WORKERS.discard(w)


def stop_all() -> int:
    """Cancel every tracked worker and kill every running ffmpeg. Returns kills."""
    with _WORKERS_LOCK:
        workers = list(_WORKERS)
    for w in workers:
        try:
            w.cancel()
        except Exception:
            pass
    with _PROCS_LOCK:
        procs = list(_PROCS)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass
    if procs:
        log(f"■ Stop All — killed {len(procs)} ffmpeg process(es)")
    return len(procs)


def run(args: list[str], timeout: float | None = None,
        label: str = "cmd") -> subprocess.CompletedProcess:
    """Run a tracked command, capturing text output; never raises on non-zero."""
    log_cmd(label, args)
    p = spawn(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
    finally:
        _release(p)
    return subprocess.CompletedProcess(args, p.returncode or 0, out, err)


# --------------------------------------------------------------------------
# Time helpers (millisecond precision, HH:MM:SS.mmm)
# --------------------------------------------------------------------------

_TIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})(?:[.,](\d{1,3}))?$")


def ms_to_time(ms: int) -> str:
    ms = max(0, int(round(ms)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{msec:03d}"


def time_to_ms(text: str) -> int:
    m = _TIME_RE.match(text.strip())
    if not m or int(m.group(2)) >= 60 or int(m.group(3)) >= 60:
        return -1
    msec = int((m.group(4) or "0").ljust(3, "0")) if m.group(4) else 0
    return int(m.group(1)) * 3_600_000 + int(m.group(2)) * 60_000 + int(m.group(3)) * 1000 + msec


def seconds_to_time(sec: float) -> str:
    return ms_to_time(int(round(max(0.0, sec) * 1000)))


def time_to_seconds(text: str) -> float | None:
    parts = text.strip().split(":")
    try:
        secs = 0.0
        for p in parts:
            secs = secs * 60 + float(p)
        return secs
    except ValueError:
        return None


# --------------------------------------------------------------------------
# ffprobe / encoder helpers
# --------------------------------------------------------------------------

def _probe(path: str) -> dict:
    cp = run([FFPROBE, "-v", "quiet", "-print_format", "json",
              "-show_streams", "-show_format", path], label="probe")
    if cp.returncode != 0 or not cp.stdout:
        return {"error": "ffprobe failed — is ffmpeg installed?"}
    try:
        data = json.loads(cp.stdout)
    except json.JSONDecodeError:
        return {"error": "could not parse ffprobe output"}

    fmt = data.get("format") or {}
    info: dict = {
        "format": fmt.get("format_name", ""),
        "vcodec": "", "acodec": "", "duration_ms": 0, "fps": 0.0, "w": 0, "h": 0,
        "start": 0.0, "pix_fmt": "", "colors": {}, "cover": False,
    }
    try:
        info["duration_ms"] = int(float(fmt.get("duration", 0)) * 1000)
    except (TypeError, ValueError):
        pass
    try:
        # ffmpeg's -ss/-to are relative to this; ffprobe timestamps are not.
        info["start"] = float(fmt.get("start_time", 0))
    except (TypeError, ValueError):
        pass

    for st in data.get("streams", []):
        kind = st.get("codec_type")
        if (st.get("disposition") or {}).get("attached_pic"):
            info["cover"] = True
        if kind == "video" and not info["vcodec"] \
                and not (st.get("disposition") or {}).get("attached_pic"):
            info["vcodec"] = st.get("codec_name", "")
            info["w"] = st.get("width", 0) or 0
            info["h"] = st.get("height", 0) or 0
            info["pix_fmt"] = st.get("pix_fmt", "") or ""
            info["colors"] = {
                flag: st[key] for key, flag in (("color_primaries", "-color_primaries"),
                                                ("color_transfer", "-color_trc"),
                                                ("color_space", "-colorspace"))
                if st.get(key) and st[key] not in ("unknown", "reserved")
            }
            r = st.get("r_frame_rate", "")
            if "/" in r:
                num, den = r.split("/", 1)
                try:
                    n, d = float(num), float(den)
                    if n > 0 and d > 0:
                        info["fps"] = n / d
                except ValueError:
                    pass
        elif kind == "audio" and not info["acodec"]:
            info["acodec"] = st.get("codec_name", "")

    if not info["vcodec"]:
        return {"error": "no video stream found"}
    return info


def get_video_info(path: str) -> dict:
    return _probe(path)


# (label, encoder, hint) — availability-filtered per machine at runtime.
_HW_ENCODERS = [
    ("H.264 (VideoToolbox)", "h264_videotoolbox",
     "Apple hardware H.264 — frame-accurate, fast"),
    ("HEVC (VideoToolbox)", "hevc_videotoolbox",
     "Apple hardware HEVC — smaller files"),
    ("H.264 (AMD AMF)", "h264_amf",
     "AMD hardware H.264 (Windows) — frame-accurate, fast"),
    ("HEVC (AMD AMF)", "hevc_amf",
     "AMD hardware HEVC (Windows) — smaller files"),
    ("AV1 (AMD AMF)", "av1_amf",
     "AMD hardware AV1 (RDNA3+, Windows) — best compression"),
]


_WORKING: list[str] | None = None     # HW encoders that passed a test encode
_WORKING_LOCK = threading.Lock()


def _encoder_works(encoder: str, deep: bool = False) -> bool:
    """Encode a few black frames with the real quality flags; True on success.
    deep=True tests 10-bit output (the flags _depth_args would use)."""
    extra = _depth_args(encoder, deep=True) if deep else []
    cp = run([FFMPEG, "-hide_banner", "-v", "error",
              "-f", "lavfi", "-i", "color=c=black:s=640x480:r=30:d=0.2",
              "-c:v", encoder] + quality_args(encoder) + extra + ["-f", "null", "-"],
             timeout=20, label="enc-test")
    return cp.returncode == 0


_DEEP: dict[str, bool] = {}          # encoder -> 10-bit output works (lazy)
_DEEP_LOCK = threading.Lock()


def encodes_10bit(encoder: str) -> bool:
    """True if `encoder` can write 10-bit here (HEVC/AV1 only; probed once)."""
    if "h264" in encoder or "x264" in encoder:
        return False
    with _DEEP_LOCK:
        if encoder not in _DEEP:
            _DEEP[encoder] = _encoder_works(encoder, deep=True)
        return _DEEP[encoder]


def is_deep(pix_fmt: str) -> bool:
    """True for >8-bit pixel formats (yuv420p10le, p010le, yuv422p12le, …)."""
    return bool(re.search(r"1[0-6](le|be)$", pix_fmt))    # ffprobe always adds le/be


def _depth_args(encoder: str, deep: bool) -> list[str]:
    """Pixel format / profile so 10-bit sources stay 10-bit where the encoder
    can, and 8-bit-only encoders (all H.264) get 8-bit input they accept."""
    if not deep:
        return ["-pix_fmt", "yuv420p"]
    fmt = "yuv420p10le" if encoder.startswith("lib") else "p010le"
    args = ["-pix_fmt", fmt]
    if "hevc" in encoder:
        args += ["-profile:v", "main10"]
    return args


def encode_args(encoder: str, info: dict) -> list[str]:
    """Quality + pixel-format + colour-tag flags for re-encoding `info`'s video."""
    deep = is_deep(info.get("pix_fmt", "")) and encodes_10bit(encoder)
    args = quality_args(encoder) + _depth_args(encoder, deep)
    for flag, val in (info.get("colors") or {}).items():
        args += [flag, val]
    return args


def working_encoders() -> list[str]:
    """HW encoders that actually work on this machine (probed once, cached).

    `ffmpeg -encoders` only says what the build was compiled with — common
    Windows builds list AMF on any GPU — so each candidate is test-encoded.
    """
    global _WORKING
    with _WORKING_LOCK:
        if _WORKING is None:
            out = run([FFMPEG, "-hide_banner", "-encoders"], label="encoders").stdout or ""
            listed = [enc for (_, enc, _) in _HW_ENCODERS if enc in out]
            ok = []
            if listed:
                with ThreadPoolExecutor(max_workers=len(listed)) as ex:
                    ok = list(ex.map(_encoder_works, listed))
            _WORKING = [enc for enc, good in zip(listed, ok) if good]
            log(f"→ working HW encoders: {', '.join(_WORKING) or 'none'}")
        return _WORKING


def available_encoders() -> list[dict]:
    working = working_encoders()
    return [{"label": lbl, "encoder": enc, "hint": hint}
            for (lbl, enc, hint) in _HW_ENCODERS if enc in working]


def quality_args(encoder: str) -> list[str]:
    if "videotoolbox" in encoder:
        return ["-q:v", "80"]           # 1-100, higher = better; ~visually lossless
    if "amf" in encoder:                # AMD: constant-QP, lower = better; no qp_b (av1)
        return ["-rc", "cqp", "-qp_i", "16", "-qp_p", "16", "-quality", "quality"]
    if "libx26" in encoder:
        return ["-crf", "16", "-preset", "medium"]
    return []


def smart_fallback_encoder(info: dict | None = None) -> str:
    """Encoder for Smart mode's frame-accurate re-encode.

    Matches the source codec where a working HW encoder exists (HEVC stays
    HEVC, AV1 stays AV1), keeps 10-bit sources 10-bit (HW, else libx265), and
    otherwise uses HW H.264, then libx264.
    """
    info = info or {}
    working = working_encoders()
    vcodec = info.get("vcodec", "")
    deep = is_deep(info.get("pix_fmt", ""))
    h264 = ["h264_videotoolbox", "h264_amf"]
    hevc = ["hevc_videotoolbox", "hevc_amf"]
    if vcodec == "av1":
        prefer = ["av1_amf"] + hevc
    elif vcodec == "hevc" or deep:
        prefer = hevc + ["av1_amf"]
    else:
        prefer = h264
    cands = [e for e in prefer if e in working]
    if deep:
        for enc in cands:
            if encodes_10bit(enc):
                return enc
        if encodes_10bit("libx265"):
            return "libx265"
        log("  no 10-bit encoder works — re-encoding as 8-bit")
    for enc in cands + [e for e in h264 if e in working]:
        return enc
    return "libx264"


_SEEK_MARGIN = 1.0      # s before the keyframe for the coarse input seek


def keyframe_before(path: str, start: float, file_start: float = 0.0,
                    fps: float = 0.0) -> tuple[float, float] | None:
    """(pts, dts) — as ffmpeg -ss sees them — of the last video keyframe shown
    before start + one frame: the keyframe a cut at `start` begins in, or the
    keyframe that *is* the first frame at/after `start`. None if not found.

    Reads packet flags only (demux, no decode). file_start is the container
    start_time: ffmpeg's -ss is relative to it but ffprobe timestamps are
    absolute (MPEG-TS starts at ~1.4 s).
    """
    t = start + file_start
    limit = t + (1.0 / fps if fps > 0 else 0.010) - 0.001
    # 10 s back covers normal GOPs; else scan from the file start. ffprobe may
    # land anywhere, so only a keyframe read contiguously up to `limit` counts.
    for back in (10.0, None):
        lo = "" if back is None else f"{max(0.0, t - back):.6f}"
        cp = run([FFPROBE, "-v", "error", "-select_streams", "V:0",
                  "-show_entries", "packet=pts_time,dts_time,flags", "-of", "csv=p=0",
                  "-read_intervals", f"{lo}%{limit:.6f}", path], label="keyframe")
        if cp.returncode != 0:
            return None
        best = None
        for line in cp.stdout.splitlines():
            parts = line.strip().split(",")
            if len(parts) < 3 or "K" not in parts[2]:
                continue
            try:
                pts = float(parts[0])
            except ValueError:
                continue
            try:
                dts = float(parts[1])
            except ValueError:
                dts = pts
            if pts < limit and (best is None or pts > best[0]):
                best = (pts, dts)
        if best is not None:
            return best[0] - file_start, best[1] - file_start
        if back is None or t - back <= 0:
            break
    return None


# --------------------------------------------------------------------------
# Freeze detection
# --------------------------------------------------------------------------

_FS_RE = re.compile(r"freeze_start:\s*([\d.]+)")
_FE_RE = re.compile(r"freeze_end:\s*([\d.]+)")
_PROGRESS_RE = re.compile(r"time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
# A frozen intro (freeze_start < 0.5 s, d=1) is reported by ~1.5 s of media;
# once progress passes this without one, there is no frozen intro.
_INTRO_GIVEUP_SEC = 3.0


def parse_initial_freeze(text: str) -> tuple[bool, float, bool]:
    """Return (starts_frozen, freeze_end_seconds, has_end).

    Detects a freeze that begins at/near t=0 (a frozen intro).
    """
    frozen = False
    for line in text.splitlines():
        if "freeze_start:" in line:
            m = _FS_RE.search(line)
            if m and float(m.group(1)) < 0.5:
                frozen = True
        elif "freeze_end:" in line and frozen:
            m = _FE_RE.search(line)
            if m:
                return True, float(m.group(1)), True
    return frozen, 0.0, False


def detect_first_change(path: str) -> str:
    """First real frame change as HH:MM:SS.mmm ('' if no ≥1s frozen intro).

    Streams ffmpeg and stops the moment the first freeze_end is seen, or once
    progress passes _INTRO_GIVEUP_SEC with no frozen intro, so it only decodes
    the start of the file instead of the whole thing.
    """
    log_file("detect", path)
    args = [FFMPEG, "-hide_banner", "-stats", "-i", path,
            "-vf", "freezedetect=n=-40dB:d=1", "-map", "0:v:0", "-f", "null", "-"]
    log_cmd("detect", args)
    proc = spawn(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    frozen = False
    result = ""
    try:
        assert proc.stderr is not None
        for line in proc.stderr:
            if "freeze_start:" in line:
                m = _FS_RE.search(line)
                if m and float(m.group(1)) < 0.5:
                    frozen = True
            elif "freeze_end:" in line and frozen:
                m = _FE_RE.search(line)
                if m:
                    result = seconds_to_time(float(m.group(1)))
                    proc.kill()
                    break
            elif not frozen and "time=" in line:
                m = _PROGRESS_RE.search(line)
                if m and (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                          + float(m.group(3))) > _INTRO_GIVEUP_SEC:
                    proc.kill()
                    break
        proc.wait()
    finally:
        _release(proc)
    log(f"→ detect {os.path.basename(path)}: {result or 'no frozen intro'}")
    return result


# --------------------------------------------------------------------------
# Trimming
# --------------------------------------------------------------------------

def build_trim_args(input_path: str, out_path: str, start: str, end: str,
                    encoder_mode: str) -> tuple[list[str], str]:
    """Return (ffmpeg_args, resolved_mode). end may be '' to trim to EOF."""
    info = _probe(input_path)
    s = time_to_seconds(start) or 0.0
    kf = keyframe_before(input_path, s, info.get("start", 0.0), info.get("fps", 0.0))
    mode = encoder_mode
    if mode == "smart":
        # Lossless only if the first frame at/after start is a keyframe
        # (±10 ms for the UI's ms rounding).
        mode = "copy" if kf is not None and kf[0] >= s - 0.010             else smart_fallback_encoder(info)

    if kf is None or (mode == "copy" and info.get("cover")):
        # Plain input seek — exact in indexed containers (mp4/mov/mkv). Used
        # when there is no keyframe info, and for copies with cover art, whose
        # single packet an output -ss would drop. Copy seeks 1 ms past the
        # keyframe: it lands on the last keyframe at/before -ss.
        if kf is not None:
            start = f"{kf[0] + 0.001:.6f}"
        args = ["-y", "-ss", start] + (["-to", end] if end else []) + ["-i", input_path]
    else:
        # Coarse input seek to before the keyframe, then an exact output seek.
        # Input seeking alone is unsafe: in MPEG-TS/PS (no index) it lands
        # mid-GOP, so video only starts at the *next* keyframe. The margin
        # covers B-frame decode delay. Re-encode decodes from the keyframe and
        # drops frames before `start` — frame-accurate without decoding from 0.
        # Copy starts at the keyframe itself: ffmpeg drops copied packets whose
        # *dts* is before the output -ss, so cut at the keyframe's dts.
        pts, dts = kf
        seek = max(0.0, min(pts, dts) - _SEEK_MARGIN)
        cut = dts - 0.001 if mode == "copy" else s
        args = ["-y", "-ss", f"{seek:.6f}", "-i", input_path,
                "-ss", f"{max(0.0, cut - seek):.6f}"]
        if end:
            args += ["-to", f"{(time_to_seconds(end) or 0.0) - seek:.6f}"]
    # -dn: data tracks (iPhone mebx, GoPro gpmd, tmcd) can't be muxed into
    # most containers; mov/mp4 regenerate timecode from metadata anyway.
    if mode == "copy":
        args += ["-map", "0", "-dn", "-c", "copy"]
    else:
        # 0:V = video without attached pictures (cover art would otherwise be
        # fed to the video encoder); subtitles are copied, not re-encoded.
        args += ["-map", "0:V", "-map", "0:a?", "-map", "0:s?", "-dn",
                 "-c:v", mode] + encode_args(mode, info)
        if ("hevc" in mode or mode == "libx265") \
                and Path(out_path).suffix.lower() in (".mp4", ".mov", ".m4v"):
            args += ["-tag:v", "hvc1"]      # QuickTime / Apple players need hvc1
        args += ["-c:a", "aac", "-b:a", "192k", "-c:s", "copy"]
    args += ["-avoid_negative_ts", "make_zero", out_path]
    return args, mode


def trim_video(input_path: str, output_path: str, start: str, end: str,
               encoder_mode: str, replace_source: bool,
               on_proc=None) -> tuple[bool, str]:
    """Run ffmpeg to trim. Returns (success, message)."""
    if not input_path or not os.path.exists(input_path):
        return False, "Input file not found"

    if replace_source:
        p = Path(input_path)
        out_path = str(p.with_name(p.stem + ".vt_tmp" + p.suffix))
    else:
        if not output_path:
            return False, "No output path specified"
        if os.path.exists(output_path) and os.path.samefile(input_path, output_path):
            return False, 'Output equals input — enable "Overwrite source" to replace it'
        out_path = output_path

    log_file("trim", input_path)
    args, mode = build_trim_args(input_path, out_path, start, end, encoder_mode)
    log(f"  start={start} end={end or 'EOF'} mode={mode} "
        f"replace={replace_source}")
    full = [FFMPEG, "-hide_banner"] + args
    log_cmd("trim", full)

    proc = spawn(full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if on_proc:
        on_proc(proc)
    try:
        out = proc.stdout.read() if proc.stdout else ""
        proc.wait()
    finally:
        _release(proc)

    if proc.returncode != 0:
        _silent_remove(out_path)            # partial / killed output is junk
        tail = out[-1500:] if len(out) > 1500 else out
        log(f"→ trim {os.path.basename(input_path)}: FAILED (exit {proc.returncode})")
        return False, f"ffmpeg error (exit {proc.returncode}):\n{tail}"

    if not os.path.exists(out_path):
        return False, "ffmpeg reported success but no output file was created"
    size_mb = os.path.getsize(out_path) / (1024 * 1024)

    final = out_path
    if replace_source:
        err = _replace_file(out_path, input_path)
        if err:
            _silent_remove(out_path)
            return False, f"Trim ok but could not replace source: {err}"
        final = input_path

    how = "lossless copy" if mode == "copy" else f"re-encoded ({mode})"
    verb = "Replaced source" if replace_source else "Saved"
    log(f"→ trim {os.path.basename(input_path)}: OK ({how}, {size_mb:.1f} MB)")
    return True, f"Done — {how}, {verb} ({size_mb:.1f} MB): {final}"


def _replace_file(src: str, dst: str) -> OSError | None:
    """os.replace (atomic on same filesystem), retried briefly on Windows
    sharing violations — AV scanners/indexers may hold a file for a moment."""
    err: OSError | None = None
    for _ in range(5):
        try:
            os.replace(src, dst)
            return None
        except PermissionError as e:
            err = e
            time.sleep(0.5)
        except OSError as e:
            return e
    return err


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Background workers (QThread)
# --------------------------------------------------------------------------

class DetectThread(QThread):
    done = pyqtSignal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        try:
            self.done.emit(detect_first_change(self.path))
        except Exception:
            self.done.emit("")


class TrimThread(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, **params):
        super().__init__()
        self.params = params

    def run(self):
        try:
            ok, msg = trim_video(**self.params)
        except Exception as e:
            ok, msg = False, f"Trim failed: {e}"
        self.done.emit(ok, msg)


class ScanThread(QThread):
    progress = pyqtSignal(dict)
    finished_rows = pyqtSignal(list)

    def __init__(self, folder: str, window: float):
        super().__init__()
        self.folder = folder
        self.window = window
        self._cancel = threading.Event()
        self._procs: set[subprocess.Popen] = set()
        self._lock = threading.Lock()
        self._done = 0

    def cancel(self):
        self._cancel.set()
        with self._lock:
            for p in list(self._procs):
                try:
                    p.kill()
                except Exception:
                    pass

    def _scan_one(self, path: str) -> dict:
        row = {"file": os.path.basename(path), "path": path,
               "frozen": False, "first_change": "", "freeze_sec": 0.0, "error": ""}
        if self._cancel.is_set():
            row["error"] = "stopped"
            return row
        log_file("scan", path)
        args = [FFMPEG, "-hide_banner", "-i", path,
                "-t", f"{self.window:.3f}", "-vf", "freezedetect=n=-40dB:d=1",
                "-map", "0:v:0", "-an", "-f", "null", "-"]
        log_cmd("scan", args)
        proc = spawn(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        with self._lock:
            self._procs.add(proc)
        err = proc.stderr.read() if proc.stderr else ""
        proc.wait()
        with self._lock:
            self._procs.discard(proc)
        _release(proc)

        if self._cancel.is_set():
            row["error"] = "stopped"
            return row
        if proc.returncode != 0 and "freeze" not in err:
            row["error"] = "probe failed"
            return row

        frozen, end, has_end = parse_initial_freeze(err)
        if frozen:
            row["frozen"] = True
            if has_end:
                row["freeze_sec"] = end
                row["first_change"] = seconds_to_time(end)
            else:
                row["freeze_sec"] = self.window
                row["first_change"] = ">" + seconds_to_time(self.window)
        log(f"→ scan {row['file']}: "
            + (row["error"] or (f"frozen {row['first_change']}" if row["frozen"] else "no freeze")))
        return row

    def run(self):
        register_worker(self)
        try:
            self._run()
        finally:
            unregister_worker(self)

    def _run(self):
        files = sorted(
            str(Path(self.folder) / e)
            for e in os.listdir(self.folder)
            if (Path(self.folder) / e).is_file()
            and Path(e).suffix.lower() in VIDEO_EXTS
            and ".vt_tmp." not in e             # leftover replace-source temp
        )
        total = len(files)
        rows: list[dict | None] = [None] * total
        if total == 0:
            self.finished_rows.emit([])
            return

        def work(idx: int, path: str):
            row = self._scan_one(path)
            rows[idx] = row
            with self._lock:
                self._done += 1
                d = self._done
            self.progress.emit({"done": d, "total": total, **row})

        with ThreadPoolExecutor(max_workers=min(6, (os.cpu_count() or 2))) as ex:
            futures = []
            for i, path in enumerate(files):
                if self._cancel.is_set():
                    break
                futures.append(ex.submit(work, i, path))
            for _ in as_completed(futures):
                pass

        self.finished_rows.emit([r for r in rows if r])


class BatchThread(QThread):
    progress = pyqtSignal(dict)
    done = pyqtSignal(dict)

    def __init__(self, items: list[dict]):
        super().__init__()
        self.items = items
        self._cancel = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def cancel(self):
        self._cancel.set()
        with self._lock:
            if self._proc:
                try:
                    self._proc.kill()
                except Exception:
                    pass

    def _register(self, proc):
        with self._lock:
            self._proc = proc

    def run(self):
        register_worker(self)
        total = len(self.items)
        succeeded = failed = 0
        errors: list[str] = []
        try:
            for i, it in enumerate(self.items):
                if self._cancel.is_set():
                    break
                try:
                    ok, msg = trim_video(
                        input_path=it["path"], output_path="", start=it["start"],
                        end="", encoder_mode="smart", replace_source=True,
                        on_proc=self._register,
                    )
                except Exception as e:
                    ok, msg = False, f"Trim failed: {e}"
                if ok:
                    succeeded += 1
                else:
                    failed += 1
                    errors.append(f"{os.path.basename(it['path'])}: {msg}")
                self.progress.emit({"done": i + 1, "total": total,
                                    "file": os.path.basename(it["path"]), "success": ok})
        finally:
            unregister_worker(self)
        self.done.emit({"total": total, "succeeded": succeeded,
                        "failed": failed, "errors": errors})


# --------------------------------------------------------------------------
# Scan results dialog
# --------------------------------------------------------------------------

class ScanDialog(QDialog):
    """Table of scan results with per-file selection and batch trim."""

    load_file = pyqtSignal(str, str)   # (path, start_time)
    batch_started = pyqtSignal(list)   # source paths about to be replaced
    batch_finished = pyqtSignal()

    COLS = ["", "File", "Frozen intro", "First change", "Freeze (s)"]

    def __init__(self, rows: list[dict], window: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Folder Freeze Scan")
        self.resize(720, 460)
        self.rows = rows
        self.window = window
        self.batch: BatchThread | None = None

        v = QVBoxLayout(self)

        head = QHBoxLayout()
        frozen_n = sum(1 for r in rows if r.get("frozen") and not r.get("error"))
        self.title = QLabel(f"{len(rows)} file(s) — {frozen_n} with a frozen intro "
                            f"(first {window:g}s)")
        self.title.setStyleSheet("font-weight: 600;")
        head.addWidget(self.title)
        head.addStretch(1)
        self.trim_btn = QPushButton("Trim Selected (replace source)")
        self.trim_btn.setObjectName("primary")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.hide()
        self.close_btn = QPushButton("Close")
        head.addWidget(self.trim_btn)
        head.addWidget(self.stop_btn)
        head.addWidget(self.close_btn)
        v.addLayout(head)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #888;")
        v.addWidget(self.status)

        self.table = QTableWidget(len(rows), len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c in range(2, len(self.COLS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self.table, 1)

        self._fill()

        self.close_btn.clicked.connect(self.accept)
        self.trim_btn.clicked.connect(self._start_batch)
        self.stop_btn.clicked.connect(self._stop_batch)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.itemChanged.connect(lambda _=None: self._update_trim_btn())
        self._update_trim_btn()

    def _fill(self):
        # Frozen first, longest freeze first.
        self.rows.sort(key=lambda r: (r.get("frozen", False), r.get("freeze_sec", 0.0)),
                       reverse=True)
        green, red = QColor("#4ec994"), QColor("#f14c4c")
        for i, r in enumerate(self.rows):
            trimmable = (r.get("frozen") and not r.get("error")
                         and r.get("first_change") and not r["first_change"].startswith(">"))

            chk = QTableWidgetItem()
            if trimmable:
                chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                chk.setCheckState(Qt.CheckState.Checked)
            else:
                chk.setFlags(Qt.ItemFlag.ItemIsEnabled)
            chk.setData(Qt.ItemDataRole.UserRole, i)
            self.table.setItem(i, 0, chk)

            self.table.setItem(i, 1, QTableWidgetItem(r["file"]))
            if r.get("error"):
                frozen_txt, change, secs = "—", r["error"], "—"
            elif r.get("frozen"):
                frozen_txt = "Yes"
                change = r["first_change"]
                secs = f"{r['freeze_sec']:.2f}"
            else:
                frozen_txt, change, secs = "No", "—", "0.00"
            self.table.setItem(i, 2, QTableWidgetItem(frozen_txt))
            self.table.setItem(i, 3, QTableWidgetItem(change))
            self.table.setItem(i, 4, QTableWidgetItem(secs))

            if r.get("error"):
                for c in range(1, 5):
                    self.table.item(i, c).setForeground(red)
            elif r.get("frozen"):
                self.table.item(i, 2).setForeground(green)

    def _selected(self) -> list[dict]:
        out = []
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            if item and item.flags() & Qt.ItemFlag.ItemIsUserCheckable \
                    and item.checkState() == Qt.CheckState.Checked:
                r = self.rows[item.data(Qt.ItemDataRole.UserRole)]
                out.append(r)
        return out

    def _update_trim_btn(self):
        if self.batch:
            return
        n = len(self._selected())
        self.trim_btn.setEnabled(n > 0)
        self.trim_btn.setText(f"Trim {n} Selected (replace source)" if n
                              else "Trim Selected (replace source)")

    def _on_double_click(self, row: int, _col: int):
        r = self.rows[row]
        start = r["first_change"] if (r.get("frozen") and r.get("first_change")
                                      and not r["first_change"].startswith(">")) else ""
        self.load_file.emit(r["path"], start)
        self.accept()

    def _start_batch(self):
        rows = self._selected()
        if not rows:
            return
        if QMessageBox.question(
                self, "Trim & replace",
                f"Trim {len(rows)} file(s) from their detected start to the end and "
                f"REPLACE each source file?\nThis cannot be undone.") \
                != QMessageBox.StandardButton.Yes:
            return
        items = [{"path": r["path"], "start": r["first_change"]} for r in rows]
        self.trim_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.stop_btn.show()
        self.stop_btn.setEnabled(True)
        self.status.setText(f"Trimming {len(items)} file(s)…")

        self.batch_started.emit([it["path"] for it in items])
        self.batch = BatchThread(items)
        self.batch.progress.connect(self._on_batch_progress)
        self.batch.done.connect(self._on_batch_done)
        self.batch.start()

    def _stop_batch(self):
        if self.batch:
            self.stop_btn.setEnabled(False)
            self.status.setText("Stopping…")
            self.batch.cancel()

    def _on_batch_progress(self, p: dict):
        mark = "✓" if p["success"] else "✗"
        self.status.setText(f"Trimming {p['done']}/{p['total']}… last: {p['file']} {mark}")

    def _on_batch_done(self, res: dict):
        self.batch = None
        self.stop_btn.hide()
        self.close_btn.setEnabled(True)
        msg = f"Done — {res['succeeded']} trimmed, {res['failed']} failed of {res['total']}."
        if res["errors"]:
            msg += "  First error: " + res["errors"][0]
        self.status.setText(msg)
        self._update_trim_btn()
        self.batch_finished.emit()

    def closeEvent(self, e):
        if self.batch:
            self.batch.cancel()
            self.batch.wait(3000)
        super().closeEvent(e)


# --------------------------------------------------------------------------
# ffmpeg log window
# --------------------------------------------------------------------------

class LogWindow(QDialog):
    closed = pyqtSignal()

    def __init__(self, bus: LogBus, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ffmpeg Log")
        self.resize(780, 400)
        self.bus = bus

        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        lbl = QLabel("Every ffmpeg / ffprobe command and the file being processed.")
        lbl.setStyleSheet("color:#888;")
        bar.addWidget(lbl)
        bar.addStretch(1)
        clear = QPushButton("Clear"); clear.clicked.connect(lambda: self.text.clear())
        save = QPushButton("Save…"); save.clicked.connect(self._save)
        bar.addWidget(clear); bar.addWidget(save)
        v.addLayout(bar)

        self.text = QPlainTextEdit(); self.text.setReadOnly(True)
        self.text.setObjectName("log"); self.text.setMaximumBlockCount(5000)
        v.addWidget(self.text, 1)

        self.text.setPlainText("\n".join(bus.snapshot()))
        bus.line.connect(self.text.appendPlainText)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save log", "ffmpeg-log.txt",
                                              "Text (*.txt);;All Files (*)")
        if path:
            try:
                Path(path).write_text(self.text.toPlainText())
            except OSError as e:
                QMessageBox.warning(self, "Save failed", str(e))

    def closeEvent(self, e):
        self.closed.emit()
        super().closeEvent(e)


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

class VideoTrim(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VideoTrim")
        self.resize(920, 720)

        self.duration_ms = 0
        self.frame_ms = 33
        self.info_duration_ms = 0
        self.encoders: list[dict] = []
        self.current_path = ""
        self.detect_thread: DetectThread | None = None
        self.trim_thread: TrimThread | None = None
        self.scan_thread: ScanThread | None = None
        self.log_window: LogWindow | None = None
        self._preview_released = False

        self._build_ui()
        self._build_menu()
        self._setup_player()
        self._load_encoders()
        self._set_controls_enabled(False)

    def _build_menu(self):
        view = self.menuBar().addMenu("View")
        self.log_action = QAction("Show ffmpeg Log", self, checkable=True)
        self.log_action.setShortcut("Ctrl+L")
        self.log_action.toggled.connect(self._toggle_log_window)
        view.addAction(self.log_action)

    def _toggle_log_window(self, on: bool):
        if LOGBUS is None:
            return
        if on:
            if self.log_window is None:
                self.log_window = LogWindow(LOGBUS, self)
                self.log_window.closed.connect(lambda: self.log_action.setChecked(False))
            LOGBUS.enabled = True
            log("ffmpeg logging enabled")
            self.log_window.show()
            self.log_window.raise_()
        else:
            LOGBUS.enabled = False
            if self.log_window:
                self.log_window.hide()

    # ---- UI construction -------------------------------------------------

    def _group(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        box = QFrame()
        box.setObjectName("group")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        lbl = QLabel(title)
        lbl.setObjectName("groupTitle")
        lay.addWidget(lbl)
        return box, lay

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # File
        fbox, flay = self._group("Video File")
        row = QHBoxLayout()
        self.file_path = QLineEdit(); self.file_path.setReadOnly(True)
        self.file_path.setPlaceholderText("No file selected")
        browse = QPushButton("Browse…"); browse.clicked.connect(self._browse_file)
        row.addWidget(self.file_path, 1); row.addWidget(browse)
        flay.addLayout(row)
        root.addWidget(fbox)

        self.info_label = QLabel(""); self.info_label.setObjectName("info")
        root.addWidget(self.info_label)

        # Preview
        self.video = QVideoWidget()
        self.video.setMinimumHeight(260)
        self.video.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.video, 1)

        # Playback controls
        pc = QHBoxLayout()
        self.play_btn = QPushButton("▶"); self.play_btn.clicked.connect(self._toggle_play)
        self.back1 = QPushButton("-1s"); self.back1.clicked.connect(lambda: self._seek_by(-1000))
        self.backf = QPushButton("‹"); self.backf.clicked.connect(lambda: self._seek_by(-self.frame_ms))
        self.fwdf = QPushButton("›"); self.fwdf.clicked.connect(lambda: self._seek_by(self.frame_ms))
        self.fwd1 = QPushButton("+1s"); self.fwd1.clicked.connect(lambda: self._seek_by(1000))
        for b in (self.play_btn, self.back1, self.backf, self.fwdf, self.fwd1):
            b.setFixedWidth(46 if b is self.play_btn else 42)
            pc.addWidget(b)
        self.pos_label = QLabel("00:00:00.000"); self.pos_label.setObjectName("mono")
        pc.addWidget(self.pos_label)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self.slider.sliderReleased.connect(self._slider_released)
        self.slider.sliderMoved.connect(self._slider_moved)
        self._dragging = False
        pc.addWidget(self.slider, 1)
        self.total_label = QLabel("00:00:00.000"); self.total_label.setObjectName("mono")
        pc.addWidget(self.total_label)
        root.addLayout(pc)

        # Trim range
        tbox, tlay = self._group("Trim Range")
        tr = QHBoxLayout()
        tr.addWidget(QLabel("Start:"))
        self.start_in = QLineEdit("00:00:00.000"); self.start_in.setFixedWidth(110)
        self.start_in.textChanged.connect(self._on_times_changed)
        tr.addWidget(self.start_in)
        self.set_start = QPushButton("Set Start"); self.set_start.clicked.connect(self._set_start)
        tr.addWidget(self.set_start)
        self.detect_btn = QPushButton("Detect Start"); self.detect_btn.clicked.connect(self._detect_start)
        self.detect_btn.setToolTip("Auto-detect the first frame change (skips a frozen intro)")
        tr.addWidget(self.detect_btn)
        tr.addSpacing(16)
        tr.addWidget(QLabel("End:"))
        self.end_in = QLineEdit("00:00:00.000"); self.end_in.setFixedWidth(110)
        self.end_in.textChanged.connect(self._on_times_changed)
        tr.addWidget(self.end_in)
        self.set_end = QPushButton("Set End"); self.set_end.clicked.connect(self._set_end)
        tr.addWidget(self.set_end)
        tr.addStretch(1)
        self.duration_label = QLabel(""); self.duration_label.setObjectName("info")
        tr.addWidget(self.duration_label)
        tlay.addLayout(tr)
        root.addWidget(tbox)

        # Encoding
        ebox, elay = self._group("Encoding")
        er = QHBoxLayout()
        er.addWidget(QLabel("Mode:"))
        self.combo = QComboBox(); self.combo.currentIndexChanged.connect(self._update_hint)
        er.addWidget(self.combo)
        self.hint = QLabel(""); self.hint.setObjectName("info")
        er.addWidget(self.hint, 1)
        elay.addLayout(er)
        root.addWidget(ebox)

        # Output
        obox, olay = self._group("Output")
        orow = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("Auto-generated from input filename")
        self.output_browse = QPushButton("Browse…"); self.output_browse.clicked.connect(self._browse_output)
        orow.addWidget(self.output_path, 1); orow.addWidget(self.output_browse)
        olay.addLayout(orow)
        self.overwrite = QCheckBox("Overwrite source (replace the opened file with the trimmed result)")
        self.overwrite.toggled.connect(self._on_overwrite_toggled)
        olay.addWidget(self.overwrite)
        root.addWidget(obox)

        # Trim + global stop
        trow = QHBoxLayout()
        self.trim_btn = QPushButton("Trim Video"); self.trim_btn.setObjectName("primary")
        self.trim_btn.clicked.connect(self._trim)
        self.stop_all_btn = QPushButton("Stop All ffmpeg"); self.stop_all_btn.setObjectName("danger")
        self.stop_all_btn.setToolTip("Cancel every running ffmpeg process (trim, detect, scan, batch)")
        self.stop_all_btn.clicked.connect(self._stop_all)
        trow.addWidget(self.trim_btn, 1)
        trow.addWidget(self.stop_all_btn)
        root.addLayout(trow)

        # Folder freeze scan
        sbox, slay = self._group("Folder Freeze Scan")
        sr = QHBoxLayout()
        self.scan_btn = QPushButton("Scan Folder…"); self.scan_btn.clicked.connect(self._scan_folder)
        self.scan_stop = QPushButton("Stop"); self.scan_stop.setEnabled(False)
        self.scan_stop.clicked.connect(self._stop_scan)
        sr.addWidget(self.scan_btn); sr.addWidget(self.scan_stop)
        sr.addWidget(QLabel("First"))
        self.scan_window = QSpinBox(); self.scan_window.setRange(1, 120); self.scan_window.setValue(10)
        self.scan_window.setFixedWidth(60)
        sr.addWidget(self.scan_window); sr.addWidget(QLabel("sec"))
        self.log_toggle = QCheckBox("Log"); self.log_toggle.toggled.connect(self._toggle_log)
        sr.addWidget(self.log_toggle)
        self.scan_status = QLabel(""); self.scan_status.setObjectName("info")
        sr.addWidget(self.scan_status, 1)
        slay.addLayout(sr)
        self.scan_log = QPlainTextEdit(); self.scan_log.setReadOnly(True)
        self.scan_log.setObjectName("log"); self.scan_log.setFixedHeight(120)
        self.scan_log.hide()
        slay.addWidget(self.scan_log)
        root.addWidget(sbox)

        # Status
        self.status = QLabel(""); self.status.setObjectName("status")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

    # ---- Player ----------------------------------------------------------

    def _setup_player(self):
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_playback_state)
        self.player.errorOccurred.connect(self._on_player_error)

    # ---- Encoders --------------------------------------------------------

    def _load_encoders(self):
        self.encoders = available_encoders()
        self.combo.clear()
        self.combo.addItem("Smart (recommended — lossless when possible)", "smart")
        self.combo.addItem("Stream Copy (fast, no re-encoding)", "copy")
        for e in self.encoders:
            self.combo.addItem(e["label"], e["encoder"])
        self._update_hint()

    def _update_hint(self):
        val = self.combo.currentData()
        if val == "smart":
            self.hint.setText("Lossless copy when start is on a keyframe; else a near-lossless "
                              "hardware re-encode for a frame-accurate cut")
        elif val == "copy":
            self.hint.setText("Fastest — cuts on nearest keyframe, no quality loss")
        else:
            e = next((x for x in self.encoders if x["encoder"] == val), None)
            self.hint.setText(e["hint"] if e else "")

    # ---- Enable / disable ------------------------------------------------

    def _set_controls_enabled(self, on: bool):
        for w in (self.play_btn, self.back1, self.backf, self.fwdf, self.fwd1,
                  self.set_start, self.set_end, self.detect_btn, self.trim_btn, self.slider):
            w.setEnabled(on)

    # ---- File load -------------------------------------------------------

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Video", "",
            "Video Files (*.mp4 *.mkv *.avi *.mov *.ts *.flv *.wmv *.webm *.m4v *.mpg *.mpeg *.3gp);;All Files (*)")
        if path:
            self.load_video(path)

    def load_video(self, path: str, preset_start: str = ""):
        self.current_path = path
        self.file_path.setText(path)
        self._show_status("", "")
        self.info_label.setText("Loading…")

        info = get_video_info(path)
        if info.get("error"):
            self.info_label.setText("Error: " + info["error"])
            self._set_controls_enabled(False)
            return

        audio = f" | Audio: {info['acodec']}" if info["acodec"] else " | No audio"
        self.info_label.setText(f"Format: {info['format']} | Video: {info['vcodec']}{audio}")
        if info["fps"] > 0:
            self.frame_ms = max(1, round(1000 / info["fps"]))
        self.info_duration_ms = info["duration_ms"]

        # Reset from ffprobe, not the player: nothing carries over from the
        # previous file, and a file the preview can't decode is still trimmable.
        self.player.stop()
        self._set_duration(self.info_duration_ms)
        self.pos_label.setText(ms_to_time(0))
        self.slider.setValue(0)
        self.start_in.setText(preset_start or ms_to_time(0))
        self.end_in.setText(ms_to_time(self.info_duration_ms))

        self.player.setSource(QUrl.fromLocalFile(path))
        if Path(path).suffix.lower() not in PREVIEWABLE:
            self._show_status("Preview may not display this format; trimming still works.", "")

        self.output_path.setText(str(Path(path).with_name(Path(path).stem + "_trimmed" + Path(path).suffix)))
        self._set_controls_enabled(True)
        self._on_times_changed()

    # ---- Player events ---------------------------------------------------

    def _set_duration(self, ms: int):
        self.duration_ms = ms
        self.slider.setRange(0, ms)
        self.total_label.setText(ms_to_time(ms))

    def _on_duration(self, ms: int):
        if ms <= 0:
            return
        self._set_duration(ms)
        # End was set from ffprobe on load; only fill it if that was unknown.
        if time_to_ms(self.end_in.text()) <= 0:
            self.end_in.setText(ms_to_time(ms))
        self._on_times_changed()

    def _on_position(self, ms: int):
        if self._dragging:
            return
        self.pos_label.setText(ms_to_time(ms))
        self.slider.setValue(ms)

    def _on_playback_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("⏸" if playing else "▶")

    def _on_player_error(self, _err, msg):
        if msg:
            self._show_status("Preview: " + msg + " (trimming still works)", "error")

    # ---- Playback --------------------------------------------------------

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _seek_by(self, delta_ms: int):
        self.player.pause()
        new = max(0, min(self.player.position() + delta_ms, self.duration_ms))
        self.player.setPosition(new)

    def _slider_released(self):
        self._dragging = False
        self.player.setPosition(self.slider.value())

    def _slider_moved(self, val: int):
        self.pos_label.setText(ms_to_time(val))

    # ---- Trim range ------------------------------------------------------

    def _set_start(self):
        self.start_in.setText(ms_to_time(self.player.position()))

    def _set_end(self):
        self.end_in.setText(ms_to_time(self.player.position()))

    def _on_times_changed(self):
        s, e = time_to_ms(self.start_in.text()), time_to_ms(self.end_in.text())
        self.start_in.setStyleSheet("" if s >= 0 else "border:1px solid #f14c4c;")
        self.end_in.setStyleSheet("" if e >= 0 else "border:1px solid #f14c4c;")
        if s < 0 or e < 0:
            self.duration_label.setText("Invalid time format")
        elif e - s > 0:
            self.duration_label.setText("Duration: " + ms_to_time(e - s))
        else:
            self.duration_label.setText("Invalid range")

    def _detect_start(self):
        if not self.current_path:
            return
        self.detect_btn.setEnabled(False)
        self.detect_btn.setText("Detecting…")
        self._show_status("Detecting first frame change…", "")
        self.detect_thread = DetectThread(self.current_path)
        self.detect_thread.done.connect(self._on_detect_done)
        self.detect_thread.start()

    def _on_detect_done(self, ts: str):
        self.detect_btn.setEnabled(True)
        self.detect_btn.setText("Detect Start")
        if ts:
            self.start_in.setText(ts)
            self.player.setPosition(time_to_ms(ts))
            self._show_status("First frame change at " + ts + " — start set", "success")
        else:
            self._show_status("No frozen intro detected — start left unchanged.", "")

    # ---- Output ----------------------------------------------------------

    def _browse_output(self):
        start_dir = str(Path(self.output_path.text()).parent) if self.output_path.text() else ""
        path, _ = QFileDialog.getSaveFileName(self, "Save As", start_dir,
                                              "Video Files (*.mp4 *.mkv *.mov *.m4v);;All Files (*)")
        if path:
            self.output_path.setText(path)

    def _on_overwrite_toggled(self, on: bool):
        self.output_path.setDisabled(on)
        self.output_browse.setDisabled(on)

    # ---- Trim ------------------------------------------------------------

    def _trim(self):
        inp = self.current_path
        replace = self.overwrite.isChecked()
        out = self.output_path.text().strip()
        if not inp:
            self._show_status("Select a valid input file.", "error"); return
        if not replace and not out:
            self._show_status("Specify an output file path.", "error"); return

        s, e = time_to_ms(self.start_in.text()), time_to_ms(self.end_in.text())
        if s < 0 or e < 0:
            self._show_status("Invalid time format.", "error"); return
        if s >= e:
            self._show_status("End time must be after start time.", "error"); return

        if replace:
            if QMessageBox.question(
                    self, "Overwrite source",
                    f'Overwrite the source file "{Path(inp).name}" with the trimmed result?\n'
                    "This cannot be undone.") != QMessageBox.StandardButton.Yes:
                return
        elif os.path.exists(out):
            if QMessageBox.question(self, "File exists",
                                    f'"{Path(out).name}" already exists. Overwrite?') \
                    != QMessageBox.StandardButton.Yes:
                return

        self.player.pause()
        if replace:
            self._release_preview()
        self.trim_btn.setEnabled(False)
        self._show_status("Trimming…", "")
        self.trim_thread = TrimThread(
            input_path=inp, output_path="" if replace else out,
            start=ms_to_time(s), end=ms_to_time(e),
            encoder_mode=self.combo.currentData(), replace_source=replace,
        )
        self.trim_thread.done.connect(self._on_trim_done)
        self.trim_thread.start()

    def _on_trim_done(self, ok: bool, msg: str):
        self.trim_btn.setEnabled(True)
        self._restore_preview()
        self._show_status(msg, "success" if ok else "error")

    # The player keeps the file open; on Windows that makes os.replace() over
    # the source fail with a sharing violation, so unload it while replacing.
    def _release_preview(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self._preview_released = True

    def _restore_preview(self):
        # Full reload: the replaced file is shorter, so the range must reset.
        if self._preview_released:
            self._preview_released = False
            if self.current_path:
                self.load_video(self.current_path)

    def _on_batch_started(self, paths: list):
        if not self.current_path:
            return
        cur = os.path.normcase(os.path.abspath(self.current_path))
        if any(os.path.normcase(os.path.abspath(p)) == cur for p in paths):
            self._release_preview()

    # ---- Global stop -----------------------------------------------------

    def _stop_all(self):
        n = stop_all()
        if self.scan_thread:
            self.scan_thread.cancel()
        # Re-enable anything that may be left disabled by an aborted op.
        self.trim_btn.setEnabled(True)
        self.scan_btn.setEnabled(True)
        self.scan_stop.setEnabled(False)
        self.detect_btn.setEnabled(True)
        self.detect_btn.setText("Detect Start")
        self._show_status(f"Stopped — killed {n} ffmpeg process(es).", "error")

    # ---- Folder scan -----------------------------------------------------

    def _toggle_log(self, on: bool):
        self.scan_log.setVisible(on)

    def _scan_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Scan")
        if not folder:
            return
        self.scan_btn.setEnabled(False)
        self.scan_stop.setEnabled(True)
        self.scan_log.clear()
        self.scan_status.setText("Scanning…")
        self.scan_thread = ScanThread(folder, float(self.scan_window.value()))
        self.scan_thread.progress.connect(self._on_scan_progress)
        self.scan_thread.finished_rows.connect(self._on_scan_done)
        self.scan_thread.start()

    def _stop_scan(self):
        if self.scan_thread:
            self.scan_stop.setEnabled(False)
            self.scan_status.setText("Stopping…")
            self.scan_thread.cancel()

    def _on_scan_progress(self, p: dict):
        self.scan_status.setText(f"Scanning… {p['done']}/{p['total']}")
        if p.get("error"):
            result = f"⚠ {p['error']}"
        elif p.get("frozen"):
            result = f"frozen → {p['first_change']} ({p['freeze_sec']:.2f}s)"
        else:
            result = "no freeze"
        self.scan_log.appendPlainText(f"[{p['done']}/{p['total']}] {p['file']} — {result}")

    def _on_scan_done(self, rows: list):
        self.scan_btn.setEnabled(True)
        self.scan_stop.setEnabled(False)
        self.scan_thread = None
        if not rows:
            self.scan_status.setText("No video files found (or scan stopped before any completed).")
            return
        frozen_n = sum(1 for r in rows if r.get("frozen") and not r.get("error"))
        win = self.scan_window.value()
        self.scan_status.setText(f"{len(rows)} file(s) — {frozen_n} with a frozen intro (first {win}s).")
        dlg = ScanDialog(rows, float(win), self)
        dlg.load_file.connect(self.load_video)
        dlg.batch_started.connect(self._on_batch_started)
        dlg.batch_finished.connect(self._restore_preview)
        dlg.exec()

    # ---- Status ----------------------------------------------------------

    def _show_status(self, msg: str, kind: str):
        color = {"success": "#4ec994", "error": "#f14c4c"}.get(kind, "#888")
        self.status.setStyleSheet(f"color: {color};")
        self.status.setText(msg)

    def closeEvent(self, e):
        # Refuse new ffmpeg spawns (a Smart trim may be between its keyframe
        # probe and the encode), kill running ones, then let threads unwind so
        # none is destroyed mid-run and no ffmpeg outlives the app.
        _SHUTDOWN.set()
        stop_all()
        for t in (self.scan_thread, self.trim_thread, self.detect_thread):
            if t and t.isRunning():
                t.wait(5000)
        super().closeEvent(e)


STYLE = """
* { font-size: 13px; }
QMainWindow, QWidget { background: #1e1e1e; color: #cccccc; }
QFrame#group { border: 1px solid #3f3f46; border-radius: 4px; background: #252526; }
QLabel#groupTitle { color: #cccccc; font-weight: 600; }
QLabel#info { color: #888888; font-size: 12px; }
QLabel#mono { color: #cccccc; font-family: Menlo, monospace; }
QLabel#status { color: #888888; font-size: 12px; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
    background: #2d2d30; border: 1px solid #3f3f46; border-radius: 3px;
    padding: 4px 6px; color: #cccccc;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #0e7ad1; }
QPlainTextEdit#log { font-family: Menlo, monospace; font-size: 11px; color: #888; }
QPushButton {
    background: #3a3a3c; border: 1px solid #3f3f46; border-radius: 3px;
    padding: 5px 12px; color: #eee;
}
QPushButton:hover { background: #4a4a4e; }
QPushButton:disabled { color: #666; background: #303032; }
QPushButton#primary { background: #0e7ad1; border-color: #0e7ad1; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #1589e4; }
QPushButton#primary:disabled { background: #2a4a63; color: #99b; }
QPushButton#danger { background: #7a2323; border-color: #a13030; color: #ffd7d7; font-weight: 600; }
QPushButton#danger:hover { background: #a13030; }
QMenuBar { background: #1e1e1e; color: #ccc; }
QMenuBar::item:selected { background: #3a3a3c; }
QMenu { background: #252526; color: #ccc; border: 1px solid #3f3f46; }
QMenu::item:selected { background: #0e7ad1; }
QTableWidget { background: #252526; gridline-color: #3f3f46; }
QHeaderView::section { background: #2d2d30; color: #888; border: none; padding: 5px 8px; }
QSlider::groove:horizontal { height: 4px; background: #3f3f46; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; background: #0e7ad1; border-radius: 6px; margin: -5px 0; }
"""


def main():
    global LOGBUS
    app = QApplication(sys.argv)
    LOGBUS = LogBus()          # created after QApplication for cross-thread signals
    app.setStyleSheet(STYLE)
    ico = icon_path()
    if ico:
        app.setWindowIcon(QIcon(ico))
    win = VideoTrim()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
