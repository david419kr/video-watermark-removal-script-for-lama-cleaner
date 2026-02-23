from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


TOOL_BRUSH = "brush"
TOOL_ERASER = "eraser"
TOOL_RECT = "rect"
TOOL_ELLIPSE = "ellipse"


class MaskCanvas(QWidget):
    def __init__(self, reference_image: QImage, existing_mask: QImage | None = None) -> None:
        super().__init__()
        self.reference_image = reference_image.convertToFormat(QImage.Format_RGB32)
        self.mask_layer = QImage(self.reference_image.size(), QImage.Format_Grayscale8)
        self.mask_layer.fill(0)

        self.tool = TOOL_BRUSH
        self.brush_size = 20
        self._drag_start: QPoint | None = None
        self._last_point: QPoint | None = None
        self._preview_end: QPoint | None = None

        if existing_mask is not None:
            self._apply_existing_mask(existing_mask)

        self.setMinimumSize(640, 360)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def _apply_existing_mask(self, mask: QImage) -> None:
        resized = mask.convertToFormat(QImage.Format_Grayscale8)
        if resized.size() != self.reference_image.size():
            resized = resized.scaled(
                self.reference_image.size(),
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation,
            )
        painter = QPainter(self.mask_layer)
        painter.drawImage(0, 0, resized)
        painter.end()

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

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(24, 24, 24))

        target = self._target_rect()
        if target.width() <= 0 or target.height() <= 0:
            return

        painter.drawImage(target, self.reference_image)

        painter.setOpacity(0.55)
        painter.drawImage(target, self.mask_layer)
        painter.setOpacity(1.0)

        if self._drag_start is not None and self._preview_end is not None and self.tool in (TOOL_RECT, TOOL_ELLIPSE):
            self._paint_preview_shape(painter, target)

    def _paint_preview_shape(self, painter: QPainter, target_rect: QRectF) -> None:
        assert self._drag_start is not None
        assert self._preview_end is not None
        preview_rect = QRect(self._drag_start, self._preview_end).normalized()

        scale_x = target_rect.width() / max(1, self.reference_image.width())
        scale_y = target_rect.height() / max(1, self.reference_image.height())
        screen_rect = QRect(
            int(target_rect.x() + preview_rect.left() * scale_x),
            int(target_rect.y() + preview_rect.top() * scale_y),
            int(preview_rect.width() * scale_x),
            int(preview_rect.height() * scale_y),
        )

        pen = QPen(QColor(80, 220, 120, 220))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if self.tool == TOOL_RECT:
            painter.drawRect(screen_rect)
        else:
            painter.drawEllipse(screen_rect)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        self.setFocus()

        point = self._map_to_image_point(event.position().toPoint())
        if point is None:
            return

        if self.tool in (TOOL_BRUSH, TOOL_ERASER):
            self._last_point = point
            self._draw_line(point, point)
        else:
            self._drag_start = point
            self._preview_end = point
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not (event.buttons() & Qt.LeftButton):
            return

        point = self._map_to_image_point(event.position().toPoint())
        if point is None:
            return

        if self.tool in (TOOL_BRUSH, TOOL_ERASER):
            if self._last_point is None:
                self._last_point = point
            self._draw_line(self._last_point, point)
            self._last_point = point
        else:
            if self._drag_start is None:
                return
            self._preview_end = point
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return

        if self.tool in (TOOL_BRUSH, TOOL_ERASER):
            self._last_point = None
            return

        if self._drag_start is None or self._preview_end is None:
            return
        self._draw_shape(self._drag_start, self._preview_end, self.tool)
        self._drag_start = None
        self._preview_end = None
        self.update()

    def _draw_line(self, start: QPoint, end: QPoint) -> None:
        painter = QPainter(self.mask_layer)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setCompositionMode(QPainter.CompositionMode_Source)

        value = 0 if self.tool == TOOL_ERASER else 255
        pen = QPen(QColor(value, value, value))
        pen.setWidth(self.brush_size)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(start, end)
        painter.end()
        self.update()

    def _draw_shape(self, start: QPoint, end: QPoint, tool: str) -> None:
        rect = QRect(start, end).normalized()
        if rect.width() < 1 and rect.height() < 1:
            return

        painter = QPainter(self.mask_layer)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255))

        if tool == TOOL_RECT:
            painter.drawRect(rect)
        elif tool == TOOL_ELLIPSE:
            painter.drawEllipse(rect)
        painter.end()

    def clear_mask(self) -> None:
        self.mask_layer.fill(0)
        self.update()

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self._drag_start = None
        self._preview_end = None
        self._last_point = None
        self.update()

    def build_binary_mask(self) -> QImage:
        mask = QImage(self.mask_layer.size(), QImage.Format_Grayscale8)
        for y in range(self.mask_layer.height()):
            for x in range(self.mask_layer.width()):
                value = QColor.fromRgb(self.mask_layer.pixel(x, y)).red()
                mask.setPixel(x, y, 255 if value > 127 else 0)
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
        self.resize(1040, 760)

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

        control_row.addWidget(QLabel("Tool"))
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)

        brush_btn = self._new_tool_button("Brush", TOOL_BRUSH, checked=True)
        eraser_btn = self._new_tool_button("Eraser", TOOL_ERASER)
        rect_btn = self._new_tool_button("Rectangle", TOOL_RECT)
        ellipse_btn = self._new_tool_button("Ellipse", TOOL_ELLIPSE)

        control_row.addWidget(brush_btn)
        control_row.addWidget(eraser_btn)
        control_row.addWidget(rect_btn)
        control_row.addWidget(ellipse_btn)

        control_row.addSpacing(12)
        control_row.addWidget(QLabel("Size"))
        self.brush_slider = QSlider(Qt.Horizontal)
        self.brush_slider.setMinimum(2)
        self.brush_slider.setMaximum(128)
        self.brush_slider.setValue(self.canvas.brush_size)
        self.brush_slider.valueChanged.connect(self._on_brush_change)
        control_row.addWidget(self.brush_slider, 1)

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

    def _new_tool_button(self, text: str, tool: str, checked: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setCheckable(True)
        button.setChecked(checked)
        button.clicked.connect(lambda: self.canvas.set_tool(tool))
        self.tool_group.addButton(button)
        return button

    def _on_brush_change(self, value: int) -> None:
        self.canvas.brush_size = value

    def _save(self) -> None:
        self._save_mask_path.parent.mkdir(parents=True, exist_ok=True)
        mask_image = self.canvas.build_binary_mask()
        ok = mask_image.save(str(self._save_mask_path), "PNG")
        if not ok:
            QMessageBox.critical(self, "Save Failed", "Failed to save mask image.")
            return
        self.accept()
