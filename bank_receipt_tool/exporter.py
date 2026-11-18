from __future__ import annotations

import json
import re
from pathlib import Path

import fitz
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .domain import ProcessResult


HEADERS = ["原始文件路径", "原始文件名", "页码或裁切序号", "文件类型", "类型置信度", "新文件名", "识别日期", "付款人户名", "收款人户名", "金额",
           "日期置信度", "付款人置信度", "收款人置信度", "金额置信度", "处理状态", "错误或复核原因", "处理时间",
           "发票号码", "购买方", "发票号置信度", "购买方置信度"]


def images_to_pdf_atomic(image_paths: list[Path], output_path: Path) -> None:
    if not image_paths:
        raise ValueError("没有可写入的页面图像")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_name(output_path.stem + ".tmp.pdf")
    doc = fitz.open()
    try:
        for image_path in image_paths:
            image = fitz.Pixmap(image_path)
            page = doc.new_page(width=image.width, height=image.height)
            page.insert_image(page.rect, pixmap=image)
        doc.save(tmp, deflate=True, garbage=3)
    finally:
        doc.close()
    if not tmp.exists() or tmp.stat().st_size < 100:
        raise OSError("临时 PDF 写入不完整")
    tmp.replace(output_path)


def image_to_pdf_atomic(image_path: Path, output_path: Path) -> None:
    images_to_pdf_atomic([image_path], output_path)


def review_image_path(result: ProcessResult, output_dir: Path) -> Path:
    """Restore a missing single-page preview from the saved PDF."""
    if result.unit.image_path.is_file():
        return result.unit.image_path
    if not result.output_path or not result.output_path.is_file():
        raise FileNotFoundError("找不到页面预览或已保存的 PDF")
    from .naming import unique_path

    directory = output_dir / "review" / ".previews"
    with fitz.open(result.output_path) as document:
        if document.page_count != 1:
            raise ValueError("仅能从单页 PDF 恢复此页面预览")
        target = unique_path(directory, result.output_path.stem + ".png")
        temp = target.with_suffix(".tmp.png")
        try:
            document[0].get_pixmap(alpha=False).save(temp)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
    return target


def save_review_metadata(result: ProcessResult, review_pdf: Path) -> Path:
    target = review_pdf.with_suffix(".json")
    tmp = target.with_suffix(".tmp.json")
    data = {
        "source_path": str(result.unit.source_path), "source_name": result.unit.source_name,
        "page_number": result.unit.page_number, "crop_number": result.unit.crop_number,
        "boundary_confidence": result.unit.boundary_confidence,
        "boundary_reasons": result.unit.boundary_reasons,
        "suggested_boxes": result.unit.suggested_boxes,
        "document_type": result.document_type,
        "document_type_confidence": result.document_type_confidence,
        "document_type_evidence": result.document_type_evidence,
        "merged_page_paths": [str(path) for path in result.merged_page_paths],
        "merged_page_labels": result.merged_page_labels,
        "needs_split": result.needs_split,
        "suggested_name": result.suggested_name, "reasons": result.reasons,
        "fields": {"date": result.fields.date, "payer": result.fields.payer, "payee": result.fields.payee,
                   "amount": result.fields.amount, "date_confidence": result.fields.date_confidence,
                   "payer_confidence": result.fields.payer_confidence,
                   "payee_confidence": result.fields.payee_confidence,
                   "amount_confidence": result.fields.amount_confidence,
                   "invoice_number": result.fields.invoice_number, "buyer": result.fields.buyer,
                   "invoice_number_confidence": result.fields.invoice_number_confidence,
                   "buyer_confidence": result.fields.buyer_confidence},
        "candidates": {
            "date": [vars_candidate(x) for x in result.fields.date_candidates],
            "payer": [vars_candidate(x) for x in result.fields.payer_candidates],
            "payee": [vars_candidate(x) for x in result.fields.payee_candidates],
            "amount": [vars_candidate(x) for x in result.fields.amount_candidates],
            "invoice_number": [vars_candidate(x) for x in result.fields.invoice_number_candidates],
            "buyer": [vars_candidate(x) for x in result.fields.buyer_candidates],
        },
        "ocr": [{"text": x.text, "box": x.box, "confidence": x.confidence} for x in result.ocr_lines],
    }
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


def vars_candidate(value) -> dict:
    return {"value": value.value, "confidence": value.confidence, "source": value.source, "line_index": value.line_index}


def export_xlsx(results: list[ProcessResult], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.stem + ".tmp.xlsx")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "处理记录"
    sheet.append(HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for result in results:
        f, u = result.fields, result.unit
        sheet.append([str(u.source_path), u.source_name, f"第{u.page_number}页/裁切{u.crop_number}",
                      result.document_type, round(result.document_type_confidence, 4),
                      result.output_path.name if result.output_path else result.suggested_name,
                      f.date or "", f.payer or "", f.payee or "", f.amount or "", round(f.date_confidence, 4),
                      round(f.payer_confidence, 4), round(f.payee_confidence, 4), round(f.amount_confidence, 4), result.status,
                      "; ".join(result.reasons) or result.error, result.processed_at,
                      f.invoice_number or "", f.buyer or "",
                      round(f.invoice_number_confidence, 4) if result.document_type == "invoice" else None,
                      round(f.buyer_confidence, 4) if result.document_type == "invoice" else None])
        # OCR and user-entered values are data, including strings beginning with "=".
        for cell in sheet[sheet.max_row]:
            if cell.data_type == "f":
                cell.data_type = "s"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = [45, 26, 18, 22, 14, 45, 14, 30, 30, 16, 14, 16, 16, 14, 12, 42, 22, 26, 32, 16, 16]
    for i, width in enumerate(widths, 1): sheet.column_dimensions[get_column_letter(i)].width = width
    workbook.save(tmp)
    tmp.replace(target)


def redact_sensitive(text: str) -> str:
    text = re.sub(r"(?<!\d)(\d{4})\d{8,15}(\d{4})(?!\d)", r"\1********\2", text)
    return re.sub(r"(?<!\d)(\d{6})\d{8}(\d{3}[\dXx])(?!\d)", r"\1********\2", text)
