from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .domain import OCRLine


DocumentType = Literal[
    "receipt", "acceptance_bill", "acceptance_bill_endorsement", "invoice", "unknown"
]

DOCUMENT_TYPE_LABELS: dict[str, str] = {
    "receipt": "银行回单",
    "invoice": "发票",
    "acceptance_bill": "承兑汇票正面/详情",
    "acceptance_bill_endorsement": "承兑汇票背书页",
    "acceptance_bill_merged": "承兑汇票合并件",
    "manual_merged": "手动合并件",
    "unknown": "未知类型",
}


@dataclass(slots=True)
class DocumentClassification:
    document_type: DocumentType
    confidence: float
    evidence: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return DOCUMENT_TYPE_LABELS[self.document_type]


def _compact(lines: list[OCRLine]) -> str:
    return re.sub(r"[\s：:;；（）()【】\[\]]+", "", "".join(line.text for line in lines))


def _marker_score(text: str, markers: dict[str, int]) -> tuple[int, list[str]]:
    evidence = [marker for marker in markers if marker in text]
    return sum(markers[marker] for marker in evidence), evidence


def _invoice_evidence(lines: list[OCRLine], rules: dict) -> list[str]:
    # Match an actual title or a combination of dedicated labels. A reference
    # to a single invoice in a receipt's remarks does not change its type.
    titles = set(rules.get("titles", []))
    compact = [_compact([line]) for line in lines]
    title = next((value for value in compact if value in titles), None)
    keys = ("发票号码", "开票日期", "价税合计")
    evidence = [
        key for key in keys if any(value.startswith(
            ("发票号码", "发票号") if key == "发票号码" else (key,)
        ) for value in compact)
    ]
    return [title, *evidence] if title else evidence if len(evidence) == len(keys) else []


def classify_document(lines: list[OCRLine], rules: dict) -> DocumentClassification:
    """Classify locally from OCR text before document-specific field extraction."""
    text = _compact(lines)
    if not text:
        return DocumentClassification("unknown", 0.0, [])

    type_rules = rules.get("document_types", {})
    receipt_markers = type_rules.get("receipt", {}).get("markers", {})
    bill_markers = type_rules.get("acceptance_bill", {}).get("markers", {})
    endorsement_markers = type_rules.get("acceptance_bill_endorsement", {}).get("markers", {})
    receipt_score, receipt_evidence = _marker_score(text, receipt_markers)
    bill_score, bill_evidence = _marker_score(text, bill_markers)
    endorsement_score, endorsement_evidence = _marker_score(text, endorsement_markers)

    bill_title_markers = type_rules.get("acceptance_bill", {}).get("strong_markers", [])
    bill_title = any(marker in text for marker in bill_title_markers)
    if endorsement_score >= 5 and (bill_title or bill_score >= 4):
        confidence = min(.99, .68 + endorsement_score * .035 + min(bill_score, 8) * .015)
        return DocumentClassification(
            "acceptance_bill_endorsement",
            confidence,
            endorsement_evidence + [item for item in bill_evidence if item not in endorsement_evidence],
        )
    if bill_title or bill_score >= 7:
        confidence = min(.99, .68 + bill_score * .03)
        return DocumentClassification("acceptance_bill", confidence, bill_evidence)
    invoice_evidence = _invoice_evidence(lines, type_rules.get("invoice", {}))
    if invoice_evidence:
        confidence = min(.99, .79 + .065 * len(invoice_evidence))
        return DocumentClassification("invoice", confidence, invoice_evidence)
    if receipt_score >= 4 and receipt_score >= bill_score + 2:
        confidence = min(.99, .66 + receipt_score * .035)
        return DocumentClassification("receipt", confidence, receipt_evidence)

    evidence = receipt_evidence if receipt_score >= bill_score else bill_evidence
    confidence = min(.59, .25 + max(receipt_score, bill_score, endorsement_score) * .035)
    return DocumentClassification("unknown", confidence, evidence)


def document_type_label(document_type: str) -> str:
    return DOCUMENT_TYPE_LABELS.get(document_type, DOCUMENT_TYPE_LABELS["unknown"])
