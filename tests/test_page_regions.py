from pathlib import Path

import cv2
import numpy as np
import pytest

from bank_receipt_tool.config import AppConfig
from bank_receipt_tool.domain import OCRLine
from bank_receipt_tool.extractor import FieldExtractor
from bank_receipt_tool.image_processing import detect_receipts_detailed
from bank_receipt_tool.page_regions import repeated_receipt_regions
from bank_receipt_tool.pipeline import ReceiptProcessor, RunOptions


def line(text, x, y, width=150, height=20):
    return OCRLine(text, [[x, y], [x+width, y], [x+width, y+height], [x, y+height]], .97)


def test_three_character_party_labels_follow_their_columns():
    lines = [
        line("收", 500, 110, 20), line("户名", 548, 112, 38),
        line("杭州示例实业有限公司", 598, 109),
        line("付", 55, 114, 20), line("户名", 105, 114, 38),
        line("上海示例技术有限公司", 155, 111),
        line("款", 54, 146, 20), line("款", 502, 143, 20),
        line("账号", 105, 175, 38), line("人", 501, 175, 20),
        line("账号", 548, 175, 38), line("人", 56, 179, 20),
    ]
    extractor = FieldExtractor(AppConfig.default().load_rules())
    fields = extractor.extract(lines)
    assert fields.payer == "上海示例技术有限公司"
    assert fields.payee == "杭州示例实业有限公司"
    fields = extractor.extract([item for item in lines if item.text != "上海示例技术有限公司"])
    assert fields.payer is None
    assert fields.payee == "杭州示例实业有限公司"


def test_aligned_frames_outvote_a_missing_whitespace_cut(monkeypatch):
    image = np.full((1200, 800, 3), 255, np.uint8)
    for top in (40, 450, 860):
        cv2.rectangle(image, (46, top), (754, top + 300), (0, 0, 0), 3)
    monkeypatch.setattr("bank_receipt_tool.image_processing._whitespace_boxes",
                        lambda _: (.08, [(0, 0, 800, 400), (0, 400, 800, 800)]))
    assert len(detect_receipts_detailed(image).crops) == 3
    monkeypatch.setattr("bank_receipt_tool.image_processing._whitespace_boxes",
                        lambda _: (.08, [(0, 0, 800, 200), (0, 200, 800, 1000)]))
    assert detect_receipts_detailed(image).ambiguous


@pytest.mark.parametrize("incomplete", [False, True])
def test_stacked_credit_and_debit_notices_reuse_full_page_ocr(tmp_path, monkeypatch, incomplete):
    source = tmp_path / "sample.png"
    cv2.imwrite(str(source), np.full((1200, 800, 3), 255, np.uint8))
    lines = []
    for index, title in enumerate(("贷记通知", "贷记通知", "借记通知")):
        y = 40 + 400 * index
        lines += [
            line(title, 330, y), line("Credit Advice" if index < 2 else "Debit Advice", 500, y + 3),
            line("交易日期 2026-09-08", 50, y + 80),
            line("客户名称", 50, y + 140), line("本方示例公司", 250, y + 140),
            line("交易对方名字", 500, y + 200), line("对方示例公司", 500, y + 235),
        ]
        if not incomplete or index != 1:
            lines.append(line(f"交易金额 {100 + index}.00", 50, y + 270))
    calls = []

    class OCR:
        def __init__(self, *_args):
            pass

        def recognize(self, path):
            calls.append(path.name)
            return [] if "_text" in path.name else lines

    monkeypatch.setattr("bank_receipt_tool.pipeline.LocalOCREngine", OCR)
    config = AppConfig.default()
    config.output_dir = tmp_path / "out"
    results = ReceiptProcessor(config).run([source], RunOptions("restart"))
    if incomplete:
        assert len(calls) == 2
        assert len(results) == 1
        assert results[0].needs_split
        assert results[0].fields.payer is None
        assert results[0].output_path.name == "多回单待拆分_第1页.pdf"
        assert len(results[0].unit.suggested_boxes) == 3
    else:
        assert len(calls) == 1
        assert [result.fields.amount for result in results] == ["100.00", "101.00", "102.00"]
        assert [result.fields.payer for result in results] == ["对方示例公司", "对方示例公司", "本方示例公司"]
        assert all(result.status == "review" for result in results)


def test_bilingual_labels_on_one_row_do_not_split_a_single_receipt():
    assert repeated_receipt_regions([
        line("贷记通知", 30, 40), line("Credit Advice", 210, 48),
        line("交易日期", 100, 170),
    ], (1200, 800)) == []
