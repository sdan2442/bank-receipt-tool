import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
from openpyxl import load_workbook
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton

from bank_receipt_tool.checkpoint import CheckpointStore
from bank_receipt_tool.config import AppConfig
from bank_receipt_tool.domain import OCRLine
from bank_receipt_tool.gui import MainWindow, ReviewDialog
from bank_receipt_tool.pipeline import ReceiptProcessor, RunOptions


def test_completed_page_can_be_reviewed_during_next_pages_ocr(tmp_path, monkeypatch):
    def line(text, y):
        return OCRLine(text, [[20, y], [350, y], [350, y + 22], [20, y + 22]], .98)

    second_page_started, resume_ocr = Event(), Event()

    class OCR:
        def __init__(self, *_args):
            pass

        def recognize(self, path):
            if path.name.startswith("p0002"):
                second_page_started.set()
                assert resume_ocr.wait(15), "second page not released"
            return [
                line("电子回单", 10),
                line("交易日期 2025-01-09", 50),
                line("付款人名称：示例汇款公司", 90),
                line("交易金额 215.00", 130),
            ]

    monkeypatch.setattr("bank_receipt_tool.pipeline.LocalOCREngine", OCR)
    source = tmp_path / "sample.pdf"
    with fitz.open() as pdf:
        pdf.new_page(width=300, height=400)
        pdf.new_page(width=300, height=400)
        pdf.save(source)
    config = AppConfig.default()
    config.output_dir = tmp_path / "out"
    published = []
    processor = ReceiptProcessor(config, result_callback=published.append)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.config = config
    window.worker = SimpleNamespace(processor=processor)
    window.start_btn.setEnabled(False)

    def confirm(dialog):
        assert not any(button.text() == "手动拆分" for button in dialog.findChildren(QPushButton))
        dialog.fields_edits["payer"].setCurrentText("示例复核公司")
        dialog.save()
        return QDialog.result(dialog)

    def unexpected_error(_parent, title, message):
        raise AssertionError((title, message))

    monkeypatch.setattr(ReviewDialog, "exec", confirm)
    monkeypatch.setattr(QMessageBox, "critical", unexpected_error)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(processor.run, [source], RunOptions("restart"))
        try:
            assert second_page_started.wait(15)
            assert len(published) == 1
            assert processor.checkpoint_for_result(published[0]).completed_pages == {1}
            window.on_result(published[0])
            window.table.selectRow(0)
            window.review_selected()
            assert not future.done()
            assert published[0].status == "success"
            assert published[0].output_path.name == "2025-01-09_示例复核公司_215.00.pdf"
            assert CheckpointStore(config.output_dir).history()[0].results[0].fields.payer == "示例复核公司"
        finally:
            resume_ocr.set()
        final = future.result(timeout=15)
    assert [result.status for result in final] == ["success", "review"]
    history = CheckpointStore(config.output_dir).history()[0]
    assert history.completed_pages == {1, 2}
    assert history.results[0].status == "success"
    workbook = load_workbook(config.output_dir / "processing_results.xlsx", read_only=True)
    try:
        assert workbook.active.cell(2, 8).value == "示例复核公司"
    finally:
        workbook.close()
    window.close()
