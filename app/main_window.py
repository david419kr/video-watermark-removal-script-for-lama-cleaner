from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import threading
import traceback

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .config import AppConfig, Paths
from .lama_manager import LamaCleanerManager
from .mask_editor import MaskEditorDialog
from .media_utils import extract_reference_frame, format_seconds, get_video_info
from .models import ProcessConfig, Segment, VideoInfo
from .pipeline import PipelineCancelled, VideoProcessingPipeline


class ProcessingWorker(QThread):
    log_message = Signal(str)
    progress_update = Signal(int, int)
    run_success = Signal(str)
    run_error = Signal(str)
    run_cancelled = Signal()

    def __init__(self, paths: Paths, config: ProcessConfig) -> None:
        super().__init__()
        self.paths = paths
        self.config = config
        self.cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        pipeline = VideoProcessingPipeline(
            paths=self.paths,
            log_cb=self.log_message.emit,
            progress_cb=self.progress_update.emit,
            cancel_event=self.cancel_event,
        )
        try:
            output_path = pipeline.run(self.config)
            self.run_success.emit(str(output_path))
        except PipelineCancelled:
            self.run_cancelled.emit()
        except Exception as exc:  # pylint: disable=broad-except
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.run_error.emit(detail)


class MainWindow(QMainWindow):
    def __init__(self, repo_root: Path) -> None:
        super().__init__()
        self.setWindowTitle("Lama Cleaner Video GUI (refactor/gui)")
        self.resize(1400, 920)

        self.paths = Paths(repo_root)
        self.paths.workspace_root.mkdir(parents=True, exist_ok=True)
        self.paths.workspace_jobs.mkdir(parents=True, exist_ok=True)
        self.paths.workspace_masks.mkdir(parents=True, exist_ok=True)

        self.video_info: VideoInfo | None = None
        self.segments: list[Segment] = []
        self.worker: ProcessingWorker | None = None
        self._slider_dragging = False

        self.lama_manager: LamaCleanerManager | None = None

        self._build_ui()
        self._wire_player()
        self._init_lama_manager()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout()
        root.setLayout(root_layout)
        self.setCentralWidget(root)

        root_layout.addWidget(self._build_io_group())
        root_layout.addWidget(self._build_lama_group())
        root_layout.addWidget(self._build_preview_group(), 2)
        root_layout.addWidget(self._build_segment_group(), 2)
        root_layout.addWidget(self._build_run_group())
        root_layout.addWidget(self._build_log_group(), 1)

    def _build_io_group(self) -> QGroupBox:
        box = QGroupBox("Input / Output")
        layout = QGridLayout()

        self.video_path_edit = QLineEdit()
        self.output_path_edit = QLineEdit()

        browse_video_btn = QPushButton("Browse Video")
        browse_video_btn.clicked.connect(self._browse_video)

        browse_output_btn = QPushButton("Browse Output")
        browse_output_btn.clicked.connect(self._browse_output)

        layout.addWidget(QLabel("Video"), 0, 0)
        layout.addWidget(self.video_path_edit, 0, 1)
        layout.addWidget(browse_video_btn, 0, 2)
        layout.addWidget(QLabel("Output"), 1, 0)
        layout.addWidget(self.output_path_edit, 1, 1)
        layout.addWidget(browse_output_btn, 1, 2)

        box.setLayout(layout)
        return box

    def _build_lama_group(self) -> QGroupBox:
        box = QGroupBox("Lama Cleaner Instances")
        layout = QHBoxLayout()

        self.instance_spin = QSpinBox()
        self.instance_spin.setMinimum(1)
        self.instance_spin.setMaximum(AppConfig.MAX_INSTANCE_COUNT)
        self.instance_spin.setValue(AppConfig.DEFAULT_INSTANCE_COUNT)

        apply_btn = QPushButton("Apply Instance Count")
        apply_btn.clicked.connect(self._apply_instance_count)

        self.ports_label = QLabel("Running Ports: -")
        self.ports_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        layout.addWidget(QLabel("Instances"))
        layout.addWidget(self.instance_spin)
        layout.addWidget(apply_btn)
        layout.addWidget(self.ports_label, 1)

        box.setLayout(layout)
        return box

    def _build_preview_group(self) -> QGroupBox:
        box = QGroupBox("Video Preview + Timeline")
        layout = QVBoxLayout()

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(260)

        controls = QHBoxLayout()
        self.play_pause_btn = QPushButton("Play")
        self.play_pause_btn.clicked.connect(self._toggle_play_pause)
        controls.addWidget(self.play_pause_btn)

        self.current_time_label = QLabel("00:00:00.000")
        self.duration_label = QLabel("00:00:00.000")
        controls.addWidget(self.current_time_label)
        controls.addWidget(QLabel("/"))
        controls.addWidget(self.duration_label)
        controls.addStretch(1)

        self.timeline_slider = QSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 0)
        self.timeline_slider.sliderPressed.connect(self._on_slider_pressed)
        self.timeline_slider.sliderReleased.connect(self._on_slider_released)
        self.timeline_slider.sliderMoved.connect(self._on_slider_moved)

        segment_row = QHBoxLayout()
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setDecimals(3)
        self.start_spin.setMinimum(0.0)
        self.start_spin.setMaximum(0.0)
        self.start_spin.setSingleStep(0.1)

        self.end_spin = QDoubleSpinBox()
        self.end_spin.setDecimals(3)
        self.end_spin.setMinimum(0.0)
        self.end_spin.setMaximum(0.0)
        self.end_spin.setSingleStep(0.1)

        set_start_btn = QPushButton("Set Start = Current")
        set_end_btn = QPushButton("Set End = Current")
        set_start_btn.clicked.connect(self._set_start_from_current)
        set_end_btn.clicked.connect(self._set_end_from_current)

        segment_row.addWidget(QLabel("Start (s)"))
        segment_row.addWidget(self.start_spin)
        segment_row.addWidget(set_start_btn)
        segment_row.addSpacing(12)
        segment_row.addWidget(QLabel("End (s)"))
        segment_row.addWidget(self.end_spin)
        segment_row.addWidget(set_end_btn)
        segment_row.addStretch(1)

        layout.addWidget(self.video_widget, 1)
        layout.addLayout(controls)
        layout.addWidget(self.timeline_slider)
        layout.addLayout(segment_row)
        box.setLayout(layout)
        return box

    def _build_segment_group(self) -> QGroupBox:
        box = QGroupBox("Segments and Masks")
        layout = QVBoxLayout()

        self.segment_table = QTableWidget(0, 4)
        self.segment_table.setHorizontalHeaderLabels(["Start", "End", "Mask", "Segment ID"])
        self.segment_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.segment_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.segment_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.segment_table.verticalHeader().setVisible(False)
        self.segment_table.setAlternatingRowColors(True)
        self.segment_table.setColumnWidth(0, 130)
        self.segment_table.setColumnWidth(1, 130)
        self.segment_table.setColumnWidth(2, 620)
        self.segment_table.setColumnWidth(3, 140)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Segment")
        remove_btn = QPushButton("Remove Selected")
        mask_file_btn = QPushButton("Assign Mask File")
        draw_mask_btn = QPushButton("Draw Mask")

        add_btn.clicked.connect(self._add_segment)
        remove_btn.clicked.connect(self._remove_selected_segment)
        mask_file_btn.clicked.connect(self._assign_mask_file)
        draw_mask_btn.clicked.connect(self._draw_mask_for_selected)

        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addWidget(mask_file_btn)
        btn_row.addWidget(draw_mask_btn)
        btn_row.addStretch(1)

        layout.addWidget(self.segment_table, 1)
        layout.addLayout(btn_row)
        box.setLayout(layout)
        return box

    def _build_run_group(self) -> QGroupBox:
        box = QGroupBox("Run")
        layout = QFormLayout()

        row = QHBoxLayout()
        self.keep_temp_box = QCheckBox("Keep temporary job folder")
        self.start_btn = QPushButton("Start Processing")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start_processing)
        self.cancel_btn.clicked.connect(self._cancel_processing)

        row.addWidget(self.keep_temp_box)
        row.addStretch(1)
        row.addWidget(self.start_btn)
        row.addWidget(self.cancel_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        layout.addRow(row)
        layout.addRow(QLabel("Progress"), self.progress_bar)
        box.setLayout(layout)
        return box

    def _build_log_group(self) -> QGroupBox:
        box = QGroupBox("Logs")
        layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        box.setLayout(layout)
        return box

    def _wire_player(self) -> None:
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._on_player_position_changed)
        self.player.durationChanged.connect(self._on_player_duration_changed)

    def _init_lama_manager(self) -> None:
        try:
            self.lama_manager = LamaCleanerManager(paths=self.paths, log_fn=self.log)
        except Exception as exc:  # pylint: disable=broad-except
            self.log(f"Failed to initialize lama manager: {exc}")
            self._error(
                "Failed to initialize lama-cleaner manager.\n"
                f"{exc}\n\nPrepare lama-cleaner in this environment, then restart the GUI."
            )
            return
        self._auto_start_lama()

    def _auto_start_lama(self) -> None:
        if self.lama_manager is None:
            return
        try:
            self.lama_manager.ensure_default_instance()
            self._refresh_ports_label()
        except Exception as exc:  # pylint: disable=broad-except
            self._error(
                "Failed to start lama-cleaner automatically.\n"
                f"{exc}\n\nEnsure lama-cleaner is available, then reopen the GUI."
            )

    def _browse_video(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video",
            str(self.paths.repo_root),
            "Video Files (*.mp4 *.mov *.mkv *.avi *.webm);;All Files (*.*)",
        )
        if not file_path:
            return

        video_path = Path(file_path)
        self.video_path_edit.setText(str(video_path))
        default_output = video_path.with_name(f"{video_path.stem}_cleaned.mp4")
        if not self.output_path_edit.text().strip():
            self.output_path_edit.setText(str(default_output))

        try:
            self.video_info = get_video_info(self.paths.ffprobe, video_path)
            self.duration_label.setText(format_seconds(self.video_info.duration_sec))
            self.start_spin.setMaximum(self.video_info.duration_sec)
            self.end_spin.setMaximum(self.video_info.duration_sec)
            self.end_spin.setValue(self.video_info.duration_sec)
            self.log(
                f"Loaded video: {video_path.name} "
                f"({self.video_info.width}x{self.video_info.height}, {self.video_info.fps:.3f} fps)"
            )
        except Exception as exc:  # pylint: disable=broad-except
            self._error(f"Failed to probe video info.\n{exc}")
            return

        self.player.setSource(QUrl.fromLocalFile(str(video_path)))
        self.player.pause()
        self.play_pause_btn.setText("Play")
        self.current_time_label.setText("00:00:00.000")
        self.timeline_slider.setValue(0)

    def _browse_output(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Output Video",
            str(self.paths.repo_root / "output" / "video_final.mp4"),
            "MP4 Video (*.mp4);;All Files (*.*)",
        )
        if file_path:
            self.output_path_edit.setText(file_path)

    def _toggle_play_pause(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_pause_btn.setText("Play")
        else:
            self.player.play()
            self.play_pause_btn.setText("Pause")

    def _on_player_duration_changed(self, duration_ms: int) -> None:
        self.timeline_slider.setRange(0, max(duration_ms, 0))
        self.duration_label.setText(format_seconds(duration_ms / 1000.0))

    def _on_player_position_changed(self, position_ms: int) -> None:
        if not self._slider_dragging:
            self.timeline_slider.setValue(position_ms)
        self.current_time_label.setText(format_seconds(position_ms / 1000.0))

    def _on_slider_pressed(self) -> None:
        self._slider_dragging = True

    def _on_slider_released(self) -> None:
        self._slider_dragging = False
        self.player.setPosition(self.timeline_slider.value())

    def _on_slider_moved(self, value: int) -> None:
        self.current_time_label.setText(format_seconds(value / 1000.0))

    def _set_start_from_current(self) -> None:
        self.start_spin.setValue(self.player.position() / 1000.0)

    def _set_end_from_current(self) -> None:
        self.end_spin.setValue(self.player.position() / 1000.0)

    def _add_segment(self) -> None:
        if self.video_info is None:
            self._error("Load a video first.")
            return

        start_sec = self.start_spin.value()
        end_sec = self.end_spin.value()
        segment = Segment(start_sec=start_sec, end_sec=end_sec)

        try:
            segment.validate()
            if end_sec > self.video_info.duration_sec:
                raise ValueError("Segment end exceeds video duration.")
            self._assert_no_overlap(segment)
        except Exception as exc:  # pylint: disable=broad-except
            self._error(str(exc))
            return

        self.segments.append(segment)
        self._refresh_segment_table()
        self.log(f"Added segment: {format_seconds(start_sec)} -> {format_seconds(end_sec)}")

    def _assert_no_overlap(self, new_segment: Segment) -> None:
        for segment in self.segments:
            overlap = max(segment.start_sec, new_segment.start_sec) < min(segment.end_sec, new_segment.end_sec)
            if overlap:
                raise ValueError(
                    f"Segment overlaps with existing segment "
                    f"{format_seconds(segment.start_sec)} -> {format_seconds(segment.end_sec)}."
                )

    def _remove_selected_segment(self) -> None:
        row = self.segment_table.currentRow()
        if row < 0 or row >= len(self.segments):
            return
        removed = self.segments.pop(row)
        self._refresh_segment_table()
        self.log(f"Removed segment: {removed.segment_id}")

    def _assign_mask_file(self) -> None:
        segment = self._selected_segment()
        if segment is None:
            self._error("Select a segment first.")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Mask Image",
            str(self.paths.repo_root),
            "Image Files (*.png *.jpg *.jpeg *.bmp);;All Files (*.*)",
        )
        if not file_path:
            return

        segment.mask_path = Path(file_path)
        self._refresh_segment_table()
        self.log(f"Assigned mask file to segment {segment.segment_id}: {segment.mask_path}")

    def _draw_mask_for_selected(self) -> None:
        segment = self._selected_segment()
        if segment is None:
            self._error("Select a segment first.")
            return

        video_path = Path(self.video_path_edit.text().strip())
        if not video_path.exists():
            self._error("Load a valid video first.")
            return

        frame_ref_path = self.paths.workspace_masks / f"ref-{segment.segment_id}.jpg"
        ok = extract_reference_frame(
            ffmpeg_path=self.paths.ffmpeg,
            video_path=video_path,
            time_sec=segment.start_sec,
            output_path=frame_ref_path,
        )
        if not ok:
            self._error("Failed to extract reference frame for mask drawing.")
            return

        save_mask_path = self.paths.workspace_masks / f"mask-{segment.segment_id}.png"
        dialog = MaskEditorDialog(
            reference_image_path=frame_ref_path,
            save_mask_path=save_mask_path,
            existing_mask_path=segment.mask_path,
            parent=self,
        )
        if dialog.exec() == QDialog.Accepted:
            segment.mask_path = save_mask_path
            self._refresh_segment_table()
            self.log(f"Drawn mask saved for segment {segment.segment_id}: {save_mask_path}")

    def _selected_segment(self) -> Segment | None:
        row = self.segment_table.currentRow()
        if row < 0 or row >= len(self.segments):
            return None
        return self.segments[row]

    def _refresh_segment_table(self) -> None:
        self.segment_table.setRowCount(len(self.segments))
        for row, segment in enumerate(self.segments):
            start_item = QTableWidgetItem(format_seconds(segment.start_sec))
            end_item = QTableWidgetItem(format_seconds(segment.end_sec))
            mask_item = QTableWidgetItem(str(segment.mask_path) if segment.mask_path else "(skip / no mask)")
            id_item = QTableWidgetItem(segment.segment_id)

            self.segment_table.setItem(row, 0, start_item)
            self.segment_table.setItem(row, 1, end_item)
            self.segment_table.setItem(row, 2, mask_item)
            self.segment_table.setItem(row, 3, id_item)

    def _apply_instance_count(self) -> None:
        if self.lama_manager is None:
            self._error("lama-cleaner manager is not available.")
            return
        target = self.instance_spin.value()
        try:
            self.lama_manager.set_instance_count(target)
            self._refresh_ports_label()
        except Exception as exc:  # pylint: disable=broad-except
            self._error(str(exc))

    def _refresh_ports_label(self) -> None:
        if self.lama_manager is None:
            self.ports_label.setText("Running Ports: -")
            return
        ports = self.lama_manager.get_ports()
        text = ", ".join(str(port) for port in ports) if ports else "-"
        self.ports_label.setText(f"Running Ports: {text}")
        self.log(f"Running lama ports: {text}")

    def _start_processing(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self._error("Processing is already running.")
            return

        video_path = Path(self.video_path_edit.text().strip())
        output_path = Path(self.output_path_edit.text().strip())
        if not video_path.exists():
            self._error("Select a valid input video file.")
            return
        if not output_path.name:
            self._error("Select a valid output path.")
            return

        if not self.segments:
            reply = QMessageBox.question(
                self,
                "No Segments",
                "No segments configured.\nCopy input video to output without processing?",
            )
            if reply == QMessageBox.Yes:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(video_path, output_path)
                self.log(f"No segments. Copied input to output: {output_path}")
            return

        for segment in self.segments:
            if segment.mask_path and not segment.mask_path.exists():
                self._error(f"Mask file not found for segment {segment.segment_id}:\n{segment.mask_path}")
                return

        if self.lama_manager is None:
            self._error("lama-cleaner manager is not available.")
            return

        ports = self.lama_manager.get_ports()
        if not ports:
            self._error("No lama-cleaner instances running. Apply instance count first.")
            return

        config = ProcessConfig(
            video_path=video_path,
            output_path=output_path,
            segments=list(self.segments),
            lama_ports=ports,
            keep_temp=self.keep_temp_box.isChecked(),
        )

        self.worker = ProcessingWorker(paths=self.paths, config=config)
        self.worker.log_message.connect(self.log)
        self.worker.progress_update.connect(self._on_worker_progress)
        self.worker.run_success.connect(self._on_worker_success)
        self.worker.run_error.connect(self._on_worker_error)
        self.worker.run_cancelled.connect(self._on_worker_cancelled)

        self._set_running_ui(True)
        self.progress_bar.setValue(0)
        self.log("Processing started.")
        self.worker.start()

    def _cancel_processing(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_cancel()
            self.log("Cancellation requested...")

    def _on_worker_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress_bar.setValue(0)
            return
        percent = int((done / total) * 100)
        self.progress_bar.setValue(max(0, min(100, percent)))

    def _on_worker_success(self, output_path: str) -> None:
        self.log(f"Processing completed successfully: {output_path}")
        self._set_running_ui(False)

    def _on_worker_error(self, message: str) -> None:
        self.log(f"Processing failed: {message}")
        self._error(message)
        self._set_running_ui(False)

    def _on_worker_cancelled(self) -> None:
        self.log("Processing cancelled.")
        self._set_running_ui(False)

    def _set_running_ui(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        self.instance_spin.setEnabled(not running)
        self.segment_table.setEnabled(not running)
        if not running:
            self.worker = None

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")

    def _error(self, message: str) -> None:
        QMessageBox.critical(self, "Error", message)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None and self.worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Exit",
                "Processing is still running. Cancel and exit?",
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.request_cancel()
            self.worker.wait(10000)

        if self.lama_manager is not None:
            try:
                self.lama_manager.stop_all()
            except Exception as exc:  # pylint: disable=broad-except
                self.log(f"Failed to stop some lama-cleaner processes: {exc}")

        event.accept()
