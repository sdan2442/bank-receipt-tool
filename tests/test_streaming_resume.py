from __future__ import annotations

import json
from pathlib import Path

import cv2
import fitz
import numpy as np

from bank_receipt_tool.checkpoint import CheckpointStore, parse_page_range, source_fingerprint
from bank_receipt_tool.config import AppConfig
from bank_receipt_tool.domain import ExtractedFields, OCRLine, ProcessResult, ReceiptUnit
from bank_receipt_tool.extractor import FieldExtractor
from bank_receipt_tool.image_processing import detect_receipts_detailed, normalize_rect, rotate_rects
from bank_receipt_tool.pipeline import ReceiptProcessor, RunOptions, split_fallback_reason


def _line(text: str, y: int) -> OCRLine:
    return OCRLine(text, [[10, y], [600, y], [600, y + 30], [10, y + 30]], .98)


class FakeOCR:
    stop_processor = None
    calls: list[str] = []

    def __init__(self, *_args, **_kwargs):
        pass

    def recognize(self, path: Path):
        self.calls.append(path.name)
        if self.stop_processor is not None and len(self.calls) == 1:
            self.stop_processor.stop()
        return [
            _line("交易日期 2026-09-08", 10),
            _line("付款人名称：上海示例贸易有限公司", 50),
            _line("交易金额 ￥12,500.00", 90),
        ]


def _pdf(path: Path, pages: int = 3) -> None:
    document = fitz.open()
    for index in range(pages):
        page = document.new_page(width=600, height=400)
        page.insert_text((40, 80), f"RECEIPT {index + 1}", fontsize=22)
    document.save(path)
    document.close()


def test_parse_page_range():
    assert parse_page_range("1,3,5-7", 8) == [1, 3, 5, 6, 7]
    assert parse_page_range("", 3) == [1, 2, 3]
    try:
        parse_page_range("4-2", 5)
    except ValueError:
        pass
    else:
        raise AssertionError("reverse range must fail")


def test_multiple_receipts_are_split_by_independent_borders():
    image = np.full((1000, 800, 3), 255, np.uint8)
    cv2.rectangle(image, (60, 60), (740, 440), (0, 0, 0), 8)
    cv2.rectangle(image, (60, 560), (740, 940), (0, 0, 0), 8)
    detection = detect_receipts_detailed(image)
    assert not detection.ambiguous
    assert len(detection.crops) == 2
    assert len(detection.suggested_boxes) == 2


def test_stacked_receipts_are_split_by_narrow_dashed_separators():
    image = np.full((1200, 800, 3), 255, np.uint8)
    for start in (0, 400, 800):
        cv2.putText(image, "CREDIT ADVICE", (80, start + 100), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 3)
        for y in (start + 160, start + 220, start + 280):
            cv2.putText(image, "FIELD 20250410 12345.67", (80, y), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 0, 0), 2)
    for y in (400, 800):
        for x in range(35, 765, 22):
            cv2.line(image, (x, y), (min(x + 13, 765), y), (0, 0, 0), 3)

    detection = detect_receipts_detailed(image)

    assert not detection.ambiguous
    assert len(detection.crops) == 3
    assert [crop.shape[0] for crop, _ in detection.crops] == [400, 400, 400]


def test_single_receipt_with_dense_table_rules_is_not_split():
    image = np.full((1200, 800, 3), 255, np.uint8)
    cv2.rectangle(image, (40, 40), (760, 1160), (0, 0, 0), 5)
    cv2.putText(image, "ONE RECEIPT", (100, 110), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 3)
    for y in range(180, 1080, 70):
        cv2.line(image, (55, y), (745, y), (0, 0, 0), 3)
        cv2.putText(image, "FIELD VALUE", (80, y - 15), cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 0, 0), 2)

    detection = detect_receipts_detailed(image)

    assert len(detection.crops) == 1
    assert not detection.ambiguous


