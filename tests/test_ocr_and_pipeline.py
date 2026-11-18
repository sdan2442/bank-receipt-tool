from pathlib import Path
from types import SimpleNamespace
import sys
import numpy as np
from bank_receipt_tool.config import AppConfig
from bank_receipt_tool.domain import OCRLine, ReceiptUnit
from bank_receipt_tool.extractor import FieldExtractor
from bank_receipt_tool.image_processing import write_image_atomic
from bank_receipt_tool.ocr_engine import LocalOCREngine
from bank_receipt_tool.pipeline import ReceiptProcessor


def test_mobile_model_names_are_explicit_and_model_source_check_is_disabled(tmp_path, monkeypatch):
    for name in ("PP-OCRv5_mobile_det_infer", "PP-OCRv5_mobile_rec_infer"):
        model_dir = tmp_path / name
        model_dir.mkdir()
        (model_dir / "inference.json").write_text("{}", encoding="utf-8")
        (model_dir / "inference.pdiparams").write_bytes(b"test")

    captured = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddleOCR))
    monkeypatch.delenv("DISABLE_MODEL_SOURCE_CHECK", raising=False)
    LocalOCREngine(tmp_path, "mobile")

    assert captured["text_detection_model_name"] == "PP-OCRv5_mobile_det"
    assert captured["text_recognition_model_name"] == "PP-OCRv5_mobile_rec"
    assert captured["text_detection_model_dir"].endswith("PP-OCRv5_mobile_det_infer")
    assert captured["text_recognition_model_dir"].endswith("PP-OCRv5_mobile_rec_infer")
    assert __import__("os").environ["DISABLE_MODEL_SOURCE_CHECK"] == "True"


def test_balanced_mode_uses_mobile_detection_and_server_recognition(tmp_path, monkeypatch):
    for name in ("PP-OCRv5_mobile_det_infer", "PP-OCRv5_server_rec_infer"):
        model_dir = tmp_path / name
        model_dir.mkdir()
        (model_dir / "inference.json").write_text("{}", encoding="utf-8")
        (model_dir / "inference.pdiparams").write_bytes(b"test")

    captured = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddleOCR))
    LocalOCREngine(tmp_path, "balanced")
    assert captured["text_detection_model_name"] == "PP-OCRv5_mobile_det"
    assert captured["text_recognition_model_name"] == "PP-OCRv5_server_rec"


def test_180_degree_result_is_selected(tmp_path: Path):
    image = tmp_path / "upside_down.png"
    write_image_atomic(image, np.full((100, 300, 3), 255, np.uint8))
    engine = LocalOCREngine.__new__(LocalOCREngine)
    def fake(path):
        if "_rot180" in str(path):
            return [OCRLine("交易日期 2026-09-08", [[0,0],[10,0],[10,10],[0,10]], .96)]
        return [OCRLine("模糊", [[0,0],[10,0],[10,10],[0,10]], .40)]
    engine._run_once = fake
    assert engine.recognize(image)[0].text.startswith("交易日期")


def test_ocr_failure_is_preserved_in_failed(tmp_path: Path):
    image = tmp_path / "receipt.png"
    write_image_atomic(image, np.full((120, 300, 3), 255, np.uint8))
    cfg = AppConfig.default(); cfg.output_dir = tmp_path / "output"
    processor = ReceiptProcessor(cfg)
    class FailingEngine:
        def recognize(self, _path): raise RuntimeError("synthetic OCR failure")
    processor.engine = FailingEngine()
    unit = ReceiptUnit(image, image.name, 1, 1, image)
    result = processor._process_unit(unit, FieldExtractor(cfg.load_rules()))
    assert result.status == "failed"
    assert result.output_path and result.output_path.exists()
    assert image.exists()
    for handler in list(processor.logger.handlers):
        handler.close(); processor.logger.removeHandler(handler)


def test_field_extraction_failure_preserves_ocr_and_enters_review(tmp_path: Path):
    cfg = AppConfig.default(); cfg.output_dir = tmp_path / "output"
    processor = ReceiptProcessor(cfg)
    lines = [OCRLine("交易金额 12,500.00", [[0, 0], [200, 0], [200, 20], [0, 20]], .99)]

    class WorkingEngine:
        last_rotation_degrees = 0
        last_source_shape = None

        def recognize(self, _path):
            return lines

    class BrokenExtractor:
        def extract(self, _lines):
            raise KeyError(1)

    processor.engine = WorkingEngine()
    image = tmp_path / "receipt.png"
    unit = ReceiptUnit(image, image.name, 1, 1, image)
    result = processor._analyze_unit(unit, BrokenExtractor())
    assert result.status == "review"
    assert result.ocr_lines == lines
    assert "字段解析异常，需人工复核" in result.reasons
    assert "字段解析异常" in result.error
    for handler in list(processor.logger.handlers):
        handler.close(); processor.logger.removeHandler(handler)


