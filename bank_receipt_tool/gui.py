from __future__ import annotations

import os
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import fitz
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QInputDialog, QListWidget, QListWidgetItem, QProgressBar, QPushButton, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from . import __version__
from .config import AppConfig
from .checkpoint import CheckpointStore, JobCheckpoint
from .bill_merge import bill_merge_candidates, merge_bill_results, page_merge_candidates
from .document_classifier import document_type_label
from .domain import ProcessResult
from .exporter import export_xlsx, images_to_pdf_atomic, review_image_path
from .invoice import buyer_name, invoice_number, invoice_total, issue_date
from .manual_split import create_manual_crops
from .naming import suggested_filename, unique_path
from .pipeline import ReceiptProcessor, RunOptions
from .split_dialog import ManualSplitDialog


class Worker(QObject):
    progress = Signal(int, int, str)
    result_ready = Signal(object)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, config: AppConfig, inputs: list[Path], options: RunOptions):
        super().__init__()
        self.processor = ReceiptProcessor(
            config,
            lambda done, total, text: self.progress.emit(done, total, text),
            lambda result: self.result_ready.emit(result),
        )
        # Freeze the selected inputs for this run. Later UI edits can never alter
        # an already-started task.
        self.inputs, self.options = list(inputs), options

    @Slot()
    def run(self):
        try:
            self.finished.emit(self.processor.run(self.inputs, self.options))
        except Exception as exc:
            self.failed.emit(str(exc))


