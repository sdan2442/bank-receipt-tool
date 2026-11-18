from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import fitz
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from bank_receipt_tool.bill_merge import merge_bill_results
from bank_receipt_tool.checkpoint import result_from_dict, result_to_dict
from bank_receipt_tool.domain import ExtractedFields, ProcessResult, ReceiptUnit
from bank_receipt_tool.gui import BillMergeDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _bill(path: Path, page_number: int, color: tuple[int, int, int]) -> ProcessResult:
    image = np.full((180, 260, 3), color, np.uint8)
    assert cv2.imwrite(str(path), image)
    return ProcessResult(
        ReceiptUnit(path.with_suffix(".pdf"), "汇票原件.pdf", page_number, 1, path),
        ExtractedFields(date="2026-09-10", payer="示例公司", amount="100.00"),
        [],
        f"第{page_number}页.pdf",
        path.with_suffix(".pdf"),
        "review",
        ["承兑汇票需复核"],
        document_type="acceptance_bill",
        document_type_confidence=.95,
    )


def test_bill_merge_selection_order_is_the_checkbox_order(tmp_path: Path):
    _app()
    results = [
        _bill(tmp_path / "one.png", 1, (0, 0, 255)),
        _bill(tmp_path / "two.png", 2, (0, 255, 0)),
        _bill(tmp_path / "three.png", 3, (255, 0, 0)),
    ]
    dialog = BillMergeDialog(results)

    dialog.page_list.item(2).setCheckState(Qt.CheckState.Checked)
    dialog.page_list.item(0).setCheckState(Qt.CheckState.Checked)

    assert dialog.selected_results() == [results[2], results[0]]
    assert "顺序 1" in dialog.page_list.item(2).text()
    assert "顺序 2" in dialog.page_list.item(0).text()
    dialog.close()


def test_merged_bill_pdf_and_checkpoint_keep_manual_page_order(tmp_path: Path):
    first = _bill(tmp_path / "red.png", 1, (0, 0, 255))
    second = _bill(tmp_path / "blue.png", 2, (255, 0, 0))

    merged = merge_bill_results([second, first], tmp_path / "output")

    with fitz.open(merged.output_path) as document:
        assert document.page_count == 2
        first_pixel = document[0].get_pixmap().samples[:3]
        second_pixel = document[1].get_pixmap().samples[:3]
    assert first_pixel[2] > 200 and first_pixel[0] < 30
    assert second_pixel[0] > 200 and second_pixel[2] < 30
    assert merged.merged_page_paths == [second.unit.image_path, first.unit.image_path]
    assert "第2页" in merged.merged_page_labels[0]
    restored = result_from_dict(result_to_dict(merged))
    assert restored.merged_page_paths == merged.merged_page_paths
    assert restored.merged_page_labels == merged.merged_page_labels
