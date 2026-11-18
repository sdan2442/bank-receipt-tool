from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from .domain import Candidate, ExtractedFields, OCRLine
from .extractor import chinese_amount, normalize_date


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def invoice_number(text: str) -> str | None:
    value = "".join(_normalize(text).split())
    return value if re.fullmatch(r"(?:[0-9]{8}|[0-9]{20})", value) else None


def issue_date(text: str) -> str | None:
    value = "".join(_normalize(text).split())
    if re.fullmatch(r"20[0-9]{2}(?:[0-9]{4}|[-./][0-9]{1,2}[-./][0-9]{1,2}|年[0-9]{1,2}月[0-9]{1,2}日)", value):
        return normalize_date(value)
    return None


def invoice_total(text: str) -> str | None:
    value = "".join(_normalize(text).split()).replace("−", "-")
    match = re.fullmatch(r"([+-]?)(¥?)(-?)((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,2})?)元?", value)
    if not match or (match[1] and match[3]):
        return None
    number = Decimal((match[1] or match[3]) + match[4].replace(",", ""))
    if abs(number) < Decimal("1000000000000"):
        return f"{number:.2f}"
    return None


def buyer_name(text: str) -> str | None:
    value = re.sub(r"\s+", " ", _normalize(text)).strip(" :;")
    if (2 <= len(value) <= 100 and re.search(r"[A-Za-z\u4e00-\u9fff]", value)
            and not re.search(r"识别号|信用代码|^名称|^[购销][买售]方|^开户|^地址|^电话|[：:]", value)):
        return value
    return None


NUMBER_LABEL = re.compile(r"^发\s*票\s*号(?:\s*码)?\s*:?\s*")
DATE_LABEL = re.compile(r"^开\s*票\s*日\s*期\s*:?\s*")
BUYER_NAME_LABEL = re.compile(r"^购买方\s*(?:信息\s*)?(?:名称|单位名称)\s*:?\s*")
NAME_LABEL = re.compile(r"^名\s*称\s*:?\s*")
TOTAL_LABEL = re.compile(r"^价\s*税\s*合\s*计")
LOWERCASE_LABEL = re.compile(r"^(?:价\s*税\s*合\s*计\s*)?\(?\s*小\s*写\s*\)?\s*:?\s*")
LOWERCASE_MARKER = re.compile(r"\(?\s*小\s*写\s*\)?\s*:?\s*")


@dataclass(frozen=True)
class TextBox:
    index: int
    line: OCRLine
    text: str
    x: float
    y: float
    left: float
    right: float
    height: float

    @classmethod
    def from_line(cls, index: int, line: OCRLine) -> TextBox:
        x, y = line.center
        xs, ys = zip(*line.box)
        return cls(index, line, _normalize(line.text), x, y, min(xs), max(xs), max(ys) - min(ys))

    def same_row(self, other: TextBox) -> bool:
        return abs(self.y - other.y) <= max(6, min(self.height, other.height) * .85)


Parser = Callable[[str], str | None]


def _label_value(
    anchor: TextBox, label: re.Pattern, boxes: list[TextBox], parser: Parser,
    source: str, right_bound: float = float("inf"), cap: float = .99,
) -> Candidate | None:
    match = label.match(anchor.text)
    if not match:
        return None
    inline = anchor.text[match.end():].strip()
    if inline:
        value = parser(inline)
        return Candidate(value, min(cap, anchor.line.confidence), source, anchor.index) if value else None

    neighbors = [
        box for box in boxes
        if anchor.x < box.left < right_bound and box.right <= right_bound
        and anchor.same_row(box) and box.left - anchor.right <= max(24, anchor.height * 12)
    ]
    if not neighbors:
        return None
    nearest = min(neighbors, key=lambda box: (box.left, abs(box.y - anchor.y)))
    value = parser(nearest.text)
    if value:
        return Candidate(value, min(cap, anchor.line.confidence, nearest.line.confidence), source, nearest.index)
    return None


