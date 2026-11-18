import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
from openpyxl import load_workbook
from PySide6.QtWidgets import QApplication

from bank_receipt_tool.checkpoint import result_from_dict, result_to_dict
from bank_receipt_tool.config import AppConfig
from bank_receipt_tool.document_classifier import classify_document
from bank_receipt_tool.domain import OCRLine
from bank_receipt_tool.gui import ReviewDialog
from bank_receipt_tool.invoice import extract_invoice
from bank_receipt_tool.naming import suggested_filename
from bank_receipt_tool.pipeline import ReceiptProcessor, RunOptions


def line(text, x, y, w=100, h=18):
    return OCRLine(text, [[x, y], [x+w, y], [x+w, y+h], [x, y+h]], .96)


def invoice_lines():
    return [
        line("电子发票（增值税专用发票）", 220, 30, 300),
        line("发票代码：000000000001", 680, 35, 280),
        line("发票号码", 650, 65, 85),
        line("25000000000000000001", 745, 64, 200),
        line("开票日期：2025年01月07日", 650, 92, 280),
        line("购买方信息", 55, 135, 18, 92),
        line("销售方信息", 510, 135, 18, 92),
        line("名称", 85, 149, 40),
        line("示例采购有限公司", 134, 149, 194),
        line("名称：示例销售有限公司", 538, 148, 250),
        line("统一社会信用代码/纳税人识别号：000000000000000001", 85, 193, 400),
        line("项目名称", 85, 250),
        line("金额", 630, 250, 42), line("税额", 842, 250, 42),
        line("100.00", 630, 280, 80), line("13.00", 842, 280, 80),
        line("合计", 110, 418), line("¥100.00", 630, 418), line("¥13.00", 842, 418),
        line("价税合计（大写）", 80, 448, 145),
        line("壹佰壹拾叁圆整", 250, 449, 175),
        line("（小写）", 685, 448, 55),
        line("￥113.00", 755, 446, 115),
    ]


def test_invoice_uses_buyer_column_number_and_tax_inclusive_total():
    lines = invoice_lines()
    result = classify_document(lines, AppConfig.default().load_rules())
    assert result.document_type == "invoice"
    fields = extract_invoice(lines, .72)
    assert fields.review_reasons == []
    assert fields.payer is None and fields.payee is None
    assert fields.invoice_number == "25000000000000000001"
    assert fields.buyer == "示例采购有限公司"
    assert fields.amount == "113.00"
    assert fields.date == "2025-01-07"
    assert suggested_filename(fields, "invoice") == "25000000000000000001_示例采购有限公司_113.00_2025-01-07.pdf"


def test_missing_total_and_buyer_stay_blank():
    lines = [item for item in invoice_lines() if item.text not in {"示例采购有限公司", "￥113.00"}]
    fields = extract_invoice(lines, .72)
    assert fields.buyer is None and fields.amount is None
    assert fields.buyer_candidates == fields.amount_candidates == []
    assert "购买方待确认_金额待确认" in suggested_filename(fields, "invoice")


def test_invoice_accepts_leading_zero_and_negative_total_with_review_for_mismatch():
    fields = extract_invoice([
        line("发票号：00000008", 600, 35, 200),
        line("开票日期：2025-01-07", 600, 65, 200),
        line("购买方名称：示例采购有限公司", 80, 100, 300),
        line("价税合计（大写）贰佰元整", 80, 350, 280),
        line("价税合计（小写）：￥-113.5", 620, 350, 300),
    ], .72)
    assert fields.invoice_number == "00000008"
    assert fields.amount == "-113.50"
    assert "价税合计大小写金额不一致" in fields.review_reasons


def test_ambiguous_invoice_number_does_not_become_filename():
    fields = extract_invoice([*invoice_lines(), line("发票号码：00000008", 710, 122, 200)], .72)
    assert fields.invoice_number is None
    assert len(fields.invoice_number_candidates) == 2
    assert "找到多个发票号候选" in fields.review_reasons
    assert suggested_filename(fields, "invoice").startswith("发票号待确认_")


def test_total_row_can_be_recognized_as_one_box():
    fields = extract_invoice([
        line("价税合计（大写）壹佰壹拾叁圆整（小写）￥113.00", 30, 420, 800),
    ], .72)
    assert fields.amount == "113.00"
    assert not any("价税合计" in reason for reason in fields.review_reasons)


def test_receipt_with_an_invoice_reference_keeps_its_document_type():
    lines = [
        line("网上银行电子回单", 220, 30, 200), line("付款人：示例公司", 220, 72),
        line("发票号码：00000008", 220, 120),
    ]
    assert classify_document(lines, AppConfig.default().load_rules()).document_type == "receipt"


def test_mixed_invoice_and_receipt_history_export_and_review(tmp_path: Path, monkeypatch):
    class OCR:
        def __init__(self, *_args):
            pass

        def recognize(self, path):
            if path.name.startswith("p0001"):
                return invoice_lines()
            return [
                line("电子回单", 150, 30),
                line("交易日期 2025-01-09", 220, 72, 200),
                line("付款人名称：示例汇款公司", 220, 120, 300),
                line("交易金额 215.00", 220, 160, 200),
            ]

    monkeypatch.setattr("bank_receipt_tool.pipeline.LocalOCREngine", OCR)
    source = tmp_path / "mixed.pdf"
    with fitz.open() as document:
        document.new_page()
        document.new_page()
        document.save(source)
    config = AppConfig.default()
    config.output_dir = tmp_path / "results"
    invoice, receipt = ReceiptProcessor(config).run([source], RunOptions("restart"))
    assert invoice.document_type == "invoice" and receipt.document_type == "receipt"
    assert invoice.status == receipt.status == "review"
    assert receipt.suggested_name == "2025-01-09_示例汇款公司_215.00.pdf"
    restored = result_from_dict(result_to_dict(invoice))
    assert restored.fields.invoice_number_candidates[0].value == "25000000000000000001"
    assert json.loads(invoice.output_path.with_suffix(".json").read_text(encoding="utf-8"))["fields"]["buyer"] == "示例采购有限公司"
    with (config.output_dir / "processing_results.xlsx").open("rb") as stream:
        workbook = load_workbook(stream, read_only=True)
        rows = list(workbook.active.values)
        workbook.close()
    assert rows[1][rows[0].index("发票号码")] == "25000000000000000001"

    app = QApplication.instance() or QApplication([])
    dialog = ReviewDialog(restored, config.output_dir)
    dialog.fields_edits["buyer"].setCurrentText("示例复核有限公司")
    dialog.fields_edits["amount"].setCurrentText("￥113")
    assert dialog.filename.text() == "25000000000000000001_示例复核有限公司_113.00_2025-01-07.pdf"
    dialog.save()
    assert restored.status == "success"
    assert restored.output_path.name == dialog.filename.text()
    assert invoice.output_path.is_file() and source.is_file()
    dialog.close()