def test_bank_payer_reason_forces_pipeline_review_status(tmp_path: Path):
    cfg = AppConfig.default(); cfg.output_dir = tmp_path / "output"
    processor = ReceiptProcessor(cfg)

    class BankPayerEngine:
        last_rotation_degrees = 0
        last_source_shape = None

        def recognize(self, _path):
            texts = (
                "网上银行电子回单", "交易流水号 202600000001",
                "交易日期 2026-09-08", "付款人名称：中国银行上海浦东支行",
                "收款人名称：上海示例贸易有限公司", "交易金额 12,500.00",
            )
            return [
                OCRLine(text, [[0, y], [600, y], [600, y + 20], [0, y + 20]], .99)
                for y, text in enumerate(texts, 0)
            ]

    processor.engine = BankPayerEngine()
    image = tmp_path / "receipt.png"
    unit = ReceiptUnit(image, image.name, 1, 1, image)
    result = processor._analyze_unit(unit, FieldExtractor(cfg.load_rules()))
    assert result.status == "review"
    assert any("付款人疑似银行机构" in reason for reason in result.reasons)
    for handler in list(processor.logger.handlers):
        handler.close(); processor.logger.removeHandler(handler)


def test_all_successful_mobile_results_are_forced_to_review(tmp_path: Path):
    cfg = AppConfig.default(); cfg.output_dir = tmp_path / "output"; cfg.model_mode = "mobile"
    processor = ReceiptProcessor(cfg)

    class ValidReceiptEngine:
        last_rotation_degrees = 0
        last_source_shape = None

        def recognize(self, _path):
            texts = (
                "网上银行电子回单", "交易流水号 202600000001",
                "交易日期 2026-09-08", "付款人名称：上海示例贸易有限公司",
                "收款人名称：北京示例科技有限公司", "交易金额 12,500.00",
            )
            return [
                OCRLine(text, [[0, y], [600, y], [600, y + 20], [0, y + 20]], .99)
                for y, text in enumerate(texts, 0)
            ]

    processor.engine = ValidReceiptEngine()
    image = tmp_path / "receipt.png"
    result = processor._analyze_unit(
        ReceiptUnit(image, image.name, 1, 1, image), FieldExtractor(cfg.load_rules())
    )
    assert result.status == "review"
    assert "快速模式（纯 mobile）识别结果需人工复核" in result.reasons
    for handler in list(processor.logger.handlers):
        handler.close(); processor.logger.removeHandler(handler)


def test_balanced_mode_is_not_forced_to_review(tmp_path: Path):
    cfg = AppConfig.default(); cfg.output_dir = tmp_path / "output"; cfg.model_mode = "balanced"
    processor = ReceiptProcessor(cfg)

    class ValidReceiptEngine:
        last_rotation_degrees = 0
        last_source_shape = None

        def recognize(self, _path):
            texts = (
                "网上银行电子回单", "交易流水号 202600000001",
                "交易日期 2026-09-08", "付款人名称：上海示例贸易有限公司",
                "收款人名称：北京示例科技有限公司", "交易金额 12,500.00",
            )
            return [
                OCRLine(text, [[0, y], [600, y], [600, y + 20], [0, y + 20]], .99)
                for y, text in enumerate(texts, 0)
            ]

    processor.engine = ValidReceiptEngine()
    image = tmp_path / "receipt.png"
    result = processor._analyze_unit(
        ReceiptUnit(image, image.name, 1, 1, image), FieldExtractor(cfg.load_rules())
    )
    assert "快速模式（纯 mobile）识别结果需人工复核" not in result.reasons
    for handler in list(processor.logger.handlers):
        handler.close(); processor.logger.removeHandler(handler)

def test_confident_upright_skips_second_ocr(tmp_path: Path):
    image = tmp_path / "upright.png"
    write_image_atomic(image, np.full((100, 300, 3), 255, np.uint8))
    engine = LocalOCREngine.__new__(LocalOCREngine)
    calls = []
    def fake(path):
        calls.append(path)
        return [
            OCRLine("交易日期", [[0,0],[10,0],[10,10],[0,10]], .95),
            OCRLine("付款人", [[0,20],[10,20],[10,30],[0,30]], .94),
            OCRLine("金额", [[0,40],[10,40],[10,50],[0,50]], .96),
        ]
    engine._run_once = fake
    assert len(engine.recognize(image)) == 3
    assert len(calls) == 1

