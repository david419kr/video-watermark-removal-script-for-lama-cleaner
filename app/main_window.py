from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import threading
import traceback

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QCursor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QPolygon,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .config import AppConfig, Paths
from .lama_manager import LamaCleanerManager
from .mask_editor import MaskEditorDialog
from .media_utils import (
    extract_reference_frame,
    format_seconds,
    frame_to_ms,
    frame_to_seconds,
    frame_to_text,
    get_video_info,
    ms_to_frame,
    parse_time_text,
    seconds_to_frame,
)
from .models import ProcessConfig, Segment, VideoInfo
from .pipeline import PipelineCancelled, VideoProcessingPipeline
from .timeline_slider import SegmentTimelineSlider


class HoverPreviewPopup(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.ToolTip)
        self.setFrameShape(QFrame.Box)
        self.setLineWidth(1)
        self.setStyleSheet(
            "QFrame { background: #151515; border: 1px solid #555; } "
            "QLabel { color: #efefef; }"
        )

        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        self.image_label = QLabel()
        self.image_label.setFixedSize(240, 136)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.text_label = QLabel("00:00:00.000")
        self.text_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.image_label)
        layout.addWidget(self.text_label)
        self.setLayout(layout)

    def show_preview(self, pixmap: QPixmap, text: str, global_pos: QPoint) -> None:
        self.image_label.setPixmap(
            pixmap.scaled(
                self.image_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )
        self.text_label.setText(text)
        self.adjustSize()
        self.move(global_pos.x() + 14, global_pos.y() - self.height() - 8)
        self.show()


class BlockingOverlay(QFrame):
    def __init__(
        self,
        parent: QWidget,
        default_text: str,
        style_name: str,
        *,
        top_level: bool = False,
        transparent_for_mouse: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(style_name)
        self._is_drop_overlay = style_name == "dropOverlay"
        self._top_level = top_level
        if top_level:
            flags = Qt.Tool | Qt.FramelessWindowHint
            if transparent_for_mouse:
                flags = Qt.ToolTip | Qt.FramelessWindowHint
            self.setWindowFlags(flags)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        if transparent_for_mouse:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            window_transparent_flag = getattr(Qt, "WindowTransparentForInput", None)
            if window_transparent_flag is not None:
                self.setWindowFlag(window_transparent_flag, True)
        self.setAcceptDrops(False)
        self.hide()

        if style_name == "loadingOverlay":
            self.setStyleSheet(
                "#loadingOverlay { background: rgba(0,0,0,150); } "
                "#loadingPanel { background: rgba(22,22,22,225); border: 1px solid #707070; border-radius: 8px; } "
                "#loadingText { color: #f2f2f2; font-size: 15px; font-weight: 600; }"
            )
        else:
            self.setStyleSheet(
                "#dropPanel { background: rgba(16,26,42,215); border: 1px solid #4d7fb8; border-radius: 8px; } "
                "#dropText { color: #f3f8ff; font-size: 18px; font-weight: 700; }"
            )

        root = QVBoxLayout()
        root.setContentsMargins(24, 24, 24, 24)
        root.addStretch(1)

        panel = QFrame()
        panel.setObjectName("loadingPanel" if style_name == "loadingOverlay" else "dropPanel")
        panel_layout = QVBoxLayout()
        panel_layout.setContentsMargins(18, 14, 18, 14)
        panel_layout.setSpacing(6)

        self.text_label = QLabel(default_text)
        self.text_label.setObjectName("loadingText" if style_name == "loadingOverlay" else "dropText")
        self.text_label.setAlignment(Qt.AlignCenter)
        panel_layout.addWidget(self.text_label)
        panel.setLayout(panel_layout)

        root.addWidget(panel, 0, Qt.AlignCenter)
        root.addStretch(1)
        self.setLayout(root)

    def set_message(self, message: str) -> None:
        self.text_label.setText(message)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        if self._is_drop_overlay:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.fillRect(self.rect(), QColor(30, 58, 95, 115))
            pen = QPen(QColor(91, 166, 255, 220), 3)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(10, 10, -10, -10), 10, 10)
            painter.end()
        super().paintEvent(event)


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
        self.setWindowTitle("Lama Cleaner Video GUI")
        self.resize(1200, 960)
        self.setAcceptDrops(True)

        self.paths = Paths(repo_root)
        self.paths.workspace_root.mkdir(parents=True, exist_ok=True)
        self.paths.workspace_jobs.mkdir(parents=True, exist_ok=True)
        self.paths.workspace_masks.mkdir(parents=True, exist_ok=True)

        self.video_info: VideoInfo | None = None
        self.segments: list[Segment] = []
        self.worker: ProcessingWorker | None = None
        self._slider_dragging = False
        self._table_refreshing = False
        self._pending_seek_ms: int | None = None
        self._hover_pending_ms: int | None = None
        self._hover_pending_pos: QPoint | None = None
        self._hover_pixmap_cache: OrderedDict[int, QPixmap] = OrderedDict()
        self._mask_image_cache: dict[Path, QImage] = {}
        self._mask_overlay_cache: OrderedDict[tuple[str, int, int], QPixmap] = OrderedDict()
        self._current_mask_overlay_key: tuple[str, int, int] | None = None

        self._key_seek_direction = 0
        self._key_seek_tick_count = 0
        self._loading_overlay_depth = 0
        self._drop_overlay_visible = False

        self.lama_manager: LamaCleanerManager | None = None
        self._lama_init_pending = True

        self._build_ui()
        self._pencil_icon = self._create_pencil_icon()
        self._load_ui_settings()
        self._build_timers()
        self._wire_player()

        self.installEventFilter(self)
        self.video_widget.installEventFilter(self)
        self.timeline_slider.installEventFilter(self)

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

        self.loading_overlay = BlockingOverlay(
            self,
            "Loading...",
            "loadingOverlay",
            top_level=True,
            transparent_for_mouse=False,
        )
        self.drop_overlay = BlockingOverlay(
            self,
            "Drop video file to open",
            "dropOverlay",
            top_level=True,
            transparent_for_mouse=True,
        )
        self._resize_overlays()

    def _build_timers(self) -> None:
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(24)
        self._seek_timer.timeout.connect(self._apply_pending_seek)

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(120)
        self._hover_timer.timeout.connect(self._show_hover_preview)

        self._key_seek_timer = QTimer(self)
        self._key_seek_timer.setInterval(70)
        self._key_seek_timer.timeout.connect(self._on_key_seek_tick)

    def _resize_overlays(self) -> None:
        if not hasattr(self, "loading_overlay") or not hasattr(self, "drop_overlay"):
            return
        top_left = self.mapToGlobal(QPoint(0, 0))
        rect = QRect(top_left, self.size())
        self.loading_overlay.setGeometry(rect)
        self.drop_overlay.setGeometry(rect)

    @staticmethod
    def _create_pencil_icon() -> QIcon:
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)

        painter.setPen(QPen(QColor(67, 67, 67), 2))
        painter.drawLine(3, 13, 11, 5)

        painter.setBrush(QColor(251, 191, 36))
        painter.setPen(QPen(QColor(145, 110, 8), 1))
        painter.drawPolygon(QPolygon([QPoint(4, 13), QPoint(3, 15), QPoint(6, 14)]))

        painter.setBrush(QColor(255, 220, 120))
        painter.setPen(QPen(QColor(120, 90, 10), 1))
        painter.drawPolygon(QPolygon([QPoint(10, 5), QPoint(12, 3), QPoint(13, 4), QPoint(11, 6)]))

        painter.setBrush(QColor(230, 80, 80))
        painter.setPen(Qt.NoPen)
        painter.drawRect(2, 12, 2, 2)
        painter.end()

        return QIcon(pixmap)

    def _show_loading_overlay(self, message: str) -> None:
        self._loading_overlay_depth += 1
        self.loading_overlay.set_message(message)
        self._resize_overlays()
        self.loading_overlay.show()
        self.loading_overlay.raise_()
        QApplication.processEvents()

    def _hide_loading_overlay(self) -> None:
        if self._loading_overlay_depth > 0:
            self._loading_overlay_depth -= 1
        if self._loading_overlay_depth == 0:
            self.loading_overlay.hide()
        QApplication.processEvents()

    def _show_drop_overlay(self) -> None:
        if self._drop_overlay_visible:
            return
        self._resize_overlays()
        self.drop_overlay.show()
        self.drop_overlay.raise_()
        self._drop_overlay_visible = True

    def _hide_drop_overlay(self) -> None:
        if not self._drop_overlay_visible:
            return
        self.drop_overlay.hide()
        self._drop_overlay_visible = False

    def _settings_path(self) -> Path:
        return self.paths.ui_settings

    def _load_ui_settings(self) -> None:
        path = self._settings_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            value = int(data.get("instance_count", AppConfig.DEFAULT_INSTANCE_COUNT))
            value = max(1, min(AppConfig.MAX_INSTANCE_COUNT, value))
            self.instance_spin.setValue(value)
        except Exception as exc:  # pylint: disable=broad-except
            self.log(f"Failed to load UI settings: {exc}")

    def _save_ui_settings(self) -> None:
        path = self._settings_path()
        payload = {"instance_count": int(self.instance_spin.value())}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        except Exception as exc:  # pylint: disable=broad-except
            self.log(f"Failed to save UI settings: {exc}")

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
        self.instance_spin.valueChanged.connect(lambda _: self._save_ui_settings())

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
        self.video_widget.setMinimumHeight(300)
        self.video_widget.setFocusPolicy(Qt.StrongFocus)
        self.video_widget.setAcceptDrops(True)

        self.mask_state_badge = QLabel("NO SEGMENT (SKIP)", self.video_widget)
        self.mask_state_badge.setStyleSheet(
            "QLabel { background: rgba(32,32,32,170); color: #f0f0f0; "
            "border: 1px solid #909090; border-radius: 4px; padding: 4px 8px; }"
        )
        self.mask_state_badge.move(12, 12)
        self.mask_state_badge.show()

        self.mask_overlay_label = QLabel(self.video_widget)
        self.mask_overlay_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.mask_overlay_label.setStyleSheet("background: transparent;")
        self.mask_overlay_label.setScaledContents(False)
        self.mask_overlay_label.hide()

        controls = QHBoxLayout()
        self.play_pause_btn = QPushButton("Play")
        self.play_pause_btn.clicked.connect(self._toggle_play_pause)
        controls.addWidget(self.play_pause_btn)

        self.current_time_label = QLabel("00:00:00.000")
        self.duration_label = QLabel("00:00:00.000")
        controls.addWidget(self.current_time_label)
        controls.addWidget(QLabel("/"))
        controls.addWidget(self.duration_label)
        controls.addSpacing(14)
        controls.addWidget(QLabel("Frame"))
        self.current_frame_label = QLabel("1")
        self.total_frame_label = QLabel("1")
        self.current_frame_label.setStyleSheet("font-weight: 600;")
        self.total_frame_label.setStyleSheet("font-weight: 600;")
        controls.addWidget(self.current_frame_label)
        controls.addWidget(QLabel("/"))
        controls.addWidget(self.total_frame_label)
        controls.addStretch(1)

        self.timeline_slider = SegmentTimelineSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 0)
        self.timeline_slider.setFocusPolicy(Qt.StrongFocus)
        self.timeline_slider.sliderPressed.connect(self._on_slider_pressed)
        self.timeline_slider.sliderReleased.connect(self._on_slider_released)
        self.timeline_slider.sliderMoved.connect(self._on_slider_moved)
        self.timeline_slider.hoverMoved.connect(self._on_timeline_hover_moved)
        self.timeline_slider.hoverLeft.connect(self._on_timeline_hover_left)
        segment_row = QHBoxLayout()
        self.start_frame_spin = QSpinBox()
        self.start_frame_spin.setMinimum(1)
        self.start_frame_spin.setMaximum(1)
        self.start_frame_spin.valueChanged.connect(self._on_start_frame_changed)
        self.start_frame_time_hint = QLabel("(00:00:00.000)")

        self.end_frame_spin = QSpinBox()
        self.end_frame_spin.setMinimum(1)
        self.end_frame_spin.setMaximum(1)
        self.end_frame_spin.valueChanged.connect(self._on_end_frame_changed)
        self.end_frame_time_hint = QLabel("(00:00:00.000)")

        set_start_btn = QPushButton("Set Start = Current Frame")
        set_end_btn = QPushButton("Set End = Current Frame")
        self.add_segment_btn = QPushButton("Add Segment")
        self.add_segment_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: #ffffff; font-weight: 700; "
            "padding: 6px 14px; border-radius: 5px; }"
            "QPushButton:hover { background: #1d4ed8; }"
            "QPushButton:disabled { background: #4a4a4a; color: #bdbdbd; }"
        )
        set_start_btn.clicked.connect(self._set_start_from_current)
        set_end_btn.clicked.connect(self._set_end_from_current)
        self.add_segment_btn.clicked.connect(self._add_segment)

        segment_row.addWidget(QLabel("Start Frame"))
        segment_row.addWidget(self.start_frame_spin)
        segment_row.addWidget(self.start_frame_time_hint)
        segment_row.addWidget(set_start_btn)
        segment_row.addSpacing(10)
        segment_row.addWidget(QLabel("End Frame"))
        segment_row.addWidget(self.end_frame_spin)
        segment_row.addWidget(self.end_frame_time_hint)
        segment_row.addWidget(set_end_btn)
        segment_row.addSpacing(14)
        segment_row.addWidget(self.add_segment_btn)
        segment_row.addStretch(1)

        tip_label = QLabel("Tip: Click preview/seekbar, then Left/Right keys for frame-step seek. Hold for faster seek.")
        tip_label.setStyleSheet("color: #c0c0c0;")

        layout.addWidget(self.video_widget, 1)
        layout.addLayout(controls)
        layout.addWidget(self.timeline_slider)
        layout.addLayout(segment_row)
        layout.addWidget(tip_label)
        box.setLayout(layout)

        self.hover_popup = HoverPreviewPopup(self)
        self.hover_popup.hide()
        return box

    def _build_segment_group(self) -> QGroupBox:
        box = QGroupBox("Segments and Masks")
        box.setMinimumHeight(200)
        layout = QVBoxLayout()

        self.segment_table = QTableWidget(0, 6)
        self.segment_table.setHorizontalHeaderLabels(
            ["Start Frame", "Start Time", "End Frame", "End Time", "Mask", "Remove"]
        )
        self.segment_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.segment_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.segment_table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
        )
        self.segment_table.verticalHeader().setVisible(False)
        self.segment_table.setAlternatingRowColors(True)
        self.segment_table.itemChanged.connect(self._on_segment_table_item_changed)
        self.segment_table.itemSelectionChanged.connect(self._on_segment_table_selection_changed)
        self.segment_table.setColumnWidth(0, 120)
        self.segment_table.setColumnWidth(1, 150)
        self.segment_table.setColumnWidth(2, 120)
        self.segment_table.setColumnWidth(3, 150)
        self.segment_table.setColumnWidth(4, 520)
        self.segment_table.setColumnWidth(5, 82)

        tip = QLabel("Mask column actions: folder icon = choose file, pencil icon = draw mask.")
        tip.setStyleSheet("color: #c0c0c0;")
        layout.addWidget(self.segment_table, 1)
        layout.addWidget(tip)
        box.setLayout(layout)
        return box

    def _build_run_group(self) -> QGroupBox:
        box = QGroupBox("Run")
        layout = QFormLayout()

        row = QHBoxLayout()
        self.keep_temp_box = QCheckBox("Keep temporary job folder")
        self.start_btn = QPushButton("Start Processing")
        self.start_btn.setStyleSheet(
            "QPushButton { background: #198754; color: #ffffff; font-weight: 700; "
            "padding: 8px 18px; border-radius: 6px; }"
            "QPushButton:hover { background: #157347; }"
            "QPushButton:disabled { background: #4a4a4a; color: #bdbdbd; }"
        )
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setStyleSheet(
            "QPushButton { background: #3e3e3e; color: #d6d6d6; border-radius: 6px; padding: 8px 14px; }"
            "QPushButton:enabled { background: #b42318; color: #ffffff; font-weight: 700; }"
            "QPushButton:enabled:hover { background: #8f1c14; }"
        )
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

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if self._lama_init_pending:
            self._lama_init_pending = False
            QTimer.singleShot(0, self._init_lama_manager)

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
        target = max(1, min(AppConfig.MAX_INSTANCE_COUNT, self.instance_spin.value()))
        self._show_loading_overlay(f"Starting lama-cleaner instances ({target})...")
        try:
            self.lama_manager.set_instance_count(target)
            self._refresh_ports_label()
            self._save_ui_settings()
        except Exception as exc:  # pylint: disable=broad-except
            self._error(
                "Failed to start lama-cleaner automatically.\n"
                f"{exc}\n\nEnsure lama-cleaner is available, then reopen the GUI."
            )
        finally:
            self._hide_loading_overlay()

    def _browse_video(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video",
            str(self.paths.repo_root),
            "Video Files (*.mp4 *.mov *.mkv *.avi *.webm);;All Files (*.*)",
        )
        if not file_path:
            return
        self._load_video_file(Path(file_path))

    def _load_video_file(self, video_path: Path) -> None:
        self._show_loading_overlay(f"Loading video: {video_path.name}")
        try:
            self.video_path_edit.setText(str(video_path))
            default_output = video_path.with_name(f"{video_path.stem}_cleaned.mp4")
            if not self.output_path_edit.text().strip():
                self.output_path_edit.setText(str(default_output))

            self.video_info = get_video_info(self.paths.ffprobe, video_path)
            self.duration_label.setText(format_seconds(self.video_info.duration_sec))

            self.start_frame_spin.blockSignals(True)
            self.end_frame_spin.blockSignals(True)
            self.start_frame_spin.setMaximum(self.video_info.total_frames)
            self.end_frame_spin.setMaximum(self.video_info.total_frames)
            self.start_frame_spin.setValue(1)
            self.end_frame_spin.setValue(self.video_info.total_frames)
            self.start_frame_spin.blockSignals(False)
            self.end_frame_spin.blockSignals(False)
            self._update_start_end_time_hints()
            self.current_frame_label.setText("1")
            self.total_frame_label.setText(str(self.video_info.total_frames))

            self.log(
                f"Loaded video: {video_path.name} "
                f"({self.video_info.width}x{self.video_info.height}, {self.video_info.fps:.3f} fps, "
                f"{self.video_info.total_frames} frames)"
            )
            self._hover_pixmap_cache.clear()
            self._mask_image_cache.clear()
            self._mask_overlay_cache.clear()
            self._current_mask_overlay_key = None
        except Exception as exc:  # pylint: disable=broad-except
            self._error(f"Failed to probe video info.\n{exc}")
            return
        finally:
            self._hide_loading_overlay()

        self.player.setSource(QUrl.fromLocalFile(str(video_path)))
        self.player.pause()
        self.play_pause_btn.setText("Play")
        self.current_time_label.setText("00:00:00.000")
        self.timeline_slider.setValue(0)
        self.timeline_slider.set_segment_data(self.segments, self.video_info.total_frames)
        self._update_mask_visuals(1)
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
        frame_index = self._frame_from_ms(position_ms)
        self.current_frame_label.setText(str(frame_index))
        if not self._slider_dragging:
            self.timeline_slider.setValue(position_ms)
        self.current_time_label.setText(format_seconds(position_ms / 1000.0))
        self._update_mask_visuals(frame_index)

    def _on_slider_pressed(self) -> None:
        self._slider_dragging = True

    def _on_slider_released(self) -> None:
        self._slider_dragging = False
        self.player.setPosition(self.timeline_slider.value())

    def _on_slider_moved(self, value: int) -> None:
        frame_index = self._frame_from_ms(value)
        self.current_frame_label.setText(str(frame_index))
        self.current_time_label.setText(format_seconds(value / 1000.0))
        self._pending_seek_ms = value
        if not self._seek_timer.isActive():
            self._seek_timer.start()
        self._update_mask_visuals(frame_index)

    def _apply_pending_seek(self) -> None:
        if self._pending_seek_ms is None:
            return
        self.player.setPosition(self._pending_seek_ms)
        self._pending_seek_ms = None

    def _on_timeline_hover_moved(self, value_ms: int, global_x: int, global_y: int) -> None:
        self._hover_pending_ms = value_ms
        self._hover_pending_pos = QPoint(global_x, global_y)
        if not self._hover_timer.isActive():
            self._hover_timer.start()

    def _on_timeline_hover_left(self) -> None:
        self._hover_pending_ms = None
        self._hover_pending_pos = None
        self.hover_popup.hide()

    def _show_hover_preview(self) -> None:
        if self.video_info is None:
            return
        if self._hover_pending_ms is None or self._hover_pending_pos is None:
            return

        frame_index = self._frame_from_ms(self._hover_pending_ms)
        pixmap = self._hover_preview_pixmap(frame_index)
        if pixmap is None:
            return

        text = frame_to_text(frame_index, self.video_info.fps)
        self.hover_popup.show_preview(pixmap, text, self._hover_pending_pos)

    def _hover_preview_pixmap(self, frame_index: int) -> QPixmap | None:
        if self.video_info is None:
            return None
        if frame_index in self._hover_pixmap_cache:
            pixmap = self._hover_pixmap_cache.pop(frame_index)
            self._hover_pixmap_cache[frame_index] = pixmap
            return pixmap

        video_path = Path(self.video_path_edit.text().strip())
        if not video_path.exists():
            return None

        sec = frame_to_seconds(frame_index, self.video_info.fps)
        command = [
            str(self.paths.ffmpeg),
            "-ss",
            f"{sec:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=320:-1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ]
        result = subprocess.run(command, capture_output=True, check=False)
        if result.returncode != 0 or not result.stdout:
            return None

        pixmap = QPixmap()
        if not pixmap.loadFromData(result.stdout):
            return None

        self._hover_pixmap_cache[frame_index] = pixmap
        while len(self._hover_pixmap_cache) > 120:
            self._hover_pixmap_cache.popitem(last=False)
        return pixmap

    def _set_start_from_current(self) -> None:
        if self.video_info is None:
            return
        self.start_frame_spin.setValue(self._frame_from_ms(self.player.position()))

    def _set_end_from_current(self) -> None:
        if self.video_info is None:
            return
        self.end_frame_spin.setValue(self._frame_from_ms(self.player.position()))

    def _on_start_frame_changed(self, value: int) -> None:
        if self.video_info is None:
            return
        if value > self.end_frame_spin.value():
            self.end_frame_spin.setValue(value)
        self._update_start_end_time_hints()

    def _on_end_frame_changed(self, value: int) -> None:
        if self.video_info is None:
            return
        if value < self.start_frame_spin.value():
            self.start_frame_spin.setValue(value)
        self._update_start_end_time_hints()

    def _update_start_end_time_hints(self) -> None:
        if self.video_info is None:
            self.start_frame_time_hint.setText("(00:00:00.000)")
            self.end_frame_time_hint.setText("(00:00:00.000)")
            return

        start_sec = frame_to_seconds(self.start_frame_spin.value(), self.video_info.fps)
        end_sec = frame_to_seconds(self.end_frame_spin.value(), self.video_info.fps)
        self.start_frame_time_hint.setText(f"({format_seconds(start_sec)})")
        self.end_frame_time_hint.setText(f"({format_seconds(end_sec)})")

    def _add_segment(self) -> None:
        if self.video_info is None:
            self._error("Load a video first.")
            return

        segment = Segment(
            start_frame=self.start_frame_spin.value(),
            end_frame=self.end_frame_spin.value(),
        )

        try:
            segment.validate()
            self._validate_segment_bounds(segment)
            self._assert_no_overlap(segment)
        except Exception as exc:  # pylint: disable=broad-except
            self._error(str(exc))
            return

        self.segments.append(segment)
        self.segments.sort(key=lambda s: s.start_frame)
        self._refresh_segment_table()
        for idx, current in enumerate(self.segments):
            if current.segment_id == segment.segment_id:
                self.segment_table.selectRow(idx)
                break
        self.log(
            f"Added segment: {frame_to_text(segment.start_frame, self.video_info.fps)} "
            f"-> {frame_to_text(segment.end_frame, self.video_info.fps)}"
        )

    def _validate_segment_bounds(self, segment: Segment) -> None:
        if self.video_info is None:
            return
        if segment.end_frame > self.video_info.total_frames:
            raise ValueError(
                f"Segment exceeds total frame count ({self.video_info.total_frames})."
            )

    def _assert_no_overlap(self, new_segment: Segment, skip_index: int | None = None) -> None:
        for idx, segment in enumerate(self.segments):
            if skip_index is not None and idx == skip_index:
                continue
            overlap = max(segment.start_frame, new_segment.start_frame) <= min(
                segment.end_frame, new_segment.end_frame
            )
            if overlap:
                raise ValueError(
                    f"Segment overlaps existing segment: "
                    f"{segment.start_frame}-{segment.end_frame}"
                )

    def _assign_mask_file_for_segment(self, segment: Segment) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Mask Image",
            str(self.paths.repo_root),
            "Image Files (*.png *.jpg *.jpeg *.bmp);;All Files (*.*)",
        )
        if not file_path:
            return

        segment.mask_path = Path(file_path)
        self._mask_overlay_cache.clear()
        self._current_mask_overlay_key = None
        self._refresh_segment_table()
        self.log(f"Assigned mask file to segment {segment.segment_id}: {segment.mask_path}")

    def _draw_mask_for_segment(self, segment: Segment) -> None:
        if self.video_info is None:
            self._error("Load a video first.")
            return

        video_path = Path(self.video_path_edit.text().strip())
        if not video_path.exists():
            self._error("Load a valid video first.")
            return

        frame_ref_path = self.paths.workspace_masks / f"ref-{segment.segment_id}.jpg"
        ok = extract_reference_frame(
            ffmpeg_path=self.paths.ffmpeg,
            video_path=video_path,
            time_sec=frame_to_seconds(segment.start_frame, self.video_info.fps),
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
            self._mask_overlay_cache.clear()
            self._current_mask_overlay_key = None
            self._refresh_segment_table()
            self.log(f"Drawn mask saved for segment {segment.segment_id}: {save_mask_path}")

    def _segment_index_by_id(self, segment_id: str) -> int | None:
        for idx, segment in enumerate(self.segments):
            if segment.segment_id == segment_id:
                return idx
        return None

    def _remove_segment_by_id(self, segment_id: str) -> None:
        idx = self._segment_index_by_id(segment_id)
        if idx is None:
            return
        removed = self.segments.pop(idx)
        self._current_mask_overlay_key = None
        self._refresh_segment_table()
        self.log(f"Removed segment: {removed.segment_id}")

    def _assign_mask_for_segment_id(self, segment_id: str) -> None:
        idx = self._segment_index_by_id(segment_id)
        if idx is None:
            return
        self.segment_table.selectRow(idx)
        self._assign_mask_file_for_segment(self.segments[idx])

    def _draw_mask_for_segment_id(self, segment_id: str) -> None:
        idx = self._segment_index_by_id(segment_id)
        if idx is None:
            return
        self.segment_table.selectRow(idx)
        self._draw_mask_for_segment(self.segments[idx])

    def _selected_segment(self) -> Segment | None:
        row = self.segment_table.currentRow()
        if row < 0 or row >= len(self.segments):
            return None
        return self.segments[row]

    def _build_mask_cell_widget(self, segment: Segment) -> QWidget:
        widget = QWidget()
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(4, 2, 4, 2)
        row_layout.setSpacing(6)

        open_btn = QPushButton()
        open_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        open_btn.setToolTip("Assign mask file")
        open_btn.setFixedSize(28, 24)
        open_btn.clicked.connect(
            lambda _=False, sid=segment.segment_id: self._assign_mask_for_segment_id(sid)
        )

        draw_btn = QPushButton()
        draw_btn.setIcon(self._pencil_icon)
        draw_btn.setToolTip("Draw mask")
        draw_btn.setFixedSize(28, 24)
        draw_btn.clicked.connect(
            lambda _=False, sid=segment.segment_id: self._draw_mask_for_segment_id(sid)
        )

        path_label = QLabel(segment.mask_path.name if segment.mask_path else "(skip / no mask)")
        path_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        path_label.setToolTip(str(segment.mask_path) if segment.mask_path else "No mask assigned")

        row_layout.addWidget(open_btn)
        row_layout.addWidget(draw_btn)
        row_layout.addWidget(path_label, 1)
        widget.setLayout(row_layout)
        return widget

    def _build_remove_cell_widget(self, segment: Segment) -> QWidget:
        widget = QWidget()
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(8, 2, 8, 2)
        row_layout.setSpacing(0)

        remove_btn = QPushButton("X")
        remove_btn.setToolTip("Remove this segment")
        remove_btn.setFixedSize(28, 24)
        remove_btn.setStyleSheet(
            "QPushButton { background: #b42318; color: #ffffff; font-weight: 700; "
            "border-radius: 4px; }"
            "QPushButton:hover { background: #912018; }"
            "QPushButton:disabled { background: #4a4a4a; color: #bdbdbd; }"
        )
        remove_btn.clicked.connect(
            lambda _=False, sid=segment.segment_id: self._remove_segment_by_id(sid)
        )

        row_layout.addWidget(remove_btn, 0, Qt.AlignCenter)
        widget.setLayout(row_layout)
        return widget

    def _on_segment_table_selection_changed(self) -> None:
        if self._table_refreshing or self.video_info is None:
            return
        segment = self._selected_segment()
        if segment is None:
            return

        self.start_frame_spin.blockSignals(True)
        self.end_frame_spin.blockSignals(True)
        self.start_frame_spin.setValue(segment.start_frame)
        self.end_frame_spin.setValue(segment.end_frame)
        self.start_frame_spin.blockSignals(False)
        self.end_frame_spin.blockSignals(False)
        self._update_start_end_time_hints()
        self.player.setPosition(self._ms_from_frame(segment.start_frame))

    def _refresh_segment_table(self) -> None:
        selected_segment_id: str | None = None
        selected_row = self.segment_table.currentRow()
        if 0 <= selected_row < len(self.segments):
            selected_segment_id = self.segments[selected_row].segment_id

        self._table_refreshing = True
        try:
            self.segment_table.setRowCount(len(self.segments))
            for row, segment in enumerate(self.segments):
                start_frame_item = QTableWidgetItem(str(segment.start_frame))
                start_time_item = QTableWidgetItem(
                    format_seconds(frame_to_seconds(segment.start_frame, self.video_info.fps)) if self.video_info else ""
                )
                end_frame_item = QTableWidgetItem(str(segment.end_frame))
                end_time_item = QTableWidgetItem(
                    format_seconds(frame_to_seconds(segment.end_frame, self.video_info.fps)) if self.video_info else ""
                )

                for item in (start_frame_item, start_time_item, end_frame_item, end_time_item):
                    item.setFlags(item.flags() | Qt.ItemIsEditable)

                self.segment_table.setItem(row, 0, start_frame_item)
                self.segment_table.setItem(row, 1, start_time_item)
                self.segment_table.setItem(row, 2, end_frame_item)
                self.segment_table.setItem(row, 3, end_time_item)
                self.segment_table.setCellWidget(row, 4, self._build_mask_cell_widget(segment))
                self.segment_table.setCellWidget(row, 5, self._build_remove_cell_widget(segment))

            if selected_segment_id is not None:
                for idx, segment in enumerate(self.segments):
                    if segment.segment_id == selected_segment_id:
                        self.segment_table.selectRow(idx)
                        break
        finally:
            self._table_refreshing = False

        total_frames = self.video_info.total_frames if self.video_info else 0
        self.timeline_slider.set_segment_data(self.segments, total_frames)
        self._update_mask_visuals(self._frame_from_ms(self.player.position()))

    def _on_segment_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._table_refreshing:
            return
        if self.video_info is None:
            return

        row = item.row()
        col = item.column()
        if row < 0 or row >= len(self.segments):
            return
        if col not in (0, 1, 2, 3):
            return

        segment = self.segments[row]
        old_start = segment.start_frame
        old_end = segment.end_frame

        try:
            text = item.text().strip()
            if col == 0:
                segment.start_frame = int(text)
            elif col == 2:
                segment.end_frame = int(text)
            elif col == 1:
                seconds = parse_time_text(text)
                segment.start_frame = seconds_to_frame(seconds, self.video_info.fps, self.video_info.total_frames)
            elif col == 3:
                seconds = parse_time_text(text)
                segment.end_frame = seconds_to_frame(seconds, self.video_info.fps, self.video_info.total_frames)

            segment.validate()
            self._validate_segment_bounds(segment)
            self._assert_no_overlap(segment, skip_index=row)
        except Exception as exc:  # pylint: disable=broad-except
            segment.start_frame = old_start
            segment.end_frame = old_end
            self._refresh_segment_table()
            self._error(f"Invalid segment edit: {exc}")
            return

        self.segments.sort(key=lambda s: s.start_frame)
        self._refresh_segment_table()

    def _apply_instance_count(self) -> None:
        if self.lama_manager is None:
            self._error("lama-cleaner manager is not available.")
            return
        target = self.instance_spin.value()
        self._show_loading_overlay(f"Applying instance count: {target}")
        try:
            self.lama_manager.set_instance_count(target)
            self._refresh_ports_label()
            self._save_ui_settings()
        except Exception as exc:  # pylint: disable=broad-except
            self._error(str(exc))
        finally:
            self._hide_loading_overlay()

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
        if done >= total:
            self.progress_bar.setValue(99)
            return
        percent = int((max(0, done) / total) * 99)
        self.progress_bar.setValue(max(0, min(99, percent)))

    def _on_worker_success(self, output_path: str) -> None:
        self.progress_bar.setValue(100)
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
        self.add_segment_btn.setEnabled(not running)
        self.segment_table.setEnabled(not running)
        if not running:
            self.worker = None

    def _frame_from_ms(self, ms: int) -> int:
        if self.video_info is None:
            return 1
        return ms_to_frame(ms, self.video_info.fps, self.video_info.total_frames)

    def _ms_from_frame(self, frame_index: int) -> int:
        if self.video_info is None:
            return 0
        return frame_to_ms(frame_index, self.video_info.fps)

    def _segment_for_frame(self, frame_index: int) -> Segment | None:
        for segment in self.segments:
            if segment.start_frame <= frame_index <= segment.end_frame:
                return segment
        return None

    def _update_mask_state_badge(self, frame_index: int) -> None:
        segment = self._segment_for_frame(frame_index)
        if segment is None:
            self.mask_state_badge.setText("NO SEGMENT (SKIP)")
            self.mask_state_badge.setStyleSheet(
                "QLabel { background: rgba(32,32,32,170); color: #f0f0f0; "
                "border: 1px solid #909090; border-radius: 4px; padding: 4px 8px; }"
            )
            return

        if segment.mask_path:
            self.mask_state_badge.setText("MASK ACTIVE")
            self.mask_state_badge.setStyleSheet(
                "QLabel { background: rgba(110,22,22,180); color: #ffe0e0; "
                "border: 1px solid #ff8f8f; border-radius: 4px; padding: 4px 8px; }"
            )
        else:
            self.mask_state_badge.setText("SEGMENT (SKIP)")
            self.mask_state_badge.setStyleSheet(
                "QLabel { background: rgba(70,70,70,180); color: #e0e0e0; "
                "border: 1px solid #bdbdbd; border-radius: 4px; padding: 4px 8px; }"
            )

    def _update_mask_visuals(self, frame_index: int) -> None:
        self._update_mask_state_badge(frame_index)
        self._update_mask_overlay(frame_index)

    def _update_mask_overlay(self, frame_index: int) -> None:
        segment = self._segment_for_frame(frame_index)
        if segment is None or segment.mask_path is None or not segment.mask_path.exists():
            self._current_mask_overlay_key = None
            self.mask_overlay_label.hide()
            return

        target_rect = self._video_target_rect()
        if target_rect.width() <= 0 or target_rect.height() <= 0:
            self._current_mask_overlay_key = None
            self.mask_overlay_label.hide()
            return

        cache_key = (str(segment.mask_path), target_rect.width(), target_rect.height())
        if self._current_mask_overlay_key != cache_key:
            pixmap = self._mask_overlay_cache.get(cache_key)
            if pixmap is None:
                mask_image = self._load_segment_mask(segment.mask_path)
                if mask_image is None:
                    self._current_mask_overlay_key = None
                    self.mask_overlay_label.hide()
                    return
                pixmap = self._build_overlay_pixmap(mask_image, target_rect.size())
                self._mask_overlay_cache[cache_key] = pixmap
                while len(self._mask_overlay_cache) > 64:
                    self._mask_overlay_cache.popitem(last=False)
            else:
                self._mask_overlay_cache.move_to_end(cache_key)

            self.mask_overlay_label.setPixmap(pixmap)
            self._current_mask_overlay_key = cache_key

        self.mask_overlay_label.setGeometry(target_rect)
        self.mask_overlay_label.show()
        self.mask_overlay_label.raise_()
        self.mask_state_badge.raise_()

    def _video_target_rect(self) -> QRect:
        width = self.video_widget.width()
        height = self.video_widget.height()
        if width <= 0 or height <= 0:
            return QRect()

        if self.video_info is None or self.video_info.width <= 0 or self.video_info.height <= 0:
            return QRect(0, 0, width, height)

        scale = min(width / self.video_info.width, height / self.video_info.height)
        target_w = max(1, int(round(self.video_info.width * scale)))
        target_h = max(1, int(round(self.video_info.height * scale)))
        left = (width - target_w) // 2
        top = (height - target_h) // 2
        return QRect(left, top, target_w, target_h)

    def _load_segment_mask(self, mask_path: Path) -> QImage | None:
        key = mask_path.resolve()
        if key in self._mask_image_cache:
            return self._mask_image_cache[key]

        image = QImage(str(mask_path))
        if image.isNull():
            self.log(f"Failed to load mask image: {mask_path}")
            return None

        mask = image.convertToFormat(QImage.Format_Grayscale8)
        if (
            self.video_info is not None
            and (mask.width() != self.video_info.width or mask.height() != self.video_info.height)
        ):
            mask = mask.scaled(
                self.video_info.width,
                self.video_info.height,
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation,
            )

        self._mask_image_cache[key] = mask
        return mask

    @staticmethod
    def _build_overlay_pixmap(mask_image: QImage, target_size: QSize) -> QPixmap:
        scaled_mask = mask_image.scaled(
            target_size,
            Qt.IgnoreAspectRatio,
            Qt.FastTransformation,
        )
        overlay = QImage(target_size, QImage.Format_ARGB32)
        overlay.fill(Qt.transparent)

        painter = QPainter(overlay)
        painter.fillRect(overlay.rect(), QColor(255, 70, 70, 170))
        painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, scaled_mask)
        painter.end()
        return QPixmap.fromImage(overlay)

    def _is_video_seek_context(self) -> bool:
        fw = self.focusWidget()
        return fw in (self.video_widget, self.timeline_slider)

    def _step_frame(self, direction: int, step: int) -> None:
        if self.video_info is None:
            return
        current_frame = self._frame_from_ms(self.player.position())
        target = max(1, min(self.video_info.total_frames, current_frame + (direction * step)))
        self.player.setPosition(self._ms_from_frame(target))

    def _on_key_seek_tick(self) -> None:
        if self._key_seek_direction == 0:
            return
        self._key_seek_tick_count += 1
        if self._key_seek_tick_count <= 4:
            step = 1
        elif self._key_seek_tick_count <= 10:
            step = 3
        else:
            step = 7
        self._step_frame(self._key_seek_direction, step)

    def _extract_supported_path_from_event(self, event) -> Path | None:  # noqa: ANN001
        mime = event.mimeData()
        if not mime or not mime.hasUrls():
            return None
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if self._is_supported_video_file(path):
                return path
        return None

    def _handle_drag_enter_or_move(self, event) -> bool:  # noqa: ANN001
        path = self._extract_supported_path_from_event(event)
        if path is None:
            self._hide_drop_overlay()
            event.ignore()
            return False
        self._show_drop_overlay()
        event.acceptProposedAction()
        return True

    def _handle_drop(self, event) -> bool:  # noqa: ANN001
        self._hide_drop_overlay()
        path = self._extract_supported_path_from_event(event)
        if path is None:
            event.ignore()
            return False
        self._load_video_file(path)
        event.acceptProposedAction()
        return True

    def _handle_drag_leave(self, event) -> None:  # noqa: ANN001
        # Avoid flicker when drag target moves between child widgets inside this window.
        if self.frameGeometry().contains(QCursor.pos()):
            event.ignore()
            return
        self._hide_drop_overlay()
        event.accept()

    def eventFilter(self, watched, event):  # noqa: ANN001
        if watched in (self.video_widget, self.timeline_slider):
            if event.type() == QEvent.MouseButtonPress:
                watched.setFocus()
            if watched is self.video_widget and event.type() == QEvent.Resize:
                self.mask_state_badge.move(12, 12)
                self._current_mask_overlay_key = None
                self._update_mask_overlay(self._frame_from_ms(self.player.position()))
            if watched is self.video_widget and event.type() == QEvent.DragEnter:
                self._handle_drag_enter_or_move(event)
                return True
            if watched is self.video_widget and event.type() == QEvent.DragMove:
                self._handle_drag_enter_or_move(event)
                return True
            if watched is self.video_widget and event.type() == QEvent.DragLeave:
                self._handle_drag_leave(event)
                return True
            if watched is self.video_widget and event.type() == QEvent.Drop:
                self._handle_drop(event)
                return True

        if event.type() == QEvent.KeyPress:
            if not self._is_video_seek_context():
                return super().eventFilter(watched, event)
            key = event.key()
            if key in (Qt.Key_Left, Qt.Key_Right):
                direction = -1 if key == Qt.Key_Left else 1
                if self._key_seek_direction != direction:
                    self._key_seek_direction = direction
                    self._key_seek_tick_count = 0
                    self._step_frame(direction, 1)
                if not self._key_seek_timer.isActive():
                    self._key_seek_timer.start()
                return True

        if event.type() == QEvent.KeyRelease:
            key = event.key()
            if key in (Qt.Key_Left, Qt.Key_Right) and (
                self._key_seek_timer.isActive() or self._key_seek_direction != 0
            ):
                self._key_seek_timer.stop()
                self._key_seek_direction = 0
                self._key_seek_tick_count = 0
                return True

        return super().eventFilter(watched, event)

    @staticmethod
    def _is_supported_video_file(path: Path) -> bool:
        return path.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm"}

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        self._handle_drag_enter_or_move(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        self._handle_drag_enter_or_move(event)

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._handle_drag_leave(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._handle_drop(event)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._resize_overlays()

    def moveEvent(self, event) -> None:  # noqa: ANN001
        super().moveEvent(event)
        self._resize_overlays()

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

        self._save_ui_settings()

        if self.lama_manager is not None:
            try:
                self.lama_manager.stop_all()
            except Exception as exc:  # pylint: disable=broad-except
                self.log(f"Failed to stop some lama-cleaner processes: {exc}")

        event.accept()