def _buyer_candidates(boxes: list[TextBox]) -> list[Candidate]:
    explicit = [
        candidate for box in boxes
        if (candidate := _label_value(box, BUYER_NAME_LABEL, boxes, buyer_name, "购买方名称"))
    ]
    if explicit:
        return explicit

    names = [box for box in boxes if NAME_LABEL.match(box.text)]
    sellers = [box for box in boxes if "".join(box.text.split()).startswith("销售方")]
    buyers = [box for box in boxes if "".join(box.text.split()).rstrip(":") in {"购买方", "购买方信息"}]
    candidates = []
    for buyer in buyers:
        # Vertical role labels can span both the name and taxpayer-ID rows.
        same_block = [box for box in names if buyer.x < box.left <= buyer.right + max(48, buyer.height * 2.5)
                      and abs(box.y - buyer.y) <= max(26, buyer.height * .78)]
        seller_boundary = min(
            (box.left for box in sellers if box.left > buyer.x
             and abs(box.y - buyer.y) <= max(26, buyer.height * .72)),
            default=float("inf"),
        )
        eligible = [box for box in same_block if box.right < seller_boundary]
        if not eligible:
            continue
        name = min(eligible, key=lambda box: (box.left, abs(box.y - buyer.y)))
        right_bound = min(seller_boundary,
                          min((box.left for box in names if box.left > name.right and box.same_row(name)),
                              default=float("inf")))
        candidate = _label_value(name, NAME_LABEL, boxes, buyer_name, "购买方栏名称", right_bound,
                                 cap=min(.99, buyer.line.confidence))
        if candidate:
            candidates.append(candidate)
    if candidates or buyers:
        return candidates

    # Some scans miss the narrow vertical labels. Use the left name only for
    # an aligned two-name row between the invoice header and the item table.
    top = [box.y for box in boxes if NUMBER_LABEL.match(box.text) or DATE_LABEL.match(box.text)]
    table = [box.y for box in boxes if "".join(box.text.split()).startswith(("项目名称", "货物或应税劳务"))]
    if not top or not table:
        return []
    for left in names:
        partners = [right for right in names if left.right < right.left and left.same_row(right)]
        if len(partners) == 1 and max(top) < left.y < min(table):
            candidate = _label_value(left, NAME_LABEL, boxes, buyer_name, "并列名称左栏", partners[0].left, .84)
            if candidate:
                candidates.append(candidate)
    return candidates


def _total_candidates(boxes: list[TextBox]) -> list[Candidate]:
    totals = [box for box in boxes if TOTAL_LABEL.match(box.text)]
    result = [
        candidate for box in boxes
        if (TOTAL_LABEL.match(box.text) or any(
            total.x <= box.x and abs(total.y - box.y) <= max(8, min(total.height, box.height) * 1.35)
            for total in totals
        ))
        and (candidate := _label_value(box, LOWERCASE_LABEL, boxes, invoice_total, "价税合计（小写）"))
    ]
    for total in totals:
        marker = LOWERCASE_MARKER.search(total.text)
        if marker and (value := invoice_total(total.text[marker.end():])):
            result.append(Candidate(value, min(.99, total.line.confidence), "价税合计（小写）", total.index))
    return result


def extract_invoice(lines: list[OCRLine], threshold: float) -> ExtractedFields:
    boxes = [TextBox.from_line(index, line) for index, line in enumerate(lines) if line.box]
    fields = ExtractedFields()
    for key, label, matches in (
        ("invoice_number", "发票号", [_label_value(box, NUMBER_LABEL, boxes, invoice_number, "发票号码") for box in boxes]),
        ("buyer", "购买方", _buyer_candidates(boxes)),
        ("amount", "价税合计（小写）", _total_candidates(boxes)),
        ("date", "开票日期", [_label_value(box, DATE_LABEL, boxes, issue_date, "开票日期") for box in boxes]),
    ):
        unique: dict[str, Candidate] = {}
        for candidate in sorted((item for item in matches if item), key=lambda item: item.confidence):
            unique[candidate.value] = candidate
        setattr(fields, f"{key}_candidates", sorted(unique.values(), key=lambda item: -item.confidence))
        if len(unique) == 1:
            match = next(iter(unique.values()))
            setattr(fields, key, match.value)
            setattr(fields, f"{key}_confidence", match.confidence)
            if match.confidence < threshold:
                fields.review_reasons.append(f"{label}置信度低")
        elif unique:
            fields.review_reasons.append(f"找到多个{label}候选")
        else:
            fields.review_reasons.append(f"{label}缺失")

    if fields.amount:
        for total in boxes:
            if not TOTAL_LABEL.match(total.text):
                continue
            for box in boxes:
                if box.x >= total.x and box.same_row(total) and (upper := chinese_amount("".join(box.text.split()))):
                    if abs(Decimal(upper)) != abs(Decimal(fields.amount)):
                        fields.review_reasons.append("价税合计大小写金额不一致")
                    return fields
    return fields