def test_bottom_heavy_upside_down_selection_rotates_saved_image(tmp_path: Path):
    import cv2
    from bank_receipt_tool.image_processing import read_image
    image = tmp_path / "bottom_heavy.png"
    pixels = np.full((100, 300, 3), 255, np.uint8)
    cv2.rectangle(pixels, (260, 75), (295, 98), (0, 0, 0), -1)
    write_image_atomic(image, pixels)
    engine = LocalOCREngine.__new__(LocalOCREngine)
    def lines_at(y_values):
        return [OCRLine(text, [[0,y],[20,y],[20,y+8],[0,y+8]], .95)
                for text, y in zip(("交易时间", "付款人", "金额"), y_values)]
    engine._run_once = lambda path: lines_at((10, 20, 30)) if "_rot180" in str(path) else lines_at((70, 80, 90))
    result = engine.recognize(image)
    updated = read_image(image)
    assert result[0].box[0][1] == 10
    assert updated[:25, :45].mean() < 250


def test_vertical_ocr_boxes_trigger_90_degree_direction_comparison(tmp_path: Path):
    from bank_receipt_tool.image_processing import read_image

    image = tmp_path / "sideways_table.png"
    write_image_atomic(image, np.full((100, 300, 3), 255, np.uint8))
    engine = LocalOCREngine.__new__(LocalOCREngine)

    def vertical_lines():
        return [
            OCRLine(text, [[x, 0], [x + 8, 0], [x + 8, 40], [x, 40]], .96)
            for x, text in enumerate(("交易日期", "付款人", "金额"), 1)
        ]

    def horizontal_lines(confidence, texts):
        return [
            OCRLine(text, [[0, y], [50, y], [50, y + 10], [0, y + 10]], confidence)
            for y, text in enumerate(texts, 1)
        ]

    def fake(path):
        name = Path(path).stem
        if "_rot90cw" in name:
            return horizontal_lines(.98, ("交易日期", "付款人", "交易金额"))
        if "_rot90ccw" in name:
            return horizontal_lines(.70, ("期日易交", "人款付", "额金"))
        return vertical_lines()

    engine._run_once = fake
    result = engine.recognize(image)
    updated = read_image(image)
    assert result[0].text == "交易日期"
    assert updated.shape[:2] == (300, 100)


def test_90_degree_fix_discards_sideways_text_and_reruns_full_upright_page(tmp_path: Path):
    from bank_receipt_tool.image_processing import read_image

    image = tmp_path / "sideways_bill.png"
    write_image_atomic(image, np.full((100, 300, 3), 255, np.uint8))
    engine = LocalOCREngine.__new__(LocalOCREngine)
    calls: list[str] = []

    def fake(path):
        name = Path(path).stem
        calls.append(name)
        if name.endswith("_rot90cw_full"):
            return [OCRLine("电子银行承兑汇票", [[0, 0], [180, 0], [180, 20], [0, 20]], .99)]
        if "_rot90cw_probe" in name:
            return [OCRLine("电子银行承兑汇票", [[0, 0], [180, 0], [180, 20], [0, 20]], .99)]
        if "_rot90ccw_probe" in name:
            return [OCRLine("票汇兑承行银子电", [[0, 0], [180, 0], [180, 20], [0, 20]], .50)]
        return [
            OCRLine(text, [[x, 0], [x + 8, 0], [x + 8, 80], [x, 80]], .30)
            for x, text in enumerate(("中", "收", "款"), 1)
        ]

    engine._run_once = fake
    result = engine.recognize(image)
    assert [line.text for line in result] == ["电子银行承兑汇票"]
    assert any(name.endswith("_rot90cw_full") for name in calls)
    assert engine.last_rotation_degrees == 90
    assert engine.last_source_shape == (100, 300)
    assert read_image(image).shape[:2] == (300, 100)


def test_90_degree_full_page_low_confidence_retries_opposite_direction(tmp_path: Path):
    image = tmp_path / "ambiguous_sideways_bill.png"
    write_image_atomic(image, np.full((100, 300, 3), 255, np.uint8))
    engine = LocalOCREngine.__new__(LocalOCREngine)

    def fake(path):
        name = Path(path).stem
        if name.endswith("_rot90ccw_full"):
            return [OCRLine("乱码", [[0, 0], [180, 0], [180, 20], [0, 20]], .12)]
        if name.endswith("_rot90cw_full"):
            return [OCRLine("电子银行承兑汇票", [[0, 0], [180, 0], [180, 20], [0, 20]], .99)]
        if "_rot90ccw_probe" in name:
            # Deliberately make the small probe select the wrong direction.
            return [OCRLine("中收款", [[0, 0], [180, 0], [180, 20], [0, 20]], .99)]
        if "_rot90cw_probe" in name:
            return [OCRLine("不清楚", [[0, 0], [180, 0], [180, 20], [0, 20]], .60)]
        return [
            OCRLine(text, [[x, 0], [x + 8, 0], [x + 8, 80], [x, 80]], .30)
            for x, text in enumerate(("中", "收", "款"), 1)
        ]

    engine._run_once = fake
    result = engine.recognize(image)
    assert [line.text for line in result] == ["电子银行承兑汇票"]
    assert engine.last_rotation_degrees == 90
