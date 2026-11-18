import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import fitz
import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from bank_receipt_tool.bill_merge import merge_bill_results, page_merge_candidates
from bank_receipt_tool.checkpoint import CheckpointStore, source_fingerprint
from bank_receipt_tool.config import AppConfig, desktop_dir
from bank_receipt_tool.domain import ExtractedFields, OCRLine, ProcessResult, ReceiptUnit
from bank_receipt_tool.exporter import image_to_pdf_atomic
from bank_receipt_tool.gui import BillMergeDialog, MainWindow
from bank_receipt_tool.manual_split import create_manual_crops, validate_regions
from bank_receipt_tool.split_dialog import ManualSplitDialog


def sample(path: Path, color: tuple[int, int, int], page=1):
    assert cv2.imwrite(str(path), np.full((200, 240, 3), color, np.uint8))
    output = path.with_suffix(".pdf")
    image_to_pdf_atomic(path, output)
    return ProcessResult(
        ReceiptUnit(path, "示例.png", page, 1, path),
        ExtractedFields(), [], output.name, output, "review",
    )


def test_default_controls_use_desktop_and_double_mobile():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.mode.currentData() == "mobile"
    assert Path(window.output_edit.text()).parent == desktop_dir()
    window.close()


def test_manual_split_mouse_coordinates_and_history(tmp_path):
    app = QApplication.instance() or QApplication([])
    result = sample(tmp_path / "sample.png", (0, 0, 255))
    result.ocr_lines = [OCRLine("交易金额 150.00", [[30, 120], [140, 120], [140, 140], [30, 140]], .95)]
    dialog = ManualSplitDialog(QPixmap(str(result.unit.image_path)), [])
    dialog.show()
    app.processEvents()
    for x, y, right, bottom in [(20, 12, 220, 88), (20, 110, 220, 190)]:
        start = dialog.view.mapFromScene(QPointF(x, y))
        end = dialog.view.mapFromScene(QPointF(right, bottom))
        QTest.mousePress(dialog.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(dialog.view.viewport(), end)
        QTest.mouseRelease(dialog.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    dialog.confirm()
    assert dialog.result()
    assert len(dialog.view.regions) == 2

    config = AppConfig.default()
    config.output_dir = tmp_path / "out"
    store = CheckpointStore(config.output_dir)
    job = store.create(source_fingerprint(result.unit.source_path), {}, 1, [1])
    job.complete_page(1, [result])
    children = create_manual_crops(result, result.unit.image_path, dialog.view.regions, config, 2, [])
    job.commit_split(replace(result, status="split"), children)
    restored = store.history()[0].results
    assert [item.status for item in restored] == ["split", "review", "review"]
    assert [item.unit.crop_number for item in restored] == [1, 2, 3]
    assert children[0].fields.amount is None
    assert children[1].fields.amount == "150.00"
    assert children[1].ocr_lines[0].box[0][1] < 20
    for child in children:
        with fitz.open(child.output_path) as document:
            assert document.page_count == 1
            assert document[0].rect.height < 100
    assert result.output_path.is_file()


def test_selected_crop_can_resize_move_and_share_overlapping_content(tmp_path):
    app = QApplication.instance() or QApplication([])
    result = sample(tmp_path / "overlap.png", (0, 0, 255))
    image = cv2.imread(str(result.unit.image_path))
    image[74:87, 81:94] = (0, 255, 0)
    assert cv2.imwrite(str(result.unit.image_path), image)
    result.ocr_lines = [OCRLine("交易金额 150.00", [[82, 75], [92, 75], [92, 85], [82, 85]], .95)]
    dialog = ManualSplitDialog(QPixmap(str(result.unit.image_path)), [])
    initial = [(20, 20, 80, 80), (60, 60, 110, 95)]
    dialog.set_regions(initial, 0)
    dialog.show()
    app.processEvents()

    def drag(x, y, right, bottom):
        start = dialog.view.mapFromScene(QPointF(x, y))
        end = dialog.view.mapFromScene(QPointF(right, bottom))
        QTest.mousePress(dialog.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(dialog.view.viewport(), end)
        QTest.mouseRelease(dialog.view.viewport(), Qt.MouseButton.LeftButton, pos=end)

    drag(100, 100, 129, 120)
    resized = dialog.view.regions[0]
    assert resized[0:2] == (20, 20)
    assert resized[2] >= 106 and resized[3] >= 97
    assert dialog.view.regions[1] == initial[1]
    drag(46, 42, 53, 51)
    moved = dialog.view.regions[0]
    assert moved[0] > resized[0] and moved[1] > resized[1]
    assert moved[2:] == resized[2:]
    dialog.undo()
    assert dialog.view.regions == [resized, initial[1]]
    dialog.add_button.click()
    drag(80, 80, 105, 110)
    assert len(dialog.view.regions) == 3
    dialog.remove_button.click()
    assert dialog.view.regions == [resized, initial[1]]
    validate_regions(dialog.view.regions, dialog.shape)

    config = AppConfig.default()
    config.output_dir = tmp_path / "out"
    children = create_manual_crops(result, result.unit.image_path, dialog.view.regions, config, 2, [])
    assert children[0].output_path != children[1].output_path
    for child, (x, y, _, _) in zip(children, dialog.view.regions):
        assert child.fields.amount == "150.00"
        with fitz.open(child.output_path) as pdf:
            assert pdf[0].get_pixmap().pixel(87 - x, 80 - y) == (0, 255, 0)
    dialog.close()


def test_unknown_page_merge_keeps_checked_order_with_missing_preview(tmp_path):
    app = QApplication.instance() or QApplication([])
    first = sample(tmp_path / "first.png", (0, 0, 255))
    second = sample(tmp_path / "second.png", (255, 0, 0), 2)
    second.unit.image_path = tmp_path / "missing.png"
    dialog = BillMergeDialog(page_merge_candidates([first, second]), bills_only=False)
    dialog.page_list.item(1).setCheckState(Qt.CheckState.Checked)
    dialog.page_list.item(0).setCheckState(Qt.CheckState.Checked)
    merged = merge_bill_results(dialog.selected_results(), tmp_path / "out")
    assert merged.document_type == "manual_merged"
    assert merged.status == "review"
    assert "第2页" in merged.merged_page_labels[0]
    with fitz.open(merged.output_path) as document:
        assert document.page_count == 2
        assert document[0].get_pixmap().samples[2] > 200
        assert document[1].get_pixmap().samples[0] > 200
    assert first.output_path.is_file() and second.output_path.is_file()
    dialog.close()