def test_single_receipt_false_separator_candidates_fall_back_to_whole_page(monkeypatch, tmp_path: Path):
    source = tmp_path / "one_receipt_with_rules.png"
    image = np.full((1200, 800, 3), 255, np.uint8)
    cv2.putText(image, "ONE RECEIPT", (100, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 3)
    cv2.line(image, (30, 300), (770, 300), (0, 0, 0), 4)
    cv2.line(image, (30, 900), (770, 900), (0, 0, 0), 4)
    for y in (180, 520, 1080):
        cv2.putText(image, "CONTENT", (100, y), cv2.FONT_HERSHEY_SIMPLEX, .8, (0, 0, 0), 2)
    cv2.imwrite(str(source), image)

    class SafetyOCR:
        calls: list[str] = []

        def __init__(self, *_args, **_kwargs):
            pass

        def recognize(self, path: Path):
            self.calls.append(path.name)
            complete = [
                _line("交易日期 2026-09-08", 10),
                _line("付款人名称：上海示例贸易有限公司", 50),
                _line("交易金额 ￥12,500.00", 90),
            ]
            return complete if path.stem.endswith("_full") else [complete[0], complete[2]]

    monkeypatch.setattr("bank_receipt_tool.pipeline.LocalOCREngine", SafetyOCR)
    config = AppConfig.default(); config.output_dir = tmp_path / "output"
    results = ReceiptProcessor(config).run([source], RunOptions("restart"))

    assert len(results) == 1
    assert results[0].unit.image_path.stem.endswith("12500.00") or results[0].unit.image_path.stem.endswith("_full")
    assert not results[0].needs_split
    assert any("回退为整页" in reason for reason in results[0].reasons)
    assert SafetyOCR.calls == [
        "p0001_c01.png", "p0001_c02.png", "p0001_c03.png", "p0001_full.png"
    ]


def test_rotated_crop_boxes_are_normalized_and_clamped():
    assert normalize_rect((1338, 107, 960, 1690), (1870, 2420)) == (1338, 107, 960, 1690)
    assert normalize_rect((100, 200, -80, -150), (500, 600)) == (20, 50, 80, 150)
    rotated = rotate_rects([(1338, 107, 960, 1690)], (1870, 2420), 90)
    assert rotated == [(73, 1338, 1690, 960)]
    x, y, width, height = rotated[0]
    assert 0 <= x < x + width <= 1870
    assert 0 <= y < y + height <= 2420


def test_bill_review_hides_unreliable_crop_suggestions(tmp_path: Path):
    class BillOCR:
        last_rotation_degrees = 0
        last_source_shape = None

        def recognize(self, _path):
            return [
                _line("电子银行承兑汇票", 10),
                _line("出票人信息", 50),
                _line("票据金额 177284.95", 90),
            ]

    config = AppConfig.default(); config.output_dir = tmp_path / "output"
    processor = ReceiptProcessor(config)
    processor.engine = BillOCR()
    unit = ReceiptUnit(
        tmp_path / "bill.pdf", "bill.pdf", 1, 1, tmp_path / "bill.png",
        .4, ["疑似一页多张回单，自动拆分边界不可靠"], [(100, 100, 300, 500)],
    )
    result = processor._analyze_unit(unit, FieldExtractor(config.load_rules()))
    assert result.document_type == "acceptance_bill"
    assert result.status == "review"
    assert result.unit.suggested_boxes == []
    assert any("整页保留" in reason for reason in result.reasons)
    for handler in list(processor.logger.handlers):
        handler.close(); processor.logger.removeHandler(handler)


def test_continuous_draft_layout_forces_whole_page_fallback(tmp_path: Path):
    fields = ExtractedFields(date="2025-04-22", payer="示例公司", amount="100.00")
    unit = ReceiptUnit(tmp_path / "draft.pdf", "draft.pdf", 1, 1, tmp_path / "crop.png")
    result = ProcessResult(
        unit, fields, [_line("电子银行承兑汇票", 10)], "draft.pdf", None, "success",
    )
    assert "整页" in split_fallback_reason([result])


def test_incomplete_candidate_crops_are_discarded_and_page_is_reprocessed(monkeypatch, tmp_path: Path):
    source = tmp_path / "two_receipts.png"
    image = np.full((1000, 800, 3), 255, np.uint8)
    cv2.rectangle(image, (60, 60), (740, 440), (0, 0, 0), 8)
    cv2.rectangle(image, (60, 560), (740, 940), (0, 0, 0), 8)
    cv2.imwrite(str(source), image)

    class CropValidationOCR:
        calls: list[str] = []

        def __init__(self, *_args, **_kwargs):
            pass

        def recognize(self, path: Path):
            self.calls.append(path.name)
            complete = [
                _line("交易日期 2026-09-08", 10),
                _line("付款人名称：上海示例贸易有限公司", 50),
                _line("交易金额 ￥12,500.00", 90),
            ]
            if "c02" in path.name:
                return [complete[0], complete[2]]
            return complete

    monkeypatch.setattr("bank_receipt_tool.pipeline.LocalOCREngine", CropValidationOCR)
    config = AppConfig.default(); config.output_dir = tmp_path / "output"
    results = ReceiptProcessor(config).run([source], RunOptions("restart"))

    assert len(results) == 1
    assert results[0].unit.crop_number == 1
    assert any("回退为整页" in reason for reason in results[0].reasons)
    assert CropValidationOCR.calls == ["p0001_c01.png", "p0001_c02.png", "p0001_full.png"]
    assert len(list((config.output_dir / "review").glob("*.pdf"))) == 1
    assert not list((config.output_dir / "success").glob("*.pdf"))


def test_checkpoint_rejects_modified_source(tmp_path: Path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"first")
    fingerprint = source_fingerprint(source)
    store = CheckpointStore(tmp_path / "output")
    signature = {"app_version": "0.2.0"}
    checkpoint = store.create(fingerprint, signature, 2, [1, 2])
    checkpoint.set_status("stopped")
    assert store.latest(fingerprint, signature) is not None
    source.write_bytes(b"changed")
    assert store.latest(source_fingerprint(source), signature) is None


def test_completed_checkpoint_is_listed_and_review_update_is_persisted(tmp_path: Path):
    source = tmp_path / "history.pdf"
    source.write_bytes(b"history")
    output = tmp_path / "output"
    store = CheckpointStore(output)
    checkpoint = store.create(source_fingerprint(source), {"app_version": "0.3.2"}, 1, [1])
    unit = ReceiptUnit(source, source.name, 1, 1, output / ".previews" / "p1.png")
    result = ProcessResult(
        unit,
        ExtractedFields(date="2026-09-08", payer="示例公司", amount="100.00"),
        [_line("交易时间 2026-09-08 10:00:00", 10)],
        "2026-09-08_示例公司_100.00.pdf",
        output / "review" / "待复核.pdf",
        "review",
        ["人工确认"],
    )
    checkpoint.complete_page(1, [result])
    checkpoint.set_status("completed")

    history = store.history()
    assert len(history) == 1
    assert history[0].data["status"] == "completed"
    loaded = history[0].results[0]
    loaded.fields.payer = "人工确认公司"
    loaded.status = "success"
    loaded.reasons = []
    assert history[0].update_result(loaded)

    persisted = CheckpointStore(output).history()[0].results[0]
    assert persisted.fields.payer == "人工确认公司"
    assert persisted.status == "success"
    assert persisted.reasons == []


def test_streaming_stop_and_resume_without_reprocessing_page(monkeypatch, tmp_path: Path):
    source = tmp_path / "three_pages.pdf"
    _pdf(source)
    config = AppConfig.default()
    config.output_dir = tmp_path / "output"
    config.render_dpi = 72
    monkeypatch.setattr("bank_receipt_tool.pipeline.LocalOCREngine", FakeOCR)

    progress: list[tuple[int, int, str]] = []
    first = ReceiptProcessor(config, lambda done, total, text: progress.append((done, total, text)))
    FakeOCR.calls = []
    FakeOCR.stop_processor = first
    first_results = first.run([source], RunOptions("restart"))
    assert [result.unit.page_number for result in first_results] == [1]
    assert any(done == 1 and total == 3 for done, total, _ in progress)

    second = ReceiptProcessor(config)
    FakeOCR.calls = []
    FakeOCR.stop_processor = None
    resumed_results = second.run([source], RunOptions("resume"))
    assert [result.unit.page_number for result in resumed_results] == [1, 2, 3]
    assert len(FakeOCR.calls) == 2
    jobs = list((config.output_dir / ".jobs").glob("*.json"))
    statuses = [json.loads(path.read_text(encoding="utf-8"))["status"] for path in jobs]
    assert "completed" in statuses


def test_page_range_processes_only_selected_pages(monkeypatch, tmp_path: Path):
    source = tmp_path / "range.pdf"
    _pdf(source)
    config = AppConfig.default(); config.output_dir = tmp_path / "output"; config.render_dpi = 72
    monkeypatch.setattr("bank_receipt_tool.pipeline.LocalOCREngine", FakeOCR)
    FakeOCR.calls = []; FakeOCR.stop_processor = None
    results = ReceiptProcessor(config).run([source], RunOptions("range", "2-3"))
    assert [result.unit.page_number for result in results] == [2, 3]
    assert len(FakeOCR.calls) == 2
