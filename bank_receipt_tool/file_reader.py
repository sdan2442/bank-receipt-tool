from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Iterator

import fitz

from .domain import ReceiptUnit
from .image_processing import detect_receipts_detailed, preprocess, read_image, write_image_atomic

SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def collect_inputs(paths: list[Path]) -> list[Path]:
    found: set[Path] = set()
    for path in paths:
        if path.is_dir():
            found.update(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED)
        elif path.is_file() and path.suffix.lower() in SUPPORTED:
            found.add(path)
    return sorted(found)


def input_page_count(path: Path) -> int:
    if path.suffix.lower() != ".pdf":
        return 1
    with fitz.open(path) as document:
        return document.page_count


def iter_input_pages(
    path: Path,
    work: Path,
    dpi: int = 220,
    selected_pages: set[int] | None = None,
    cancel: Event | None = None,
) -> Iterator[tuple[int, Path]]:
    work.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() != ".pdf":
        if (not selected_pages or 1 in selected_pages) and not (cancel and cancel.is_set()):
            yield 1, path
        return
    document = fitz.open(path)
    try:
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        for index, page in enumerate(document, 1):
            if cancel and cancel.is_set():
                break
            if selected_pages and index not in selected_pages:
                continue
            target = work / f"page_{index:04d}.png"
            page.get_pixmap(matrix=matrix, alpha=False).save(target)
            yield index, target
    finally:
        document.close()


def split_page(path: Path, page_number: int, image_path: Path, work_dir: Path) -> list[ReceiptUnit]:
    image, _ = preprocess(read_image(image_path))
    detection = detect_receipts_detailed(image)
    units: list[ReceiptUnit] = []
    for crop_number, (crop, confidence) in enumerate(detection.crops, 1):
        target = work_dir / f"p{page_number:04d}_c{crop_number:02d}.png"
        write_image_atomic(target, crop)
        units.append(ReceiptUnit(
            source_path=path, source_name=path.name, page_number=page_number,
            crop_number=crop_number, image_path=target,
            boundary_confidence=confidence,
            boundary_reasons=list(detection.reasons),
            suggested_boxes=list(detection.suggested_boxes),
        ))
    return units


def whole_page_unit(
    path: Path,
    page_number: int,
    image_path: Path,
    work_dir: Path,
    reason: str,
    suggested_boxes: list[tuple[int, int, int, int]] | None = None,
) -> ReceiptUnit:
    """Create a safe whole-page fallback after candidate crops fail validation."""
    image, _ = preprocess(read_image(image_path))
    target = work_dir / f"p{page_number:04d}_full.png"
    write_image_atomic(target, image)
    return ReceiptUnit(
        source_path=path,
        source_name=path.name,
        page_number=page_number,
        crop_number=1,
        image_path=target,
        boundary_confidence=.65,
        boundary_reasons=[reason],
        suggested_boxes=list(suggested_boxes or []),
    )


def split_input(path: Path, work_dir: Path, dpi: int = 220, cancel: Event | None = None) -> list[ReceiptUnit]:
    """Compatibility helper; the application pipeline uses the streaming APIs."""
    units: list[ReceiptUnit] = []
    for page_number, image_path in iter_input_pages(path, work_dir, dpi, cancel=cancel):
        if cancel and cancel.is_set():
            break
        units.extend(split_page(path, page_number, image_path, work_dir))
    return units
