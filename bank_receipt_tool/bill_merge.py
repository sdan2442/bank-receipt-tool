from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path

from .domain import ExtractedFields, ProcessResult, ReceiptUnit
from .exporter import images_to_pdf_atomic, review_image_path, save_review_metadata
from .naming import suggested_filename, unique_path


def bill_merge_candidates(results: list[ProcessResult]) -> list[ProcessResult]:
    """Return locally previewable, unmerged acceptance-bill pages."""
    return [
        result
        for result in page_merge_candidates(results)
        if result.document_type.startswith("acceptance_bill")
        and result.document_type != "acceptance_bill_merged"
    ]


def page_merge_candidates(results: list[ProcessResult]) -> list[ProcessResult]:
    return [
        result for result in results
        if result.status in {"review", "success"}
        and not result.merged_page_paths
        and (result.unit.image_path.is_file()
             or (result.output_path is not None and result.output_path.is_file()))
    ]


def merge_bill_results(
    ordered_results: list[ProcessResult], output_dir: Path
) -> ProcessResult:
    """Create a review PDF in the exact order chosen by the reviewer."""
    if len(ordered_results) < 2:
        raise ValueError("至少选择两页才能合并")
    if len(page_merge_candidates(ordered_results)) != len(ordered_results):
        raise ValueError("部分选中页面无法用于合并")
    page_paths = [review_image_path(result, output_dir) for result in ordered_results]
    if len(set(page_paths)) != len(page_paths):
        raise ValueError("同一个页面只能加入一次")

    is_bill = len(bill_merge_candidates(ordered_results)) == len(ordered_results)
    label = "承兑汇票合并件" if is_bill else "手动合并件"
    target = unique_path(output_dir / "review", f"{label}_待复核.pdf")
    images_to_pdf_atomic(page_paths, target)
    labels = [
        f"{result.unit.source_name} 第{result.unit.page_number}页/裁切{result.unit.crop_number}"
        for result in ordered_results
    ]
    first = ordered_results[0]
    fields = deepcopy(first.fields) if is_bill else ExtractedFields()
    suggested = suggested_filename(fields)
    if not all((fields.date, fields.payer, fields.amount)):
        suggested = target.name
    merged = ProcessResult(
        unit=ReceiptUnit(
            source_path=target,
            source_name=f"{label}（{len(page_paths)}页）",
            page_number=0,
            crop_number=0,
            image_path=page_paths[0],
        ),
        fields=fields,
        ocr_lines=[line for member in ordered_results for line in member.ocr_lines],
        suggested_name=suggested,
        output_path=target,
        status="review",
        reasons=["页面已按勾选顺序合并，需人工复核"],
        processed_at=datetime.now().isoformat(timespec="seconds"),
        document_type="acceptance_bill_merged" if is_bill else "manual_merged",
        document_type_confidence=min(
            result.document_type_confidence for result in ordered_results
        ) if is_bill else 0.0,
        document_type_evidence=list(labels),
        merged_page_paths=list(page_paths),
        merged_page_labels=list(labels),
    )
    save_review_metadata(merged, target)
    return merged
