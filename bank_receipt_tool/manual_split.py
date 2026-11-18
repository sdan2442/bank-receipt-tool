from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .config import AppConfig
from .document_classifier import classify_document
from .domain import ProcessResult
from .exporter import image_to_pdf_atomic, save_review_metadata
from .extractor import FieldExtractor
from .image_processing import normalize_rect, read_image, write_image_atomic
from .invoice import extract_invoice
from .naming import suggested_filename, unique_path
from .page_regions import Rect, lines_in_rect


def validate_regions(regions: list[Rect], shape: tuple[int, int]) -> None:
    if len(regions) < 2:
        raise ValueError("请至少框选两个区域")
    for rect in regions:
        _, _, width, height = rect
        if normalize_rect(rect, shape) != rect or min(width, height) < 8:
            raise ValueError("裁切框太小或超出页面边界")


def create_manual_crops(
    parent: ProcessResult, preview: Path, regions: list[Rect], config: AppConfig,
    first_number: int, created: list[Path],
) -> list[ProcessResult]:
    image = read_image(preview)
    validate_regions(regions, image.shape[:2])
    rules = config.load_rules()
    extractor = FieldExtractor(rules, config.confidence_threshold)
    results = []
    for index, rect in enumerate(regions, first_number):
        x, y, width, height = rect
        lines = lines_in_rect(parent.ocr_lines, rect)
        classification = classify_document(lines, rules)
        fields = (extract_invoice(lines, config.confidence_threshold)
                  if classification.document_type == "invoice" else extractor.extract(lines))
        name = suggested_filename(fields, classification.document_type)
        target = unique_path(config.output_dir / "review", name)
        thumbnail = unique_path(config.output_dir / "review" / ".previews", target.stem + ".png")
        unit = replace(
            parent.unit, crop_number=index, image_path=thumbnail, boundary_confidence=1.0,
            boundary_reasons=[], suggested_boxes=[],
        )
        result = ProcessResult(
            unit, fields, lines, name, target, "review",
            ["人工拆分，已复用原页 OCR，请核对字段", *fields.review_reasons],
            processed_at=datetime.now().isoformat(timespec="seconds"),
            document_type=classification.document_type,
            document_type_confidence=classification.confidence,
            document_type_evidence=classification.evidence,
        )
        write_image_atomic(thumbnail, image[y:y + height, x:x + width])
        created.append(thumbnail)
        image_to_pdf_atomic(thumbnail, target)
        created.append(target)
        created.append(save_review_metadata(result, target))
        results.append(result)
    return results
