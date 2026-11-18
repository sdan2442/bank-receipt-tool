from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QListWidget,
    QMessageBox, QPushButton, QVBoxLayout,
)

from .manual_split import validate_regions
from .page_regions import Rect


class CropView(QGraphicsView):
    rectangle_drawn = Signal(object)
    rectangle_adjusted = Signal(int, object)
    rectangle_selected = Signal(int)

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.scene().addPixmap(pixmap)
        self.page = QRectF(pixmap.rect())
        self.setSceneRect(self.page)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.regions: list[Rect] = []
        self.selected = -1
        self.force_draw = False
        self.outlines = []
        self.start: QPointF | None = None
        self.draft = None
        self.drag_mode = "draw"
        self.original: QRectF | None = None
        self.handle = (0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.page.isEmpty():
            self.fitInView(self.page, Qt.AspectRatioMode.KeepAspectRatio)

    def show_regions(self, regions: list[Rect], selected: int = -1):
        for item in self.outlines:
            self.scene().removeItem(item)
        self.regions = list(regions)
        self.selected = selected
        self.outlines = []
        for index, (x, y, width, height) in enumerate(self.regions):
            color = QColor("#2d8bff" if index == selected else "#fa503c")
            pen = QPen(color, 3 if index == selected else 2)
            pen.setCosmetic(True)
            outline = self.scene().addRect(x, y, width, height, pen)
            outline.setZValue(2 if index == selected else 1)
            self.outlines.append(outline)
            label = self.scene().addSimpleText(str(index + 1))
            label.setBrush(color)
            label.setZValue(3)
            label.setPos(x + 3, y + 3)
            label.setFlag(label.GraphicsItemFlag.ItemIgnoresTransformations)
            self.outlines.append(label)
        if 0 <= selected < len(self.regions):
            rect = QRectF(*self.regions[selected])
            for _, point in self.handles(rect):
                dot = self.scene().addEllipse(
                    -4.5, -4.5, 9, 9, QPen(QColor("#2d8bff"), 1.5), QColor("white")
                )
                dot.setPos(point)
                dot.setFlag(dot.GraphicsItemFlag.ItemIgnoresTransformations)
                dot.setZValue(4)
                self.outlines.append(dot)

    @staticmethod
    def handles(rect: QRectF):
        center = rect.center()
        for horizontal, x in ((-1, rect.left()), (0, center.x()), (1, rect.right())):
            for vertical, y in ((-1, rect.top()), (0, center.y()), (1, rect.bottom())):
                if horizontal or vertical:
                    yield (horizontal, vertical), QPointF(x, y)

    def handle_at(self, position):
        if not 0 <= self.selected < len(self.regions):
            return None
        rect = QRectF(*self.regions[self.selected])
        for handle, point in self.handles(rect):
            if (self.mapFromScene(point) - position).manhattanLength() <= 10:
                return handle
        return None

    def region_at(self, point: QPointF) -> int:
        if 0 <= self.selected < len(self.regions) and QRectF(*self.regions[self.selected]).contains(point):
            return self.selected
        return next(
            (index for index in reversed(range(len(self.regions)))
             if QRectF(*self.regions[index]).contains(point)), -1,
        )

    def mousePressEvent(self, event):
        point = self.mapToScene(event.position().toPoint())
        handle = None if self.force_draw else self.handle_at(event.position().toPoint())
        if event.button() != Qt.MouseButton.LeftButton or (not handle and not self.page.contains(point)):
            super().mousePressEvent(event)
            return
        self.start = point
        index = -1 if self.force_draw else self.region_at(point)
        if handle:
            self.drag_mode = "resize"
            self.handle = handle
            self.original = QRectF(*self.regions[self.selected])
        elif index >= 0:
            self.rectangle_selected.emit(index)
            self.drag_mode = "move"
            self.original = QRectF(*self.regions[index])
        else:
            self.drag_mode = "draw"
            self.original = None
        pen = QPen(QColor("#2d8bff"), 2)
        pen.setCosmetic(True)
        self.draft = self.scene().addRect(self.original or QRectF(point, point), pen)
        self.draft.setZValue(5)

    def drag_rect(self, event) -> QRectF:
        point = self.mapToScene(event.position().toPoint())
        if self.drag_mode == "draw":
            return QRectF(self.start, point).normalized().intersected(self.page)
        rect = QRectF(self.original)
        if self.drag_mode == "move":
            delta = point - self.start
            rect.moveTo(
                max(0, min(self.page.width() - rect.width(), rect.left() + delta.x())),
                max(0, min(self.page.height() - rect.height(), rect.top() + delta.y())),
            )
            return rect
        horizontal, vertical = self.handle
        delta = point - self.start
        if horizontal < 0:
            rect.setLeft(max(0, min(rect.right() - 8, rect.left() + delta.x())))
        elif horizontal > 0:
            rect.setRight(min(self.page.width(), max(rect.left() + 8, rect.right() + delta.x())))
        if vertical < 0:
            rect.setTop(max(0, min(rect.bottom() - 8, rect.top() + delta.y())))
        elif vertical > 0:
            rect.setBottom(min(self.page.height(), max(rect.top() + 8, rect.bottom() + delta.y())))
        return rect

    def mouseMoveEvent(self, event):
        if self.draft is not None:
            self.draft.setRect(self.drag_rect(event))
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.draft is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        rect = self.drag_rect(event)
        self.scene().removeItem(self.draft)
        self.draft = self.start = None
        x, y = round(rect.left()), round(rect.top())
        width, height = round(rect.right()) - x, round(rect.bottom()) - y
        if min(width, height) >= 8:
            region = (x, y, width, height)
            if self.drag_mode == "draw":
                self.rectangle_drawn.emit(region)
            elif region != self.regions[self.selected]:
                self.rectangle_adjusted.emit(self.selected, region)


class ManualSplitDialog(QDialog):
    def __init__(self, pixmap: QPixmap, suggestions: list[Rect], parent=None):
        super().__init__(parent)
        self.setWindowTitle("手动拆分页面")
        self.resize(1040, 800)
        self.shape = pixmap.height(), pixmap.width()
        self.view = CropView(pixmap)
        self.editing_index: int | None = None
        self.drawing_new = False
        self.undo_stack: list[tuple[list[Rect], int]] = []
        layout = QVBoxLayout(self)
        instructions = QLabel("拖动鼠标框选裁片。允许重叠，同一区域可以保存在多个 PDF 中。")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        editor = QHBoxLayout()
        editor.addWidget(self.view, 1)
        sidebar = QVBoxLayout()
        sidebar.addWidget(QLabel("裁片列表"))
        self.region_list = QListWidget()
        self.region_list.setFixedWidth(180)
        sidebar.addWidget(self.region_list, 1)
        self.add_button = QPushButton("新增裁片")
        self.redraw_button = QPushButton("重画选中框")
        self.remove_button = QPushButton("删除选中框")
        self.add_button.clicked.connect(self.toggle_add)
        self.redraw_button.clicked.connect(self.toggle_redraw)
        self.remove_button.clicked.connect(self.remove_selected)
        sidebar.addWidget(self.add_button)
        sidebar.addWidget(self.redraw_button)
        sidebar.addWidget(self.remove_button)
        editor.addLayout(sidebar)
        layout.addLayout(editor, 1)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        actions = QHBoxLayout()
        for text, callback in (
            ("使用建议框", lambda: self.set_regions(suggestions, 0)),
            ("撤销", self.undo),
            ("清空", lambda: self.set_regions([])),
            ("取消", self.reject),
            ("生成裁片", self.confirm),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            if text == "使用建议框":
                button.setEnabled(bool(suggestions))
            actions.addWidget(button)
        layout.addLayout(actions)
        self.view.rectangle_drawn.connect(self.add_or_replace)
        self.view.rectangle_adjusted.connect(self.adjust_region)
        self.view.rectangle_selected.connect(self.region_list.setCurrentRow)
        self.region_list.currentRowChanged.connect(self.selection_changed)
        self.update_controls()

    def set_regions(self, regions: list[Rect], selected: int = -1, *, record: bool = True):
        if record:
            self.undo_stack.append((list(self.view.regions), self.region_list.currentRow()))
        self.editing_index = None
        self.drawing_new = False
        self.region_list.blockSignals(True)
        self.region_list.clear()
        for index, (_, _, width, height) in enumerate(regions, 1):
            self.region_list.addItem(f"裁片 {index}   {width} × {height}")
        self.region_list.setCurrentRow(selected)
        self.region_list.blockSignals(False)
        self.view.show_regions(regions, selected)
        self.update_controls()

    def selection_changed(self, index: int):
        self.editing_index = None
        self.drawing_new = False
        self.view.show_regions(self.view.regions, index)
        self.update_controls()

    def update_controls(self):
        selected = self.region_list.currentRow() >= 0
        self.redraw_button.setEnabled(selected)
        self.remove_button.setEnabled(selected)
        editing = self.editing_index is not None
        self.view.force_draw = editing or self.drawing_new
        self.add_button.setText("取消新增" if self.drawing_new else "新增裁片")
        self.redraw_button.setText("取消重画" if editing else "重画选中框")
        self.hint.setText(
            f"正在重画裁片 {self.editing_index + 1}。拖出新范围后替换这个框。"
            if editing else "拖出新裁片范围，可从现有框内开始。"
            if self.drawing_new else "点选裁片后，拖边或角调整大小，拖框内移动。空白处拖动可新增。"
        )

    def toggle_add(self):
        self.drawing_new = not self.drawing_new
        self.editing_index = None
        self.update_controls()

    def toggle_redraw(self):
        index = self.region_list.currentRow()
        self.drawing_new = False
        self.editing_index = index if index >= 0 and self.editing_index is None else None
        self.update_controls()

    def add_or_replace(self, rect: Rect):
        regions = list(self.view.regions)
        index = self.editing_index
        if index is None:
            index = len(regions)
            regions.append(rect)
        else:
            regions[index] = rect
        self.set_regions(regions, index)

    def adjust_region(self, index: int, region: Rect):
        regions = list(self.view.regions)
        regions[index] = region
        self.set_regions(regions, index)

    def remove_selected(self):
        index = self.region_list.currentRow()
        if index >= 0:
            regions = list(self.view.regions)
            regions.pop(index)
            self.set_regions(regions, min(index, len(regions) - 1))

    def undo(self):
        if self.undo_stack:
            regions, selected = self.undo_stack.pop()
            self.set_regions(regions, selected, record=False)

    def confirm(self):
        try:
            validate_regions(self.view.regions, self.shape)
        except ValueError as exc:
            QMessageBox.warning(self, "请检查裁切框", str(exc))
            return
        self.accept()
