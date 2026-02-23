from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider

from .models import Segment


class SegmentTimelineSlider(QSlider):
    hoverMoved = Signal(int, int, int)
    hoverLeft = Signal()

    def __init__(self, orientation=Qt.Horizontal, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
        self._segments: list[Segment] = []
        self._total_frames: int = 0

    def set_segment_data(self, segments: Iterable[Segment], total_frames: int) -> None:
        self._segments = list(segments)
        self._total_frames = max(0, int(total_frames))
        self.update()

    def _slider_option(self) -> QStyleOptionSlider:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        return option

    def _groove_rect(self) -> QRect:
        option = self._slider_option()
        return self.style().subControlRect(
            QStyle.CC_Slider,
            option,
            QStyle.SC_SliderGroove,
            self,
        )

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)

        if self.orientation() != Qt.Horizontal:
            return
        if self._total_frames <= 0 or not self._segments:
            return

        groove = self._groove_rect()
        if groove.width() <= 2:
            return

        painter = QPainter(self)
        for segment in self._segments:
            left_ratio = (segment.start_frame - 1) / max(1, self._total_frames - 1)
            right_ratio = segment.end_frame / max(1, self._total_frames)

            x1 = groove.left() + int(left_ratio * groove.width())
            x2 = groove.left() + int(right_ratio * groove.width())
            width = max(2, x2 - x1)
            rect = QRect(x1, groove.top(), width, groove.height())

            if segment.mask_path:
                color = QColor(255, 92, 92, 120)
            else:
                color = QColor(140, 140, 140, 100)
            painter.fillRect(rect, color)

    def _value_from_mouse_pos(self, pos: QPoint) -> int:
        groove = self._groove_rect()
        x = min(max(pos.x(), groove.left()), groove.right())
        offset = x - groove.left()
        return QStyle.sliderValueFromPosition(
            self.minimum(),
            self.maximum(),
            offset,
            max(1, groove.width()),
            upsideDown=False,
        )

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        value = self._value_from_mouse_pos(event.position().toPoint())
        global_pos = self.mapToGlobal(event.position().toPoint())
        self.hoverMoved.emit(value, global_pos.x(), global_pos.y())

    def enterEvent(self, event) -> None:  # noqa: ANN001
        super().enterEvent(event)
        cursor = self.mapFromGlobal(self.cursor().pos())
        value = self._value_from_mouse_pos(cursor)
        global_pos = self.mapToGlobal(cursor)
        self.hoverMoved.emit(value, global_pos.x(), global_pos.y())

    def leaveEvent(self, event) -> None:  # noqa: ANN001
        super().leaveEvent(event)
        self.hoverLeft.emit()
