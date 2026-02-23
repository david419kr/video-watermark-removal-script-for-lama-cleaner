from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


class MaskCanvas(QWidget):
    def __init__(self, reference_image: QImage, existing_mask: QImage | None = None) -> None:
        super().__init__()
        self.reference_image = reference_image.convertToFormat(QImage.Format_RGB32)
        self.draw_layer = QImage(self.reference_image.size(), QImage.Format_ARGB32)
        self.draw_layer.fill(Qt.transparent)

        self.brush_size = 20
        self.erase_mode = False
        self._last_point: QPoint | None = None

        if existing_mask is not None:
            self._apply_existing_mask(existing_mask)

        self.setMinimumSize(640, 360)
        self.setMouseTracking(True)

    def _apply_existing_mask(self, mask: QImage) -> None:
        resized = mask.convertToFormat(QImage.Format_Grayscale8)
        if resized.size() != self.reference_image.size():
            resized = resized.scaled(
                self.reference_image.size(),
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation,
            )

        for y in range(resized.height()):
            for x in range(resized.width()):
                value = QColor.fromRgb(resized.pixel(x, y)).red()
                if value > 0:
                    self.draw_layer.setPixelColor(x, y, QColor(255, 255, 255, 220))

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(24, 24, 24))

        target = self._target_rect()
        if target.width() <= 0 or target.height() <= 0:
            return

        painter.drawImage(target, self.reference_image)
        painter.setOpacity(0.55)
        painter.drawImage(target, self.draw_layer)
        painter.setOpacity(1.0)

    def _target_rect(self) -> QRectF:
        iw = self.reference_image.width()
        ih = self.reference_image.height()
        if iw <= 0 or ih <= 0:
            return QRectF()

        scale = min(self.width() / iw, self.height() / ih)
        tw = iw * scale
        th = ih * scale
        tx = (self.width() - tw) / 2
        ty = (self.height() - th) / 2
        return QRectF(tx, ty, tw, th)

    def _map_to_image_point(self, point: QPoint) -> QPoint | None:
        target = self._target_rect()
        if not target.contains(point):
            return None

        rel_x = (point.x() - target.x()) / target.width()
        rel_y = (point.y() - target.y()) / target.height()
        x = max(0, min(self.reference_image.width() - 1, int(rel_x * self.reference_image.width())))
        y = max(0, min(self.reference_image.height() - 1, int(rel_y * self.reference_image.height())))
        return QPoint(x, y)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        point = self._map_to_image_point(event.position().toPoint())
        if point is None:
            return
        self._last_point = point
        self._draw_line(point, point)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not (event.buttons() & Qt.LeftButton):
            return
        if self._last_point is None:
            return
        point = self._map_to_image_point(event.position().toPoint())
        if point is None:
            return
        self._draw_line(self._last_point, point)
        self._last_point = point

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._last_point = None

    def _draw_line(self, start: QPoint, end: QPoint) -> None:
        painter = QPainter(self.draw_layer)
        pen = QPen()
        pen.setWidth(self.brush_size)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        if self.erase_mode:
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            pen.setColor(Qt.transparent)
        else:
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            pen.setColor(QColor(255, 255, 255, 220))
        painter.setPen(pen)
        painter.drawLine(start, end)
        painter.end()
        self.update()

    def clear_mask(self) -> None:
        self.draw_layer.fill(Qt.transparent)
        self.update()

    def build_binary_mask(self) -> QImage:
        mask = QImage(self.draw_layer.size(), QImage.Format_Grayscale8)
        mask.fill(0)

        for y in range(self.draw_layer.height()):
            for x in range(self.draw_layer.width()):
                alpha = QColor.fromRgba(self.draw_layer.pixel(x, y)).alpha()
                if alpha > 0:
                    mask.setPixel(x, y, 255)
        return mask


class MaskEditorDialog(QDialog):
    def __init__(
        self,
        reference_image_path: Path,
        save_mask_path: Path,
        existing_mask_path: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Draw Segment Mask")
        self.resize(980, 720)

        self._save_mask_path = save_mask_path

        reference_image = QImage(str(reference_image_path))
        if reference_image.isNull():
            raise RuntimeError(f"Failed to load reference frame image: {reference_image_path}")

        existing_mask = None
        if existing_mask_path and existing_mask_path.exists():
            maybe_mask = QImage(str(existing_mask_path))
            if not maybe_mask.isNull():
                existing_mask = maybe_mask

        self.canvas = MaskCanvas(reference_image, existing_mask=existing_mask)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout()
        control_row = QHBoxLayout()

        control_row.addWidget(QLabel("Brush"))
        self.brush_slider = QSlider(Qt.Horizontal)
        self.brush_slider.setMinimum(2)
        self.brush_slider.setMaximum(128)
        self.brush_slider.setValue(self.canvas.brush_size)
        self.brush_slider.valueChanged.connect(self._on_brush_change)
        control_row.addWidget(self.brush_slider, 1)

        self.eraser_box = QCheckBox("Eraser")
        self.eraser_box.stateChanged.connect(self._on_eraser_toggle)
        control_row.addWidget(self.eraser_box)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.canvas.clear_mask)
        control_row.addWidget(clear_btn)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        control_row.addWidget(save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        control_row.addWidget(cancel_btn)

        root.addLayout(control_row)
        root.addWidget(self.canvas, 1)
        self.setLayout(root)

    def _on_brush_change(self, value: int) -> None:
        self.canvas.brush_size = value

    def _on_eraser_toggle(self, state: int) -> None:
        self.canvas.erase_mode = state == Qt.Checked

    def _save(self) -> None:
        self._save_mask_path.parent.mkdir(parents=True, exist_ok=True)
        mask_image = self.canvas.build_binary_mask()
        ok = mask_image.save(str(self._save_mask_path), "PNG")
        if not ok:
            QMessageBox.critical(self, "Save Failed", "Failed to save mask image.")
            return
        self.accept()
