#!/usr/bin/env python3
"""VideoTrim - Simple video trimmer using ffmpeg."""

import json
import os
import subprocess
import sys
from functools import partial
from pathlib import Path

from PyQt6.QtCore import QProcess, QTime, Qt, QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QStyle,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_FORMATS = (
    "Video Files (*.mp4 *.mkv *.avi *.mov *.ts *.flv *.wmv *.webm *.m4v *.mpg *.mpeg *.3gp);;"
    "All Files (*)"
)

ENCODING_STREAM_COPY = "Stream Copy (fast, no re-encoding)"

# (label, ffmpeg encoder name, UI hint)
HW_ENCODERS = [
    (
        "H.264 (Apple VideoToolbox)",
        "h264_videotoolbox",
        "Hardware-accelerated H.264 — frame-accurate, fast",
    ),
    (
        "HEVC (Apple VideoToolbox)",
        "hevc_videotoolbox",
        "Hardware-accelerated HEVC — frame-accurate, smaller files",
    ),
    (
        "H.264 (Intel QSV)",
        "h264_qsv",
        "Hardware-accelerated H.264 via Intel Quick Sync",
    ),
    (
        "HEVC (Intel QSV)",
        "hevc_qsv",
        "Hardware-accelerated HEVC via Intel Quick Sync",
    ),
    (
        "AV1 (Intel QSV)",
        "av1_qsv",
        "Hardware-accelerated AV1 via Intel Quick Sync — best compression",
    ),
]

# UI colours — defined centrally so theme changes are a one-liner.
COLOR_DIM = "color: #888888;"
COLOR_SUCCESS = "color: #4ec994;"
COLOR_ERROR = "color: #f14c4c;"
COLOR_NORMAL = ""


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe discovery
# ---------------------------------------------------------------------------
# _find_tool is defined first so that FFMPEG / FFPROBE can be assigned at
# module level before _probe_available_hw_encoders (which references them)
# is ever called.


def _find_tool(name: str) -> str:
    """Return the path to *name* (ffmpeg or ffprobe).

    When running as a frozen PyInstaller bundle the binary is looked for in
    several candidate locations before falling back to the system PATH.
    """
    exe_name = f"{name}.exe" if sys.platform == "win32" else name

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        candidates: list[Path] = []

        if sys.platform == "darwin":
            # py2app / PyInstaller .app bundle — Resources and Frameworks dirs
            candidates.append(base.parent / "Resources" / "ffmpeg" / exe_name)
            candidates.append(base.parent / "Frameworks" / "ffmpeg" / exe_name)

        # PyInstaller onedir layouts:
        #   pre-6.x  → binary sits beside the executable
        #   6.x+     → binary lives under _internal/
        # PyInstaller onefile → extracted to _MEIPASS at runtime
        candidates.append(base / "ffmpeg" / exe_name)
        candidates.append(base / "_internal" / "ffmpeg" / exe_name)

        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "ffmpeg" / exe_name)

        for c in candidates:
            if c.is_file():
                return str(c)

    return exe_name  # fall back to PATH


FFMPEG = _find_tool("ffmpeg")
FFPROBE = _find_tool("ffprobe")