class ReviewDialog(QDialog):
    def __init__(
        self, result: ProcessResult, output_dir: Path, parent=None, split_page=None,
        *, result_lock=None, persist_review=None,
    ):
        super().__init__(parent)
        self.result, self.output_dir = result, output_dir
        self.is_invoice = result.document_type == "invoice"
        self.result_lock = result_lock
        self.persist_review = persist_review
        self.setWindowTitle("人工复核")
        self.resize(1180, 760)
        splitter = QSplitter()
        preview = QWidget(); preview_layout = QVBoxLayout(preview)
        self.preview_paths = list(result.merged_page_paths) or [result.unit.image_path]
        if not result.merged_page_paths and not result.unit.image_path.is_file():
            try:
                self.preview_paths = [review_image_path(result, output_dir)]
            except (OSError, ValueError, RuntimeError):
                pass
        self.preview_index = 0
        self.image = QLabel(alignment=Qt.AlignCenter)
        preview_layout.addWidget(self.image, 1)
        self.page_label = QLabel(alignment=Qt.AlignCenter)
        previous_page = QPushButton("上一页"); next_page = QPushButton("下一页")
        previous_page.clicked.connect(lambda: self.show_preview(self.preview_index - 1))
        next_page.clicked.connect(lambda: self.show_preview(self.preview_index + 1))
        navigation = QHBoxLayout(); navigation.addWidget(previous_page)
        navigation.addWidget(self.page_label); navigation.addWidget(next_page)
        if len(self.preview_paths) > 1:
            preview_layout.addLayout(navigation)
        self.show_preview(0)
        splitter.addWidget(preview)
        right = QWidget(); right_layout = QVBoxLayout(right)
        origin = QLabel(
            f"原文件：{result.unit.source_name}　 第 {result.unit.page_number} 页 / 裁切 {result.unit.crop_number}　"
            f"类型：{document_type_label(result.document_type)}（{result.document_type_confidence:.1%}）"
        )
        if result.merged_page_labels:
            origin.setText(
                f"{origin.text()}\n合并顺序：" + " → ".join(result.merged_page_labels)
            )
        origin.setWordWrap(True); right_layout.addWidget(origin)
        form = QFormLayout()
        field_labels = (
            (("invoice_number", "发票号"), ("buyer", "购买方"), ("amount", "价税合计（小写）"), ("date", "开票日期"))
            if self.is_invoice else
            (("date", "日期"), ("payer", "付款人"), ("payee", "收款人"), ("amount", "金额"))
        )
        self.fields_edits: dict[str, QComboBox] = {}
        for key, label in field_labels:
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems([candidate.value for candidate in getattr(result.fields, f"{key}_candidates")])
            combo.setCurrentText(getattr(result.fields, key) or "")
            combo.currentTextChanged.connect(self.refresh_name)
            self.fields_edits[key] = combo
            form.addRow(f"{label}（{getattr(result.fields, f'{key}_confidence'):.1%}）", combo)
        name = (suggested_filename(result.fields, result.document_type)
                if self.is_invoice and not result.needs_split else result.suggested_name)
        self.filename = QLineEdit(name)
        self.filename.setReadOnly(True)
        form.addRow("推荐文件名", self.filename)
        right_layout.addLayout(form)
        reasons = QLabel("复核原因：" + ("；".join(result.reasons) or "无"))
        reasons.setWordWrap(True)
        right_layout.addWidget(reasons)
        if result.unit.boundary_reasons and result.unit.suggested_boxes:
            right_layout.addWidget(QLabel("红框为建议裁切边界，可在手动拆分中采用或重画。"))
        right_layout.addWidget(QLabel("OCR 全文（只保存在本机）："))
        ocr = QPlainTextEdit(result.full_text); ocr.setReadOnly(True); right_layout.addWidget(ocr, 1)
        buttons = QHBoxLayout(); save = QPushButton("确认并保存"); close = QPushButton("稍后复核")
        save.clicked.connect(self.save); close.clicked.connect(self.reject)
        save.setEnabled(not result.needs_split and result.status != "split")
        if split_page and len(self.preview_paths) == 1 and not result.merged_page_paths and result.status != "split":
            split = QPushButton("手动拆分")
            split.setEnabled(self.preview_paths[0].is_file())
            split.clicked.connect(
                lambda: self.reject() if split_page(result, self.preview_paths[0], self) else None
            )
            buttons.addWidget(split)
        buttons.addStretch(); buttons.addWidget(close); buttons.addWidget(save); right_layout.addLayout(buttons)
        splitter.addWidget(right); splitter.setSizes([580, 600])
        layout = QVBoxLayout(self); layout.addWidget(splitter)

    def show_preview(self, index: int):
        if not self.preview_paths:
            return
        self.preview_index = max(0, min(index, len(self.preview_paths) - 1))
        pix = QPixmap(str(self.preview_paths[self.preview_index]))
        if (
            len(self.preview_paths) == 1
            and self.result.unit.boundary_reasons
            and self.result.unit.suggested_boxes
            and not pix.isNull()
        ):
            painter = QPainter(pix)
            painter.setPen(QPen(QColor(255, 70, 70), max(3, pix.width() // 500)))
            for x, y, width, height in self.result.unit.suggested_boxes:
                painter.drawRect(x, y, width, height)
            painter.end()
        if not pix.isNull():
            self.image.setPixmap(
                pix.scaled(560, 700, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            self.image.setText("页面预览不存在")
        self.page_label.setText(f"第 {self.preview_index + 1}/{len(self.preview_paths)} 页")

    def refresh_name(self):
        self.filename.setText(suggested_filename(self.edited_fields(), self.result.document_type))

    def edited_fields(self):
        values = {key: combo.currentText().strip() for key, combo in self.fields_edits.items()}
        if self.is_invoice:
            values = {
                "invoice_number": invoice_number(values["invoice_number"]),
                "buyer": buyer_name(values["buyer"]),
                "amount": invoice_total(values["amount"]),
                "date": issue_date(values["date"]),
            }
        return replace(self.result.fields, **{key: value or None for key, value in values.items()})

    def save(self):
        if self.result.needs_split or self.result.status == "split":
            return
        fields = self.edited_fields()
        required = (fields.invoice_number, fields.buyer, fields.amount, fields.date) if self.is_invoice else (
            fields.date, fields.payer, fields.amount
        )
        if not all(required):
            message = ("请确认发票号为 8 位或 20 位数字，并填写有效购买方、价税合计和开票日期。"
                       if self.is_invoice else "日期、付款人和金额均需确认后才能保存到成功目录。")
            QMessageBox.warning(self, "字段未完成", message)
            return
        name = suggested_filename(fields, self.result.document_type)
        try:
            with self.result_lock if self.result_lock is not None else nullcontext():
                target = unique_path(self.output_dir / "success", name)
                updated = replace(
                    self.result, fields=fields, output_path=target, status="success",
                    suggested_name=name, reasons=[],
                )
                images_to_pdf_atomic(self.preview_paths, target)
                try:
                    if self.persist_review is not None and not self.persist_review(updated):
                        raise OSError("复核结果无法写回历史任务")
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
                self.result.output_path = updated.output_path
                self.result.status = updated.status
                self.result.fields = updated.fields
                self.result.suggested_name = updated.suggested_name
                self.result.reasons = updated.reasons
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc)); return
        self.accept()


class BillMergeDialog(QDialog):
    """Collect pages in the exact order in which they are checked."""

    def __init__(self, candidates: list[ProcessResult], parent=None, *, bills_only=True):
        super().__init__(parent)
        self.candidates = list(candidates)
        self.selection_order: list[int] = []
        self.setWindowTitle("承兑汇票页面合并" if bills_only else "手动合并页面")
        self.resize(1100, 620)
        layout = QVBoxLayout(self)
        message = QLabel(
            "请按希望合并的先后顺序逐项勾选。勾选后会显示“顺序 1、2……”；"
            "取消勾选再重新勾选，可把该页移到最后。未勾选页面不会删除。"
        )
        message.setWordWrap(True); layout.addWidget(message)
        self.page_list = QListWidget(); self.page_list.setAlternatingRowColors(True)
        self.page_list.itemChanged.connect(self._selection_changed)
        for index, result in enumerate(self.candidates):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.page_list.addItem(item)
        preview_row = QSplitter()
        preview_row.addWidget(self.page_list)
        self.page_preview = QLabel("选择列表中的一行查看预览", alignment=Qt.AlignCenter)
        preview_row.addWidget(self.page_preview)
        preview_row.setSizes([660, 440])
        layout.addWidget(preview_row, 1)
        self.page_list.currentRowChanged.connect(self._show_page)
        self.summary = QLabel("尚未勾选页面（至少选择 2 页）")
        layout.addWidget(self.summary)
        actions = QHBoxLayout()
        select_all = QPushButton("按当前列表顺序全选")
        clear = QPushButton("全部取消")
        merge = QPushButton("合并并进入人工核对")
        later = QPushButton("稍后处理")
        select_all.clicked.connect(self._select_all)
        clear.clicked.connect(self._clear)
        merge.clicked.connect(self._accept_merge)
        later.clicked.connect(self.reject)
        actions.addWidget(select_all); actions.addWidget(clear); actions.addStretch()
        actions.addWidget(later); actions.addWidget(merge)
        layout.addLayout(actions)
        self._refresh_items()

    def _show_page(self, index: int):
        if index < 0:
            return
        result = self.candidates[index]
        pixmap = QPixmap(str(result.unit.image_path))
        if pixmap.isNull() and result.output_path and result.output_path.is_file():
            try:
                with fitz.open(result.output_path) as document:
                    page = document[0]
                    scale = min(1, 520 / max(page.rect.width, page.rect.height))
                    pixmap.loadFromData(page.get_pixmap(matrix=fitz.Matrix(scale, scale)).tobytes("png"))
            except (OSError, ValueError, RuntimeError):
                pass
        if pixmap.isNull():
            self.page_preview.setText("该页预览不可用")
        else:
            self.page_preview.setPixmap(pixmap.scaled(400, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _selection_changed(self, item: QListWidgetItem):
        index = int(item.data(Qt.ItemDataRole.UserRole))
        if item.checkState() == Qt.CheckState.Checked:
            if index not in self.selection_order:
                self.selection_order.append(index)
        elif index in self.selection_order:
            self.selection_order.remove(index)
        self._refresh_items()

    def _refresh_items(self):
        self.page_list.blockSignals(True)
        try:
            for row, result in enumerate(self.candidates):
                item = self.page_list.item(row)
                order = self.selection_order.index(row) + 1 if row in self.selection_order else 0
                prefix = f"顺序 {order}" if order else "未选择"
                item.setText(
                    f"[{prefix}] {result.unit.source_name}｜第 {result.unit.page_number} 页/裁切 "
                    f"{result.unit.crop_number}｜{document_type_label(result.document_type)}｜"
                    f"{result.suggested_name}"
                )
        finally:
            self.page_list.blockSignals(False)
        self.summary.setText(
            f"已选择 {len(self.selection_order)} 页；合并顺序严格按勾选先后排列"
            if self.selection_order else "尚未勾选页面（至少选择 2 页）"
        )

    def _select_all(self):
        for row in range(self.page_list.count()):
            self.page_list.item(row).setCheckState(Qt.CheckState.Checked)

    def _clear(self):
        for row in range(self.page_list.count()):
            self.page_list.item(row).setCheckState(Qt.CheckState.Unchecked)

    def _accept_merge(self):
        if len(self.selection_order) < 2:
            QMessageBox.information(self, "请选择页面", "至少按顺序勾选两页。")
            return
        self.accept()

    def selected_results(self) -> list[ProcessResult]:
        return [self.candidates[index] for index in self.selection_order]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = AppConfig.default(); self.inputs: list[Path] = []; self.results: list[ProcessResult] = []
        self.worker = None; self.thread = None; self.loaded_checkpoint: JobCheckpoint | None = None
        self.setWindowTitle(f"银行回单拆分与重命名工具（本地离线） v{__version__}")
        self.resize(1280, 800)
        root = QWidget(); self.setCentralWidget(root); layout = QVBoxLayout(root)
        source_box = QGroupBox("输入与输出"); grid = QGridLayout(source_box)
        self.add_files_btn = QPushButton("选择文件"); self.add_folder_btn = QPushButton("选择输入文件夹")
        self.add_files_btn.clicked.connect(self.choose_files); self.add_folder_btn.clicked.connect(self.choose_folder)
        self.input_label = QLabel("尚未选择文件"); self.input_label.setWordWrap(True)
        self.input_list = QListWidget(); self.input_list.setMaximumHeight(130); self.input_list.setAlternatingRowColors(True)
        self.input_list.itemChanged.connect(self._show_inputs)
        self.check_all_btn = QPushButton("全部勾选"); self.uncheck_all_btn = QPushButton("全部取消")
        self.remove_unchecked_btn = QPushButton("移除未勾选"); self.clear_inputs_btn = QPushButton("清空清单")
        self.check_all_btn.clicked.connect(lambda: self._set_all_inputs(True))
        self.uncheck_all_btn.clicked.connect(lambda: self._set_all_inputs(False))
        self.remove_unchecked_btn.clicked.connect(self._remove_unchecked)
        self.clear_inputs_btn.clicked.connect(self._clear_inputs)
        self.output_edit = QLineEdit(str(self.config.output_dir)); self.out_button = QPushButton("选择输出目录")
        self.out_button.clicked.connect(self.choose_output)
        grid.addWidget(self.add_files_btn, 0, 0); grid.addWidget(self.add_folder_btn, 0, 1); grid.addWidget(self.input_label, 0, 2, 1, 3)
        grid.addWidget(self.input_list, 1, 0, 1, 5)
        input_actions = QHBoxLayout(); input_actions.addWidget(self.check_all_btn); input_actions.addWidget(self.uncheck_all_btn)
        input_actions.addWidget(self.remove_unchecked_btn); input_actions.addWidget(self.clear_inputs_btn); input_actions.addStretch()
        grid.addLayout(input_actions, 2, 0, 1, 5)
        grid.addWidget(QLabel("输出目录"), 3, 0); grid.addWidget(self.output_edit, 3, 1, 1, 3); grid.addWidget(self.out_button, 3, 4)
        layout.addWidget(source_box)

        controls = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("高精度（server 检测 + server 识别）", "server")
        self.mode.addItem("均衡（mobile 检测 + server 识别）", "balanced")
        self.mode.addItem("快速（mobile 检测 + mobile 识别）", "mobile")
        self.mode.setCurrentIndex(self.mode.findData(self.config.model_mode))
        self.threshold = QSpinBox(); self.threshold.setRange(50, 99); self.threshold.setValue(72); self.threshold.setSuffix("%")
        self.run_mode = QComboBox()
        self.run_mode.addItem("从上次中断处继续", "resume")
        self.run_mode.addItem("重新处理全部页面", "restart")
        self.run_mode.addItem("处理指定页码", "range")
        self.page_range = QLineEdit(); self.page_range.setPlaceholderText("例如 13-32 或 1,3,8-10"); self.page_range.setEnabled(False)
        self.run_mode.currentIndexChanged.connect(self._toggle_page_range)
        self.start_btn = QPushButton("开始处理"); self.pause_btn = QPushButton("暂停")
        self.stop_btn = QPushButton("停止并保存进度"); self.cancel_btn = QPushButton("取消任务")
        for button in (self.pause_btn, self.stop_btn, self.cancel_btn): button.setEnabled(False)
        self.start_btn.clicked.connect(self.start); self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn.clicked.connect(self.stop_and_save); self.cancel_btn.clicked.connect(self.cancel_task)
        controls.addWidget(QLabel("OCR 模型")); controls.addWidget(self.mode)
        controls.addWidget(QLabel("复核阈值")); controls.addWidget(self.threshold)
        controls.addWidget(QLabel("任务方式")); controls.addWidget(self.run_mode)
        controls.addWidget(self.page_range, 1)
        controls.addWidget(self.start_btn); controls.addWidget(self.pause_btn)
        controls.addWidget(self.stop_btn); controls.addWidget(self.cancel_btn)
        layout.addLayout(controls)
        self.naming_rule_label = QLabel(
            "回单  交易日期_付款人户名_交易金额.pdf     发票  发票号_购买方_价税合计_开票日期.pdf"
        )
        self.naming_rule_label.setWordWrap(True)
        self.naming_rule_label.setStyleSheet("color: #a9c7e8; padding: 2px 0 4px 0;")
        layout.addWidget(self.naming_rule_label)

        self.progress = QProgressBar(); self.progress.setFormat("%p%（%v/%m 页）")
        self.status_label = QLabel("就绪：所有识别与处理均在本机完成")
        layout.addWidget(self.progress); layout.addWidget(self.status_label)
        counts = QHBoxLayout(); self.success_count = QLabel("成功 0"); self.review_count = QLabel("待复核 0"); self.failed_count = QLabel("失败 0")
        for label in (self.success_count, self.review_count, self.failed_count): counts.addWidget(label)
        counts.addStretch(); layout.addLayout(counts)
        self.table = QTableWidget(0, 11); self.table.setHorizontalHeaderLabels(["状态", "原文件", "页/裁切", "文件类型", "日期", "付款人 / 购买方", "收款人", "金额", "置信度", "发票号", "新文件名"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents); self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.review_selected); layout.addWidget(self.table, 1)
        bottom = QHBoxLayout(); review = QPushButton("人工复核选中项")
        self.bill_merge_btn = QPushButton("手动合并页面 / 排序")
        self.bill_merge_btn.setEnabled(False)
        self.history_btn = QPushButton("加载历史任务 / 继续复核")
        export = QPushButton("导出处理记录"); open_dir = QPushButton("打开输出目录")
        review.clicked.connect(self.review_selected)
        self.bill_merge_btn.clicked.connect(self.open_page_merge)
        self.history_btn.clicked.connect(self.load_history)
        export.clicked.connect(self.export_results); open_dir.clicked.connect(self.open_output)
        bottom.addWidget(review); bottom.addWidget(self.bill_merge_btn)
        bottom.addWidget(self.history_btn); bottom.addStretch()
        bottom.addWidget(export); bottom.addWidget(open_dir); layout.addLayout(bottom)

    def _toggle_page_range(self):
        self.page_range.setEnabled(self.run_mode.currentData() == "range")

    def choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择银行回单", "", "支持的文件 (*.pdf *.jpg *.jpeg *.png *.tif *.tiff *.bmp)")
        self._add_inputs(Path(value) for value in files)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输入文件夹")
        if folder: self._add_inputs([Path(folder)])

    def choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_edit.text())
        if folder: self.output_edit.setText(folder)

    def _add_inputs(self, paths):
        existing = {
            os.path.normcase(str(Path(self.input_list.item(i).data(Qt.ItemDataRole.UserRole))))
            for i in range(self.input_list.count())
        }
        self.input_list.blockSignals(True)
        for path in paths:
            normalized = Path(path).resolve()
            key = os.path.normcase(str(normalized))
            if key in existing:
                continue
            kind = "文件夹" if normalized.is_dir() else "文件"
            item = QListWidgetItem(f"{kind}：{normalized}")
            item.setData(Qt.ItemDataRole.UserRole, str(normalized))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.input_list.addItem(item)
            existing.add(key)
        self.input_list.blockSignals(False)
        self._sync_inputs(); self._show_inputs()

    def _checked_inputs(self) -> list[Path]:
        return [
            Path(self.input_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.input_list.count())
            if self.input_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _sync_inputs(self):
        self.inputs = [
            Path(self.input_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.input_list.count())
        ]

    def _set_all_inputs(self, checked: bool):
        self.input_list.blockSignals(True)
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.input_list.count()):
            self.input_list.item(i).setCheckState(state)
        self.input_list.blockSignals(False); self._show_inputs()

    def _remove_unchecked(self):
        for i in range(self.input_list.count() - 1, -1, -1):
            if self.input_list.item(i).checkState() != Qt.CheckState.Checked:
                self.input_list.takeItem(i)
        self._sync_inputs(); self._show_inputs()

    def _clear_inputs(self):
        self.input_list.clear(); self._sync_inputs(); self._show_inputs()

    def _show_inputs(self, *_args):
        total, checked = self.input_list.count(), len(self._checked_inputs())
        if total:
            self.input_label.setText(f"清单共 {total} 项，已勾选 {checked} 项（仅处理已勾选项）")
        else:
            self.input_label.setText("尚未选择文件")

    def _set_input_controls_enabled(self, enabled: bool):
        for widget in (
            self.add_files_btn, self.add_folder_btn, self.input_list, self.check_all_btn,
            self.uncheck_all_btn, self.remove_unchecked_btn, self.clear_inputs_btn,
            self.output_edit, self.out_button, self.mode, self.threshold, self.run_mode,
            self.history_btn,
        ):
            widget.setEnabled(enabled)
        self.page_range.setEnabled(enabled and self.run_mode.currentData() == "range")
        self.bill_merge_btn.setEnabled(
            enabled and len(page_merge_candidates(self.results)) >= 2
        )

    def start(self):
        selected_inputs = list(self._checked_inputs())
        if not selected_inputs:
            QMessageBox.information(self, "请选择文件", "请至少勾选一个回单文件或输入文件夹。"); return
        if self.run_mode.currentData() == "range" and not self.page_range.text().strip():
            QMessageBox.information(self, "请输入页码", "请选择页码范围，例如 13-32。"); return
        self.config.output_dir = Path(self.output_edit.text())
        self.config.model_mode = self.mode.currentData()
        self.config.confidence_threshold = self.threshold.value() / 100
        options = RunOptions(self.run_mode.currentData(), self.page_range.text().strip())
        self.loaded_checkpoint = None
        self.results = []; self.refresh_table(); self.progress.setValue(0)
        self.thread = QThread(self); self.worker = Worker(self.config, selected_inputs, options); self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run); self.worker.progress.connect(self.on_progress)
        self.worker.result_ready.connect(self.on_result); self.worker.finished.connect(self.on_finished); self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.thread.quit); self.worker.failed.connect(self.thread.quit)
        self.start_btn.setEnabled(False)
        self._set_input_controls_enabled(False)
        for button in (self.pause_btn, self.stop_btn, self.cancel_btn): button.setEnabled(True)
        self.thread.start()

    @Slot(int, int, str)
    def on_progress(self, done, total, text):
        self.progress.setMaximum(max(total, 1)); self.progress.setValue(done); self.status_label.setText(text)

    @Slot(object)
    def on_result(self, result):
        self.results.append(result); self.refresh_table()

    @Slot(list)
    def on_finished(self, results):
        self.results = list(results); self.refresh_table()
        state = self.worker.processor.termination_mode
        if state == "stopped":
            self.status_label.setText("已停止；页级进度和已完成结果已保存，可稍后继续")
        elif state == "cancelled":
            self.status_label.setText("任务已取消；已完成结果仍安全保留")
        else:
            self.progress.setValue(self.progress.maximum()); self.status_label.setText("处理完成")
        self._reset_controls()
        candidates = bill_merge_candidates(self.results)
        if state not in {"stopped", "cancelled"} and len(candidates) >= 2:
            self.status_label.setText(
                f"处理完成；发现 {len(candidates)} 个承兑汇票页面，请先选择合并顺序"
            )
            QTimer.singleShot(0, self._open_bill_merge_when_idle)

    @Slot(str)
    def on_failed(self, error):
        QMessageBox.critical(self, "处理失败", error); self.status_label.setText("处理失败：" + error); self._reset_controls()

    def _reset_controls(self):
        self.start_btn.setEnabled(True); self.pause_btn.setText("暂停")
        self._set_input_controls_enabled(True)
        for button in (self.pause_btn, self.stop_btn, self.cancel_btn): button.setEnabled(False)

    def toggle_pause(self):
        if self.worker.processor.pause_event.is_set():
            self.worker.processor.resume(); self.pause_btn.setText("暂停"); self.status_label.setText("正在继续处理……")
        else:
            self.worker.processor.pause(); self.pause_btn.setText("继续"); self.status_label.setText("将在当前安全步骤结束后暂停")

    def stop_and_save(self):
        if self.worker:
            self.worker.processor.stop(); self.status_label.setText("正在完成当前页并保存断点……")

    def cancel_task(self):
        if self.worker:
            self.worker.processor.cancel(); self.status_label.setText("正在安全取消；已完成结果会保留……")

    def refresh_table(self):
        self.table.setRowCount(len(self.results))
        for row, result in enumerate(self.results):
            fields = result.fields
            is_invoice = result.document_type == "invoice"
            party_scores = (fields.invoice_number_confidence, fields.buyer_confidence) if is_invoice else (fields.payer_confidence,)
            confidence = min(fields.date_confidence, fields.amount_confidence, *party_scores)
            values = [
                "已拆分" if result.status == "split" else result.status, result.unit.source_name,
                (
                    f"合并{len(result.merged_page_paths)}页"
                    if result.merged_page_paths
                    else f"{result.unit.page_number}/{result.unit.crop_number}"
                ),
                f"{document_type_label(result.document_type)} {result.document_type_confidence:.0%}",
                fields.date or "", (fields.buyer if is_invoice else fields.payer) or "", fields.payee or "", fields.amount or "",
                f"{confidence:.1%}", fields.invoice_number or "", result.output_path.name if result.output_path else result.suggested_name,
            ]
            for column, value in enumerate(values): self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.success_count.setText(f"成功 {sum(result.status == 'success' for result in self.results)}")
        self.review_count.setText(f"待复核 {sum(result.status == 'review' for result in self.results)}")
        self.failed_count.setText(f"失败 {sum(result.status == 'failed' for result in self.results)}")
        running = bool(self.thread is not None and self.thread.isRunning())
        self.bill_merge_btn.setEnabled(
            not running and len(page_merge_candidates(self.results)) >= 2
        )

    def open_bill_merge(self):
        self.open_page_merge(bills_only=True)

    def _open_bill_merge_when_idle(self):
        if QApplication.activeModalWidget() is None:
            self.open_bill_merge()

    def open_page_merge(self, *, bills_only=False):
        if not self.start_btn.isEnabled():
            return
        candidates = bill_merge_candidates(self.results) if bills_only else page_merge_candidates(self.results)
        if len(candidates) < 2:
            QMessageBox.information(
                self, "没有可合并页面", "至少需要两个可用页面。"
            )
            return
        dialog = BillMergeDialog(candidates, self, bills_only=bills_only)
        if not dialog.exec():
            return
        ordered = dialog.selected_results()
        try:
            merged = merge_bill_results(ordered, self.config.output_dir)
        except Exception as exc:
            QMessageBox.critical(self, "页面合并失败", str(exc))
            return
        stored = self.loaded_checkpoint or CheckpointStore(self.config.output_dir).find_for_member(ordered[0])
        if stored is not None:
            try:
                stored.append_result(merged)
            except OSError as exc:
                QMessageBox.warning(self, "历史任务保存失败", f"合并 PDF 已保存。{exc}")
                stored = None
        self.results.append(merged)
        self.refresh_table(); self.export_results(silent=True)
        self.status_label.setText(
            f"已按勾选顺序合并 {len(ordered)} 页；未勾选页面和原文件均已保留"
        )
        if ReviewDialog(merged, self.config.output_dir, self).exec():
            if stored is not None:
                stored.update_result(merged)
            self.refresh_table(); self.export_results(silent=True)

    def review_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.results):
            QMessageBox.information(self, "请选择结果", "请先选中一条已处理结果。")
            return
        result = self.results[row]
        processor = self.worker.processor if self.worker is not None else None
        stored = self.loaded_checkpoint or (processor.checkpoint_for_result(result) if processor else None)
        if stored is None:
            stored = CheckpointStore(self.config.output_dir).find_for_member(result)
        dialog = ReviewDialog(
            result, self.config.output_dir, self,
            split_page=self.split_review_page if self.start_btn.isEnabled() else None,
            result_lock=processor.result_lock if processor else None,
            persist_review=stored.update_result if stored is not None else None,
        )
        if dialog.exec():
            self.refresh_table()
            try:
                self.export_results(silent=True)
            except OSError as exc:
                QMessageBox.warning(self, "处理记录导出失败", f"复核 PDF 已保存。{exc}")
            if not self.start_btn.isEnabled():
                return
            if stored is not None:
                self.status_label.setText("复核结果已保存，并同步写回历史任务")
            else:
                self.status_label.setText("复核结果已保存；未找到对应历史任务记录")

    def split_review_page(self, result: ProcessResult, preview: Path, parent: QDialog) -> bool:
        pixmap = QPixmap(str(preview))
        if pixmap.isNull():
            QMessageBox.warning(parent, "预览不可用", "无法读取该页图像")
            return False
        dialog = ManualSplitDialog(pixmap, result.unit.suggested_boxes, parent)
        if not dialog.exec():
            return False
        stored = self.loaded_checkpoint or CheckpointStore(self.config.output_dir).find_for_member(result)
        siblings = self.results if stored is None else stored.results
        last_number = max(
            (member.unit.crop_number for member in siblings
             if member.unit.source_path == result.unit.source_path
             and member.unit.page_number == result.unit.page_number),
            default=result.unit.crop_number,
        )
        original = replace(result, status="split", needs_split=False,
                           reasons=["已手动拆分，原页已保留", *result.reasons])
        created = []
        try:
            children = create_manual_crops(
                result, preview, dialog.view.regions, self.config, last_number + 1, created,
            )
            if stored is not None:
                stored.commit_split(original, children)
        except Exception as exc:
            for path in reversed(created):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            QMessageBox.critical(parent, "拆分失败", str(exc))
            return False
        index = next(i for i, item in enumerate(self.results) if item is result)
        self.results[index:index + 1] = [original, *children]
        self.refresh_table()
        self.table.selectRow(index + 1)
        self.export_results(silent=True)
        self.status_label.setText(f"已生成 {len(children)} 个待复核裁片")
        return True

    def load_history(self):
        """Load results for review without re-running OCR or touching source files."""
        output_dir = Path(self.output_edit.text().strip())
        checkpoints = [item for item in CheckpointStore(output_dir).history() if item.data.get("results")]
        if not checkpoints:
            folder = QFileDialog.getExistingDirectory(
                self, "选择以前的处理结果目录", str(output_dir)
            )
            if not folder:
                return
            output_dir = Path(folder)
            checkpoints = [item for item in CheckpointStore(output_dir).history() if item.data.get("results")]
        if not checkpoints:
            QMessageBox.information(
                self, "没有历史任务", "所选目录中未找到可加载的 .jobs 历史任务记录。"
            )
            return

        status_labels = {
            "completed": "已完成", "stopped": "已停止", "cancelled": "已取消",
            "running": "处理中", "failed": "失败",
        }
        labels: list[str] = []
        for index, checkpoint in enumerate(checkpoints, 1):
            data = checkpoint.data
            source_name = Path(data.get("source", {}).get("path", "未知文件")).name
            requested = {int(page) for page in data.get("requested_pages", [])}
            completed = len(requested & checkpoint.completed_pages) if requested else len(checkpoint.completed_pages)
            total = len(requested) or int(data.get("total_pages", 0))
            review_count = sum(item.get("status") == "review" for item in data.get("results", []))
            status = status_labels.get(data.get("status", ""), data.get("status", "未知"))
            updated = str(data.get("updated_at", data.get("created_at", ""))).replace("T", " ")
            labels.append(
                f"{index}. {source_name}｜{status}｜{completed}/{total} 页｜待复核 {review_count}｜{updated}"
            )
        selected, accepted = QInputDialog.getItem(
            self, "加载历史任务", "选择要恢复的任务（不会重新执行 OCR）：", labels, 0, False
        )
        if not accepted or not selected:
            return
        self.output_edit.setText(str(output_dir))
        self.config.output_dir = output_dir
        self._display_checkpoint(checkpoints[labels.index(selected)])

    def _display_checkpoint(self, checkpoint: JobCheckpoint):
        try:
            results = checkpoint.results
        except (KeyError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "历史任务损坏", f"无法读取该任务记录：{exc}")
            return
        self.loaded_checkpoint = checkpoint
        self.results = results
        requested = {int(page) for page in checkpoint.data.get("requested_pages", [])}
        completed = len(requested & checkpoint.completed_pages) if requested else len(checkpoint.completed_pages)
        total = len(requested) or int(checkpoint.data.get("total_pages", 0))
        self.progress.setMaximum(max(total, 1)); self.progress.setValue(min(completed, max(total, 1)))
        self.refresh_table()
        source_name = Path(checkpoint.data.get("source", {}).get("path", "未知文件")).name
        self.status_label.setText(
            f"已加载历史任务：{source_name}，共 {len(results)} 项；双击待复核项即可继续（未重新执行 OCR）"
        )

    def export_results(self, silent=False):
        if not self.results: return
        target = self.config.output_dir / "processing_results.xlsx"
        processor = self.worker.processor if self.worker is not None else None
        with processor.result_lock if processor else nullcontext():
            results = processor.committed_results if processor and not self.start_btn.isEnabled() else self.results
            export_xlsx(results, target)
        if not silent: QMessageBox.information(self, "导出完成", f"处理记录已保存到：\n{target}")

    def open_output(self):
        self.config.output_dir.mkdir(parents=True, exist_ok=True); os.startfile(self.config.output_dir)


def run_gui():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    window = MainWindow(); window.show()
    return app.exec()
