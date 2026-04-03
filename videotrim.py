#!/usr/bin/env python3
"""VideoTrim - Simple video trimmer using ffmpeg."""

import json
import os
import subprocess
import sys
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

SUPPORTED_FORMATS = (
    "Video Files (*.mp4 *.mkv *.avi *.mov *.ts *.flv *.wmv *.webm *.m4v *.mpg *.mpeg *.3gp);;"
    "All Files (*)"
)

ENCODING_STREAM_COPY = "Stream Copy (fast, no re-encoding)"

# (label, ffmpeg encoder name, UI hint)
HW_ENCODERS = [
    ("H.264 (Apple VideoToolbox)", "h264_videotoolbox",
     "Hardware-accelerated H.264 — frame-accurate, fast"),
    ("HEVC (Apple VideoToolbox)", "hevc_videotoolbox",
     "Hardware-accelerated HEVC — frame-accurate, smaller files"),
    ("H.264 (Intel QSV)", "h264_qsv",
     "Hardware-accelerated H.264 via Intel Quick Sync"),
    ("HEVC (Intel QSV)", "hevc_qsv",
     "Hardware-accelerated HEVC via Intel Quick Sync"),
    ("AV1 (Intel QSV)", "av1_qsv",
     "Hardware-accelerated AV1 via Intel Quick Sync — best compression"),
]


def _probe_available_hw_encoders() -> list[tuple[str, str, str]]:
    """Probe ffmpeg for available hardware encoders."""
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


def _find_tool(name: str) -> str:
    """Find ffmpeg/ffprobe: bundled location first, then PATH."""
    exe_name = f"{name}.exe" if sys.platform == "win32" else name

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        if sys.platform == "darwin":
            # py2app .app bundle
            candidate = base.parent / "Resources" / "ffmpeg" / exe_name
        else:
            # PyInstaller on Windows / Linux
            candidate = base / "ffmpeg" / exe_name
        if candidate.is_file():
            return str(candidate)

    return exe_name


FFMPEG = _find_tool("ffmpeg")
FFPROBE = _find_tool("ffprobe")


def get_video_info(filepath: str) -> dict | None:
    """Get video stream info using ffprobe."""
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
    h = ms // 3_600_000
    m = (ms % 3_600_000) // 60_000
    s = (ms % 60_000) // 1000
    msec = ms % 1000
    return QTime(h, m, s, msec)


def qtime_to_ms(t: QTime) -> int:
    return t.hour() * 3_600_000 + t.minute() * 60_000 + t.second() * 1000 + t.msec()


def qtime_to_ffmpeg(t: QTime) -> str:
    return t.toString("HH:mm:ss.zzz")


def format_ms(ms: int) -> str:
    s, ms_rem = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms_rem:03d}"


class VideoTrimWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VideoTrim")
        self.setMinimumSize(700, 600)
        self.process = None
        self.video_duration_ms = 0
        self.frame_duration_ms = 33  # default ~30fps, updated on load
        self._slider_pressed = False
        self._available_hw_encoders = _probe_available_hw_encoders()
        self._build_ui()
        self._setup_player()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- File selection ---
        file_group = QGroupBox("Video File")
        file_layout = QHBoxLayout(file_group)

        self.file_path = QLineEdit()
        self.file_path.setReadOnly(True)
        self.file_path.setPlaceholderText("No file selected")
        file_layout.addWidget(self.file_path, stretch=1)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_file)
        file_layout.addWidget(browse_btn)

        layout.addWidget(file_group)

        # --- Video info ---
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: gray;")
        layout.addWidget(self.info_label)

        # --- Video preview ---
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(300)
        layout.addWidget(self.video_widget, stretch=1)

        # --- Playback controls ---
        playback_layout = QHBoxLayout()

        self.play_btn = QPushButton()
        self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_btn.setFixedWidth(40)
        self.play_btn.clicked.connect(self._toggle_play)
        self.play_btn.setEnabled(False)
        playback_layout.addWidget(self.play_btn)

        self.step_back_1s_btn = QPushButton("-1s")
        self.step_back_1s_btn.setFixedWidth(40)
        self.step_back_1s_btn.setEnabled(False)
        self.step_back_1s_btn.clicked.connect(lambda: self._step(-1000))
        playback_layout.addWidget(self.step_back_1s_btn)

        self.step_back_frame_btn = QPushButton("<")
        self.step_back_frame_btn.setFixedWidth(30)
        self.step_back_frame_btn.setEnabled(False)
        self.step_back_frame_btn.clicked.connect(lambda: self._step_frame(-1))
        playback_layout.addWidget(self.step_back_frame_btn)

        self.step_fwd_frame_btn = QPushButton(">")
        self.step_fwd_frame_btn.setFixedWidth(30)
        self.step_fwd_frame_btn.setEnabled(False)
        self.step_fwd_frame_btn.clicked.connect(lambda: self._step_frame(1))
        playback_layout.addWidget(self.step_fwd_frame_btn)

        self.step_fwd_1s_btn = QPushButton("+1s")
        self.step_fwd_1s_btn.setFixedWidth(40)
        self.step_fwd_1s_btn.setEnabled(False)
        self.step_fwd_1s_btn.clicked.connect(lambda: self._step(1000))
        playback_layout.addWidget(self.step_fwd_1s_btn)

        self.position_label = QLabel("00:00:00.000")
        self.position_label.setFixedWidth(90)
        playback_layout.addWidget(self.position_label)

        self.scrub_slider = QSlider(Qt.Orientation.Horizontal)
        self.scrub_slider.setRange(0, 0)
        self.scrub_slider.sliderPressed.connect(self._on_slider_pressed)
        self.scrub_slider.sliderReleased.connect(self._on_slider_released)
        self.scrub_slider.sliderMoved.connect(self._on_slider_moved)
        playback_layout.addWidget(self.scrub_slider, stretch=1)

        self.total_label = QLabel("00:00:00.000")
        self.total_label.setFixedWidth(90)
        playback_layout.addWidget(self.total_label)

        layout.addLayout(playback_layout)

        # --- Trim range with set buttons ---
        time_group = QGroupBox("Trim Range")
        time_layout = QHBoxLayout(time_group)

        time_layout.addWidget(QLabel("Start:"))
        self.start_time = QTimeEdit()
        self.start_time.setDisplayFormat("HH:mm:ss.zzz")
        self.start_time.setTime(QTime(0, 0, 0, 0))
        time_layout.addWidget(self.start_time)

        self.set_start_btn = QPushButton("Set Start")
        self.set_start_btn.setEnabled(False)
        self.set_start_btn.clicked.connect(self._set_start_from_player)
        time_layout.addWidget(self.set_start_btn)

        time_layout.addSpacing(20)

        time_layout.addWidget(QLabel("End:"))
        self.end_time = QTimeEdit()
        self.end_time.setDisplayFormat("HH:mm:ss.zzz")
        self.end_time.setTime(QTime(0, 0, 0, 0))
        time_layout.addWidget(self.end_time)

        self.set_end_btn = QPushButton("Set End")
        self.set_end_btn.setEnabled(False)
        self.set_end_btn.clicked.connect(self._set_end_from_player)
        time_layout.addWidget(self.set_end_btn)

        time_layout.addSpacing(20)

        self.duration_label = QLabel("")
        self.duration_label.setStyleSheet("color: gray;")
        time_layout.addWidget(self.duration_label)

        layout.addWidget(time_group)

        # --- Encoding mode ---
        encoding_group = QGroupBox("Encoding")
        encoding_layout = QHBoxLayout(encoding_group)

        encoding_layout.addWidget(QLabel("Mode:"))
        self.encoding_combo = QComboBox()
        self.encoding_combo.addItem(ENCODING_STREAM_COPY)
        for label, _enc, _hint in self._available_hw_encoders:
            self.encoding_combo.addItem(label)
        encoding_layout.addWidget(self.encoding_combo, stretch=1)

        self.encoding_hint = QLabel("")
        self.encoding_hint.setStyleSheet("color: gray;")
        encoding_layout.addWidget(self.encoding_hint)
        self.encoding_combo.currentTextChanged.connect(self._on_encoding_changed)
        self._on_encoding_changed(self.encoding_combo.currentText())

        layout.addWidget(encoding_group)

        # --- Output ---
        output_group = QGroupBox("Output")
        output_layout = QHBoxLayout(output_group)

        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("Auto-generated from input filename")
        output_layout.addWidget(self.output_path, stretch=1)

        output_browse_btn = QPushButton("Browse…")
        output_browse_btn.clicked.connect(self._browse_output)
        output_layout.addWidget(output_browse_btn)

        layout.addWidget(output_group)

        # --- Trim button + progress ---
        self.trim_btn = QPushButton("Trim Video")
        self.trim_btn.setEnabled(False)
        self.trim_btn.setMinimumHeight(40)
        self.trim_btn.clicked.connect(self._trim_video)
        layout.addWidget(self.trim_btn)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # Update duration label when times change
        self.start_time.timeChanged.connect(self._update_duration_label)
        self.end_time.timeChanged.connect(self._update_duration_label)

    def _on_encoding_changed(self, text: str):
        if text == ENCODING_STREAM_COPY:
            self.encoding_hint.setText("Fastest — cuts on nearest keyframe, no quality loss")
            return
        for label, _enc, hint in self._available_hw_encoders:
            if text == label:
                self.encoding_hint.setText(hint)
                return

    def _setup_player(self):
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Video", "", SUPPORTED_FORMATS)
        if not path:
            return

        self.file_path.setText(path)
        self._load_video(path)

    def _load_video(self, path: str):
        info = get_video_info(path)

        if info is None:
            self.info_label.setText("Could not read video info — is ffprobe installed?")
            self.trim_btn.setEnabled(False)
            return

        # Extract codec info and fps with validation
        video_codec = None
        audio_codec = None
        for stream in info.get("streams", []):
            codec_type = stream.get("codec_type")
            codec_name = stream.get("codec_name")
            if codec_type == "video" and codec_name:
                video_codec = codec_name
                r_fps = stream.get("r_frame_rate", "")
                if "/" in r_fps:
                    parts = r_fps.split("/")
                    if len(parts) == 2:
                        try:
                            num, den = float(parts[0]), float(parts[1])
                            if den > 0 and num > 0:
                                fps = num / den
                                self.frame_duration_ms = round(1000.0 / fps)
                        except ValueError:
                            pass
            elif codec_type == "audio" and codec_name:
                audio_codec = codec_name

        if video_codec is None:
            self.info_label.setText("No video stream found in file")
            self.trim_btn.setEnabled(False)
            return

        fmt_name = info.get("format", {}).get("format_name", "unknown")
        audio_info = f" | Audio: {audio_codec}" if audio_codec else " | No audio"
        self.info_label.setText(f"Format: {fmt_name} | Video: {video_codec}{audio_info}")

        # Load into player
        self.player.setSource(QUrl.fromLocalFile(path))

        # Auto-generate output path
        p = Path(path)
        self.output_path.setText(str(p.with_stem(p.stem + "_trimmed")))

        self.play_btn.setEnabled(True)
        self.step_back_1s_btn.setEnabled(True)
        self.step_back_frame_btn.setEnabled(True)
        self.step_fwd_frame_btn.setEnabled(True)
        self.step_fwd_1s_btn.setEnabled(True)
        self.set_start_btn.setEnabled(True)
        self.set_end_btn.setEnabled(True)
        self.trim_btn.setEnabled(True)
        self.status_label.setText("")

    def _on_duration_changed(self, duration_ms: int):
        self.video_duration_ms = duration_ms
        self.scrub_slider.setRange(0, duration_ms)
        self.total_label.setText(format_ms(duration_ms))

        end_qtime = ms_to_qtime(duration_ms)
        self.start_time.setTime(QTime(0, 0, 0, 0))
        self.end_time.setTime(end_qtime)
        self.start_time.setMaximumTime(end_qtime)
        self.end_time.setMaximumTime(end_qtime)

    def _on_position_changed(self, position_ms: int):
        self.position_label.setText(format_ms(position_ms))
        if not self._slider_pressed:
            self.scrub_slider.setValue(position_ms)

    def _on_playback_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _on_slider_pressed(self):
        self._slider_pressed = True

    def _on_slider_released(self):
        self._slider_pressed = False
        self.player.setPosition(self.scrub_slider.value())

    def _on_slider_moved(self, position_ms: int):
        self.position_label.setText(format_ms(position_ms))
        self.player.setPosition(position_ms)

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _step(self, delta_ms: int):
        self.player.pause()
        new_pos = max(0, min(self.player.position() + delta_ms, self.video_duration_ms))
        self.player.setPosition(new_pos)

    def _step_frame(self, frames: int):
        self._step(frames * self.frame_duration_ms)

    def _set_start_from_player(self):
        pos_ms = self.player.position()
        self.start_time.setTime(ms_to_qtime(pos_ms))

    def _set_end_from_player(self):
        pos_ms = self.player.position()
        self.end_time.setTime(ms_to_qtime(pos_ms))

    def _update_duration_label(self):
        start_ms = qtime_to_ms(self.start_time.time())
        end_ms = qtime_to_ms(self.end_time.time())
        diff_ms = end_ms - start_ms
        if diff_ms > 0:
            self.duration_label.setText(f"Duration: {format_ms(diff_ms)}")
        else:
            self.duration_label.setText("Invalid range")

    def _browse_output(self):
        current = self.output_path.text()
        start_dir = str(Path(current).parent) if current else ""
        path, _ = QFileDialog.getSaveFileName(self, "Save As", start_dir, SUPPORTED_FORMATS)
        if path:
            self.output_path.setText(path)

    def _trim_video(self):
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

        # Pause playback during trim
        self.player.pause()

        start = qtime_to_ffmpeg(self.start_time.time())
        end = qtime_to_ffmpeg(self.end_time.time())
        mode = self.encoding_combo.currentText()

        if mode == ENCODING_STREAM_COPY:
            # Stream copy: input seeking (-ss before -i) for speed
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
            # Re-encode: output seeking (-ss after -i) for frame accuracy
            video_codec = None
            for label, enc, _hint in self._available_hw_encoders:
                if mode == label:
                    video_codec = enc
                    break

            quality_args = []
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
        self.progress.setRange(0, 0)  # indeterminate
        self.status_label.setText("Trimming…")
        self.status_label.setStyleSheet("")

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.finished.connect(self._on_process_finished)
        self.process.start(cmd[0], cmd[1:])

    def _on_process_finished(self, exit_code, _exit_status):
        self.progress.setVisible(False)
        self.trim_btn.setEnabled(True)

        output_path = self.output_path.text()

        if exit_code == 0 and os.path.isfile(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            self.status_label.setText(f"Done! Saved ({size_mb:.1f} MB): {output_path}")
            self.status_label.setStyleSheet("color: green;")
        elif exit_code == 0:
            self.status_label.setText("ffmpeg reported success but output file was not created.")
            self.status_label.setStyleSheet("color: red;")
        else:
            output = self.process.readAll().data().decode(errors="replace")
            self.status_label.setText(f"ffmpeg failed (exit code {exit_code})")
            self.status_label.setStyleSheet("color: red;")
            QMessageBox.critical(
                self, "ffmpeg Error", output[-2000:] if len(output) > 2000 else output
            )

        self.process = None


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("VideoTrim")
    window = VideoTrimWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