def _probe_available_hw_encoders() -> list[tuple[str, str, str]]:
    """Probe ffmpeg for available hardware encoders and return matching entries."""
    try:
        result = subprocess.run(
            [FFMPEG, "-encoders", "-hide_banner"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout
        return [(label, enc, hint) for label, enc, hint in HW_ENCODERS if enc in output]
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def get_video_info(filepath: str) -> dict | None:
    """Return the ffprobe JSON dict for *filepath*, or None on failure."""
    try:
        result = subprocess.run(
            [
                FFPROBE,
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-show_format",
                filepath,
            ],
            capture_output=True,
            text=True,
        )
        data = json.loads(result.stdout)
        if "format" not in data:
            return None
        return data
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return None


def ms_to_qtime(ms: int) -> QTime:
    # NOTE: QTimeEdit cannot represent times >= 24 h (Qt limitation).
    # Videos longer than 24 h will have their duration capped by the widget.
    h = ms // 3_600_000
    m = (ms % 3_600_000) // 60_000
    s = (ms % 60_000) // 1000
    msec = ms % 1000
    return QTime(h, m, s, msec)


def qtime_to_ms(t: QTime) -> int:
    return t.hour() * 3_600_000 + t.minute() * 60_000 + t.second() * 1000 + t.msec()


def qtime_to_ffmpeg(t: QTime) -> str:
    """Format a QTime as HH:mm:ss.zzz (accepted by ffmpeg -ss / -to)."""
    return t.toString("HH:mm:ss.zzz")


def format_ms(ms: int) -> str:
    s, ms_rem = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms_rem:03d}"


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class VideoTrimWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VideoTrim")
        self.setMinimumSize(700, 600)

        # Runtime state
        self.process: QProcess | None = None
        self._process_output: list[bytes] = []   # accumulated ffmpeg output
        self.video_duration_ms: int = 0
        self.frame_duration_ms: int = 33          # default ~30 fps; updated on load
        self._slider_pressed: bool = False
        self._available_hw_encoders: list[tuple[str, str, str]] = (
            _probe_available_hw_encoders()
        )

        self._build_ui()
        self._setup_player()

    # ------------------------------------------------------------------
    # UI construction (split into focused helpers)
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.addWidget(self._build_file_group())

        self.info_label = QLabel("")
        self.info_label.setStyleSheet(COLOR_DIM)
        layout.addWidget(self.info_label)

        layout.addWidget(self._build_video_preview(), stretch=1)
        layout.addLayout(self._build_playback_controls())
        layout.addWidget(self._build_trim_group())
        layout.addWidget(self._build_encoding_group())
        layout.addWidget(self._build_output_group())
        layout.addWidget(self._build_trim_button())
        layout.addWidget(self._build_progress())
        layout.addWidget(self._build_status_label())

        # Cross-widget signal wiring that needs both groups to exist
        self.start_time.timeChanged.connect(self._update_duration_label)
        self.end_time.timeChanged.connect(self._update_duration_label)

    def _build_file_group(self) -> QGroupBox:
        group = QGroupBox("Video File")
        row = QHBoxLayout(group)

        self.file_path = QLineEdit()
        self.file_path.setReadOnly(True)
        self.file_path.setPlaceholderText("No file selected")
        row.addWidget(self.file_path, stretch=1)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_file)
        row.addWidget(browse_btn)

        return group

    def _build_video_preview(self) -> QVideoWidget:
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(300)
        return self.video_widget

    def _build_playback_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self.play_btn = QPushButton()
        self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_btn.setFixedWidth(40)
        self.play_btn.clicked.connect(self._toggle_play)
        self.play_btn.setEnabled(False)
        row.addWidget(self.play_btn)

        # Step buttons use partial so they are named callables (easier to
        # disconnect, inspect, or extend later).
        self.step_back_1s_btn = QPushButton("-1s")
        self.step_back_1s_btn.setFixedWidth(40)
        self.step_back_1s_btn.setEnabled(False)
        self.step_back_1s_btn.clicked.connect(partial(self._step, -1000))
        row.addWidget(self.step_back_1s_btn)

        self.step_back_frame_btn = QPushButton("<")
        self.step_back_frame_btn.setFixedWidth(30)
        self.step_back_frame_btn.setEnabled(False)
        self.step_back_frame_btn.clicked.connect(partial(self._step_frame, -1))
        row.addWidget(self.step_back_frame_btn)

        self.step_fwd_frame_btn = QPushButton(">")
        self.step_fwd_frame_btn.setFixedWidth(30)
        self.step_fwd_frame_btn.setEnabled(False)
        self.step_fwd_frame_btn.clicked.connect(partial(self._step_frame, 1))
        row.addWidget(self.step_fwd_frame_btn)

        self.step_fwd_1s_btn = QPushButton("+1s")
        self.step_fwd_1s_btn.setFixedWidth(40)
        self.step_fwd_1s_btn.setEnabled(False)
        self.step_fwd_1s_btn.clicked.connect(partial(self._step, 1000))
        row.addWidget(self.step_fwd_1s_btn)

        self.position_label = QLabel("00:00:00.000")
        self.position_label.setFixedWidth(90)
        row.addWidget(self.position_label)

        self.scrub_slider = QSlider(Qt.Orientation.Horizontal)
        self.scrub_slider.setRange(0, 0)
        self.scrub_slider.setEnabled(False)
        self.scrub_slider.sliderPressed.connect(self._on_slider_pressed)
        self.scrub_slider.sliderReleased.connect(self._on_slider_released)
        self.scrub_slider.sliderMoved.connect(self._on_slider_moved)
        row.addWidget(self.scrub_slider, stretch=1)

        self.total_label = QLabel("00:00:00.000")
        self.total_label.setFixedWidth(90)
        row.addWidget(self.total_label)

        return row

    def _build_trim_group(self) -> QGroupBox:
        group = QGroupBox("Trim Range")
        row = QHBoxLayout(group)

        row.addWidget(QLabel("Start:"))
        self.start_time = QTimeEdit()
        self.start_time.setDisplayFormat("HH:mm:ss.zzz")
        self.start_time.setTime(QTime(0, 0, 0, 0))
        row.addWidget(self.start_time)

        self.set_start_btn = QPushButton("Set Start")
        self.set_start_btn.setEnabled(False)
        self.set_start_btn.clicked.connect(self._set_start_from_player)
        row.addWidget(self.set_start_btn)

        row.addSpacing(20)

        row.addWidget(QLabel("End:"))
        self.end_time = QTimeEdit()
        self.end_time.setDisplayFormat("HH:mm:ss.zzz")
        self.end_time.setTime(QTime(0, 0, 0, 0))
        row.addWidget(self.end_time)

        self.set_end_btn = QPushButton("Set End")
        self.set_end_btn.setEnabled(False)
        self.set_end_btn.clicked.connect(self._set_end_from_player)
        row.addWidget(self.set_end_btn)

        row.addSpacing(20)

        self.duration_label = QLabel("")
        self.duration_label.setStyleSheet(COLOR_DIM)
        row.addWidget(self.duration_label)

        return group

    def _build_encoding_group(self) -> QGroupBox:
        group = QGroupBox("Encoding")
        row = QHBoxLayout(group)

        row.addWidget(QLabel("Mode:"))
        self.encoding_combo = QComboBox()
        self.encoding_combo.addItem(ENCODING_STREAM_COPY)
        for label, _enc, _hint in self._available_hw_encoders:
            self.encoding_combo.addItem(label)
        row.addWidget(self.encoding_combo, stretch=1)

        self.encoding_hint = QLabel("")
        self.encoding_hint.setStyleSheet(COLOR_DIM)
        row.addWidget(self.encoding_hint)

        self.encoding_combo.currentTextChanged.connect(self._on_encoding_changed)
        self._on_encoding_changed(self.encoding_combo.currentText())

        return group

    def _build_output_group(self) -> QGroupBox:
        group = QGroupBox("Output")
        row = QHBoxLayout(group)

        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("Auto-generated from input filename")
        row.addWidget(self.output_path, stretch=1)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_output)
        row.addWidget(browse_btn)

        return group

    def _build_trim_button(self) -> QPushButton:
        self.trim_btn = QPushButton("Trim Video")
        self.trim_btn.setEnabled(False)
        self.trim_btn.setMinimumHeight(40)
        self.trim_btn.clicked.connect(self._trim_video)
        return self.trim_btn

    def _build_progress(self) -> QProgressBar:
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        return self.progress

    def _build_status_label(self) -> QLabel:
        self.status_label = QLabel("")
        return self.status_label

    # ------------------------------------------------------------------
    # Player setup
    # ------------------------------------------------------------------

    def _setup_player(self) -> None:
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_player_error)

    # ------------------------------------------------------------------
    # Window lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        """Kill any in-progress ffmpeg process before closing."""
        if self.process is not None:
            if self.process.state() != QProcess.ProcessState.NotRunning:
                self.process.kill()
                self.process.waitForFinished(2000)
        event.accept()

    # ------------------------------------------------------------------
    # Encoding combo
    # ------------------------------------------------------------------

    def _on_encoding_changed(self, text: str) -> None:
        if text == ENCODING_STREAM_COPY:
            self.encoding_hint.setText("Fastest — cuts on nearest keyframe, no quality loss")
            return
        for label, _enc, hint in self._available_hw_encoders:
            if text == label:
                self.encoding_hint.setText(hint)
                return

    # ------------------------------------------------------------------
    # File browsing & video loading
    # ------------------------------------------------------------------

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Video", "", SUPPORTED_FORMATS)
        if path:
            self.file_path.setText(path)
            self._load_video(path)

    def _load_video(self, path: str) -> None:
        # Reset stale UI state immediately so nothing from the previous file
        # persists while the new file is being probed / loaded.
        self._reset_player_ui()

        info = get_video_info(path)
        if info is None:
            self.info_label.setText("Could not read video info — is ffprobe installed?")
            self.info_label.setStyleSheet(COLOR_ERROR)
            return

        # Extract codec and FPS from the first video/audio streams found.
        video_codec: str | None = None
        audio_codec: str | None = None
        for stream in info.get("streams", []):
            codec_type = stream.get("codec_type")
            codec_name = stream.get("codec_name")
            if codec_type == "video" and codec_name and video_codec is None:
                video_codec = codec_name
                r_fps = stream.get("r_frame_rate", "")
                if "/" in r_fps:
                    parts = r_fps.split("/")
                    if len(parts) == 2:
                        try:
                            num, den = float(parts[0]), float(parts[1])
                            if den > 0 and num > 0:
                                self.frame_duration_ms = round(1000.0 / (num / den))
                        except ValueError:
                            pass
            elif codec_type == "audio" and codec_name and audio_codec is None:
                audio_codec = codec_name

        if video_codec is None:
            self.info_label.setText("No video stream found in file")
            self.info_label.setStyleSheet(COLOR_ERROR)
            return

        fmt_name = info.get("format", {}).get("format_name", "unknown")
        audio_info = f" | Audio: {audio_codec}" if audio_codec else " | No audio"
        self.info_label.setText(f"Format: {fmt_name} | Video: {video_codec}{audio_info}")
        self.info_label.setStyleSheet(COLOR_DIM)

        self.player.setSource(QUrl.fromLocalFile(path))

        p = Path(path)
        self.output_path.setText(str(p.with_stem(p.stem + "_trimmed")))

        self._set_controls_enabled(True)
        self.status_label.setText("")
        self.status_label.setStyleSheet(COLOR_NORMAL)

    def _reset_player_ui(self) -> None:
        """Clear all playback-related UI to a blank state."""
        self.player.stop()
        self.player.setSource(QUrl())          # detach any previous source

        self.frame_duration_ms = 33            # back to ~30 fps default
        self.video_duration_ms = 0
        self._slider_pressed = False

        self.scrub_slider.setRange(0, 0)
        self.scrub_slider.setValue(0)
        self.position_label.setText("00:00:00.000")
        self.total_label.setText("00:00:00.000")

        self.start_time.setTime(QTime(0, 0, 0, 0))
        self.end_time.setTime(QTime(0, 0, 0, 0))
        self.duration_label.setText("")

        self.info_label.setText("")
        self.info_label.setStyleSheet(COLOR_DIM)

        self._set_controls_enabled(False)

    def _set_controls_enabled(self, on: bool) -> None:
        self.play_btn.setEnabled(on)
        self.step_back_1s_btn.setEnabled(on)
        self.step_back_frame_btn.setEnabled(on)
        self.step_fwd_frame_btn.setEnabled(on)
        self.step_fwd_1s_btn.setEnabled(on)
        self.scrub_slider.setEnabled(on)
        self.set_start_btn.setEnabled(on)
        self.set_end_btn.setEnabled(on)
        self.trim_btn.setEnabled(on)

    # ------------------------------------------------------------------
    # Player signals
    # ------------------------------------------------------------------

    def _on_duration_changed(self, duration_ms: int) -> None:
        self.video_duration_ms = duration_ms
        self.scrub_slider.setRange(0, duration_ms)
        self.total_label.setText(format_ms(duration_ms))

        end_qtime = ms_to_qtime(duration_ms)
        self.start_time.setTime(QTime(0, 0, 0, 0))
        self.end_time.setTime(end_qtime)
        self.start_time.setMaximumTime(end_qtime)
        self.end_time.setMaximumTime(end_qtime)

    def _on_position_changed(self, position_ms: int) -> None:
        self.position_label.setText(format_ms(position_ms))
        if not self._slider_pressed:
            self.scrub_slider.setValue(position_ms)

    def _on_playback_state_changed(self, state) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _on_player_error(self, error, error_string: str) -> None:
        """Show a non-blocking warning when Qt's media player can't decode a file."""
        # Media errors are common for formats the OS codec pack doesn't support
        # (e.g. HEVC on Windows without the HEVC Video Extensions).
        # Trimming via ffmpeg still works even if preview fails.
        self._set_status(
            f"Preview unavailable: {error_string} "
            "(trimming still works — ffmpeg handles all formats)",
            COLOR_ERROR,
        )

    # ------------------------------------------------------------------
    # Scrub slider
    # ------------------------------------------------------------------

    def _on_slider_pressed(self) -> None:
        self._slider_pressed = True

    def _on_slider_released(self) -> None:
        self._slider_pressed = False
        self.player.setPosition(self.scrub_slider.value())

    def _on_slider_moved(self, position_ms: int) -> None:
        self.position_label.setText(format_ms(position_ms))
        self.player.setPosition(position_ms)

    # ------------------------------------------------------------------
    # Playback controls
    # ------------------------------------------------------------------

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _step(self, delta_ms: int) -> None:
        self.player.pause()
        new_pos = max(0, min(self.player.position() + delta_ms, self.video_duration_ms))
        self.player.setPosition(new_pos)

    def _step_frame(self, frames: int) -> None:
        self._step(frames * self.frame_duration_ms)

    def _set_start_from_player(self) -> None:
        self.start_time.setTime(ms_to_qtime(self.player.position()))

    def _set_end_from_player(self) -> None:
        self.end_time.setTime(ms_to_qtime(self.player.position()))

    # ------------------------------------------------------------------
    # Trim range label
    # ------------------------------------------------------------------

    def _update_duration_label(self) -> None:
        start_ms = qtime_to_ms(self.start_time.time())
        end_ms = qtime_to_ms(self.end_time.time())
        diff_ms = end_ms - start_ms
        if diff_ms > 0:
            self.duration_label.setText(f"Duration: {format_ms(diff_ms)}")
        else:
            self.duration_label.setText("Invalid range")

    # ------------------------------------------------------------------
    # Output browsing
    # ------------------------------------------------------------------

    def _browse_output(self) -> None:
        current = self.output_path.text()
        start_dir = str(Path(current).parent) if current else ""
        path, _ = QFileDialog.getSaveFileName(self, "Save As", start_dir, SUPPORTED_FORMATS)
        if path:
            self.output_path.setText(path)

    # ------------------------------------------------------------------
    # Trim
    # ------------------------------------------------------------------

    def _trim_video(self) -> None:
        input_path = self.file_path.text()
        output_path = self.output_path.text().strip()

        if not input_path or not os.path.isfile(input_path):
            QMessageBox.warning(self, "Error", "Please select a valid input file.")
            return

        if not output_path:
            QMessageBox.warning(self, "Error", "Please specify an output file path.")
            return

        if os.path.abspath(input_path) == os.path.abspath(output_path):
            QMessageBox.warning(self, "Error", "Output file cannot be the same as input file.")
            return

        start_ms = qtime_to_ms(self.start_time.time())
        end_ms = qtime_to_ms(self.end_time.time())

        if start_ms >= end_ms:
            QMessageBox.warning(self, "Error", "End time must be after start time.")
            return

        if os.path.exists(output_path):
            reply = QMessageBox.question(
                self,
                "File Exists",
                f"'{Path(output_path).name}' already exists. Overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.player.pause()

        start = qtime_to_ffmpeg(self.start_time.time())
        end = qtime_to_ffmpeg(self.end_time.time())
        mode = self.encoding_combo.currentText()

        if mode == ENCODING_STREAM_COPY:
            # Input seeking: fast, cuts on the nearest keyframe.
            cmd = [
                FFMPEG, "-y",
                "-ss", start,
                "-to", end,
                "-i", input_path,
                "-c", "copy",
                "-map", "0",
                "-avoid_negative_ts", "make_zero",
                output_path,
            ]
        else:
            # Output seeking: frame-accurate re-encode.
            video_codec: str | None = None
            for label, enc, _hint in self._available_hw_encoders:
                if mode == label:
                    video_codec = enc
                    break

            if video_codec is None:
                # This should not happen in normal use, but guard against it
                # to avoid a TypeError from the "in" check below.
                QMessageBox.warning(self, "Error", "Unknown encoder mode selected.")
                return

            quality_args: list[str] = []
            if "videotoolbox" in video_codec:
                quality_args = ["-q:v", "65"]
            elif "qsv" in video_codec:
                quality_args = ["-global_quality", "18"]

            cmd = [
                FFMPEG, "-y",
                "-i", input_path,
                "-ss", start,
                "-to", end,
                "-c:v", video_codec,
                *quality_args,
                "-c:a", "aac",
                "-map", "0",
                "-avoid_negative_ts", "make_zero",
                output_path,
            ]

        self.trim_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)   # indeterminate
        self._set_status("Trimming…", COLOR_NORMAL)

        self._process_output = []      # reset accumulator for this run
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

        # Drain stdout/stderr continuously so the buffer stays small even for
        # very long transcodes and all output is available on error.
        self.process.readyReadStandardOutput.connect(self._on_process_output)

        self.process.finished.connect(self._on_process_finished)
        self.process.start(cmd[0], cmd[1:])

    def _on_process_output(self) -> None:
        """Drain the QProcess output buffer as data arrives."""
        if self.process is not None:
            chunk = self.process.readAllStandardOutput().data()
            self._process_output.append(chunk)

    def _on_process_finished(self, exit_code: int, _exit_status) -> None:
        self.progress.setVisible(False)
        self.trim_btn.setEnabled(True)

        output_path = self.output_path.text()

        if exit_code == 0 and os.path.isfile(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            self._set_status(
                f"Done! Saved ({size_mb:.1f} MB): {output_path}",
                COLOR_SUCCESS,
            )
        elif exit_code == 0:
            self._set_status(
                "ffmpeg reported success but output file was not created.",
                COLOR_ERROR,
            )
        else:
            full_output = b"".join(self._process_output).decode(errors="replace")
            tail = full_output[-2000:] if len(full_output) > 2000 else full_output
            self._set_status(f"ffmpeg failed (exit code {exit_code})", COLOR_ERROR)
            QMessageBox.critical(self, "ffmpeg Error", tail)

        self.process = None
        self._process_output = []

    # ------------------------------------------------------------------
    # Status helper
    # ------------------------------------------------------------------

    def _set_status(self, message: str, style: str) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(style)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("VideoTrim")
    window = VideoTrimWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
