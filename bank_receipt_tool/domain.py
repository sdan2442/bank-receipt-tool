from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(slots=True)
class OCRLine:
    text: str
    box: list[list[float]]
    confidence: float

    @property
    def center(self) -> tuple[float, float]:
        return (sum(p[0] for p in self.box) / len(self.box), sum(p[1] for p in self.box) / len(self.box))


@dataclass(slots=True)
class Candidate:
    value: str
    confidence: float
    source: str
    line_index: int = -1


@dataclass(slots=True)
class ExtractedFields:
    date: str | None = None
    payer: str | None = None
    amount: str | None = None
    date_confidence: float = 0.0
    payer_confidence: float = 0.0
    amount_confidence: float = 0.0
    date_candidates: list[Candidate] = field(default_factory=list)
    payer_candidates: list[Candidate] = field(default_factory=list)
    amount_candidates: list[Candidate] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    # Kept after the original fields so older positional construction remains
    # compatible. The output filename still uses payer, not payee.
    payee: str | None = None
    payee_confidence: float = 0.0
    payee_candidates: list[Candidate] = field(default_factory=list)
    invoice_number: str | None = None
    buyer: str | None = None
    invoice_number_confidence: float = 0.0
    buyer_confidence: float = 0.0
    invoice_number_candidates: list[Candidate] = field(default_factory=list)
    buyer_candidates: list[Candidate] = field(default_factory=list)


@dataclass(slots=True)
class ReceiptUnit:
    source_path: Path
    source_name: str
    page_number: int
    crop_number: int
    image_path: Path
    boundary_confidence: float = 1.0
    boundary_reasons: list[str] = field(default_factory=list)
    suggested_boxes: list[tuple[int, int, int, int]] = field(default_factory=list)


@dataclass(slots=True)
class ProcessResult:
    unit: ReceiptUnit
    fields: ExtractedFields
    ocr_lines: list[OCRLine]
    suggested_name: str
    output_path: Path | None
    status: Literal["success", "review", "failed", "cancelled", "split"]
    reasons: list[str] = field(default_factory=list)
    error: str = ""
    processed_at: str = ""
    document_type: str = "unknown"
    document_type_confidence: float = 0.0
    document_type_evidence: list[str] = field(default_factory=list)
    # Populated for manually ordered, merged pages.
    # These local preview images are reused to save the final multi-page PDF.
    merged_page_paths: list[Path] = field(default_factory=list)
    merged_page_labels: list[str] = field(default_factory=list)
    needs_split: bool = False

    @property
    def full_text(self) -> str:
        return "\n".join(line.text for line in self.ocr_lines)
