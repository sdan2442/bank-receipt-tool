from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from bank_receipt_tool.checkpoint import CheckpointStore, source_fingerprint
from bank_receipt_tool.domain import ExtractedFields, OCRLine, ProcessResult, ReceiptUnit
from bank_receipt_tool.gui import MainWindow, Worker
from bank_receipt_tool.pipeline import RunOptions


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_input_list_deduplicates_checks_and_removes(tmp_path: Path):
    _app()
    receipt = tmp_path / "receipt.pdf"
    receipt.write_bytes(b"pdf-placeholder")
    folder = tmp_path / "batch"
    folder.mkdir()

    window = MainWindow()
    window._add_inputs([receipt, folder, receipt])

    assert window.input_list.count() == 2
    assert window._checked_inputs() == [receipt.resolve(), folder.resolve()]
    assert "已勾选 2 项" in window.input_label.text()

    window.input_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert window._checked_inputs() == [folder.resolve()]
    window._remove_unchecked()
    assert window.input_list.count() == 1
    assert window.inputs == [folder.resolve()]
    window.close()


def test_task_inputs_are_snapshotted_and_controls_lock(tmp_path: Path):
    _app()
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    window = MainWindow()
    window._add_inputs([first, second])
    window.config.output_dir = tmp_path / "output"
    selected = list(window._checked_inputs())
    worker = Worker(window.config, selected, RunOptions("restart", ""))

    window.input_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    selected.clear()
    assert worker.inputs == [first.resolve(), second.resolve()]

    window._set_input_controls_enabled(False)
    assert not window.input_list.isEnabled()
    assert not window.add_files_btn.isEnabled()
    assert not window.output_edit.isEnabled()
    window._set_input_controls_enabled(True)
    assert window.input_list.isEnabled()
    assert window.add_files_btn.isEnabled()
    window.close()


def test_completed_history_loads_without_starting_worker(monkeypatch, tmp_path: Path):
    _app()
    source = tmp_path / "finished.pdf"
    source.write_bytes(b"finished")
    output = tmp_path / "output"
    checkpoint = CheckpointStore(output).create(
        source_fingerprint(source), {"app_version": "0.3.2"}, 1, [1]
    )
    unit = ReceiptUnit(source, source.name, 1, 1, output / ".previews" / "p1.png")
    result = ProcessResult(
        unit,
        ExtractedFields(date="2026-09-08", payer="示例公司", amount="12500.00"),
        [OCRLine("交易时间 2026-09-08 10:00:00", [[0, 0], [1, 0], [1, 1], [0, 1]], .99)],
        "2026-09-08_示例公司_12500.00.pdf",
        output / "review" / "待复核.pdf",
        "review",
    )
    checkpoint.complete_page(1, [result])
    checkpoint.set_status("completed")

    # The returned label must match the dynamically built choice. Capture it
    # while still guaranteeing no OCR worker is created.
    monkeypatch.setattr(
        "bank_receipt_tool.gui.QInputDialog.getItem",
        lambda _parent, _title, _prompt, choices, _current, _editable: (choices[0], True),
    )
    window = MainWindow()
    window.output_edit.setText(str(output))
    window.load_history()

    assert window.worker is None
    assert window.loaded_checkpoint is not None
    assert window.table.rowCount() == 1
    assert window.progress.value() == 1
    assert "未重新执行 OCR" in window.status_label.text()
    window.close()
