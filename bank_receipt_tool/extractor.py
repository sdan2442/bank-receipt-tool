from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from statistics import median

import cn2an

from .domain import Candidate, ExtractedFields, OCRLine


CHINESE_AMOUNT_PATTERN = re.compile(
    r"(?:人民币\s*)?([零壹贰叁肆伍陆柒捌玖拾佰仟万亿]+(?:元|圆)[零壹贰叁肆伍陆柒捌玖角分整正]*)"
)


def normalize_date(text: str) -> str | None:
    patterns = [
        # Also accept OCR output such as 2025-12-1710:21:45, where the time is
        # joined to the date without a space.
        r"(?<!\d)(20\d{2})[-/.年](0?[1-9]|1[0-2])[-/.月](0?[1-9]|[12]\d|3[01])日?(?=(?:[ T]?(?:[01]\d|2[0-3]):)|\D|$)",
        r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return datetime(int(match[1]), int(match[2]), int(match[3])).strftime("%Y-%m-%d")
            except ValueError:
                return None
    return None


def normalize_amount(text: str) -> str | None:
    match = re.search(r"[¥￥]?\s*((?:\d{1,3}(?:,\d{3})+)|\d+)\s*(?:\.\s*(\d{1,2}))?", text)
    if not match:
        return None
    try:
        value = Decimal(match[1].replace(",", "") + "." + (match[2] or "00").ljust(2, "0"))
        if not (Decimal("0") < value < Decimal("1000000000000")):
            return None
        return f"{value:.2f}"
    except InvalidOperation:
        return None


def chinese_amount(text: str) -> str | None:
    match = CHINESE_AMOUNT_PATTERN.search(text)
    if not match:
        return None
    raw = match.group(1).replace("圆", "元").replace("正", "整")
    try:
        yuan, _, fraction = raw.partition("元")
        number = Decimal(str(cn2an.cn2an(yuan, "smart")))
        jiao = re.search(r"([零壹贰叁肆伍陆柒捌玖])角", fraction)
        fen = re.search(r"([零壹贰叁肆伍陆柒捌玖])分", fraction)
        digits = "零壹贰叁肆伍陆柒捌玖"
        if jiao: number += Decimal(digits.index(jiao[1])) / 10
        if fen: number += Decimal(digits.index(fen[1])) / 100
        return f"{number:.2f}"
    # Mobile recognition can produce syntactically plausible but invalid unit
    # sequences. cn2an may raise KeyError/IndexError for those inputs; an
    # invalid candidate must never abort the whole page.
    except Exception:
        return None


class FieldExtractor:
    def __init__(self, rules: dict, threshold: float = 0.72) -> None:
        self.rules = rules["common"]
        self.threshold = threshold

    @staticmethod
    def _nearby(lines: list[OCRLine], keyword_index: int) -> list[tuple[int, OCRLine, float]]:
        key = lines[keyword_index]
        kx, ky = key.center
        result = []
        for i, line in enumerate(lines):
            x, y = line.center
            same = abs(y - ky) <= 40 and x >= kx - 30
            # Search both above and below. On a 180-degree scan, the value that
            # appears after the keyword in reading order is physically above the label.
            neighbor_line = 0 < abs(y - ky) <= 110 and abs(x - kx) <= 900
            if i == keyword_index or same or neighbor_line:
                result.append((i, line, 0.12 if same else (0.06 if neighbor_line else 0.0)))
        return result

    def _keyword_candidates(self, lines, keywords, parser, excludes=()) -> list[Candidate]:
        result: list[Candidate] = []
        for i, line in enumerate(lines):
            if not any(keyword in line.text for keyword in keywords) or any(keyword in line.text for keyword in excludes):
                continue
            nearby_items = self._nearby(lines, i)
            # OCR reading order is a safe fallback when inverted coordinates
            # prevent the usual spatial relation from matching.
            seen_indexes = {item[0] for item in nearby_items}
            for offset, bonus in ((1, 0.08), (2, 0.03)):
                index = i + offset
                if index < len(lines) and index not in seen_indexes:
                    nearby_items.append((index, lines[index], bonus))
            for index, nearby, bonus in nearby_items:
                if any(keyword in nearby.text for keyword in excludes):
                    continue
                value = parser(nearby.text)
                if value:
                    result.append(Candidate(value, min(0.99, nearby.confidence + bonus), f"关键词:{line.text}", index))
        unique: dict[str, Candidate] = {}
        for item in result:
            if item.value not in unique or unique[item.value].confidence < item.confidence:
                unique[item.value] = item
        return sorted(unique.values(), key=lambda candidate: candidate.confidence, reverse=True)

    @staticmethod
    def _box_size(line: OCRLine) -> tuple[float, float]:
        xs = [point[0] for point in line.box]
        ys = [point[1] for point in line.box]
        return max(xs) - min(xs), max(ys) - min(ys)

    @staticmethod
    def _box_bounds(line: OCRLine) -> tuple[float, float, float, float]:
        xs = [point[0] for point in line.box]
        ys = [point[1] for point in line.box]
        return min(xs), min(ys), max(xs), max(ys)

    @staticmethod
    def _label_compact(text: str) -> str:
        """Normalize common OCR traditional/full-width label variants."""
        translated = text.translate(str.maketrans({
            "戶": "户", "帳": "账", "賬": "账", "號": "号", "稱": "称", "匯": "汇",
        }))
        return re.sub(r"[\s：:;；（）()★*]", "", translated)

    @classmethod
    def _contains_keyword(cls, text: str, keyword: str) -> bool:
        return cls._label_compact(keyword) in cls._label_compact(text)

    @classmethod
    def _keyword_tail(cls, text: str, keyword: str) -> str:
        variants = {
            "户": "[户戶]", "账": "[账帳賬]", "称": "[称稱]", "汇": "[汇匯]",
        }
        pattern = "".join(variants.get(char, re.escape(char)) for char in keyword)
        match = re.search(pattern, text)
        return text[match.end():] if match else ""

    @classmethod
    def _is_field_label(cls, text: str) -> bool:
        compact = cls._label_compact(text)
        exact = {
            "付", "收", "汇", "款", "人", "付款", "付款人", "收款", "收款人", "户名", "名称", "账号", "帐号", "账户",
            "交易币种", "币种", "人民币", "金额", "小写", "大写", "写", "手续费", "交易时间",
            "交易日期", "日期", "摘要", "附言", "流水号", "交易流水号", "对方机构名",
        }
        if compact in exact:
            return True
        label_tokens = ("户名", "账号", "帐号", "账户", "币种", "金额", "手续费", "收款", "付款", "日期", "时间", "摘要", "附言", "流水号")
        return len(compact) <= 12 and any(token in compact for token in label_tokens)

    @classmethod
    def _is_institution_field(cls, text: str, role: str | None = None) -> bool:
        """Return whether a label describes a party's bank instead of its name.

        ``付款人开户行`` contains the shorter configured keyword ``付款人``.
        Institution labels therefore have to win before ordinary party-keyword
        matching; otherwise the bank name receives a high OCR confidence and is
        incorrectly promoted to a party name.
        """
        compact = cls._label_compact(text)
        institution_tokens = ("开户行", "开户银行", "开户机构", "开户网点", "开户支行")
        if not any(token in compact for token in institution_tokens):
            return False
        if role is None:
            return True
        role_tokens = (
            ("付款人", "付款方", "汇款人", "转出方")
            if role == "payer"
            else ("收款人", "收款方", "被收款人", "转入方")
        )
        return any(token in compact for token in role_tokens)

    @classmethod
    def _is_non_name_party_field(cls, text: str) -> bool:
        """Reject party account/bank metadata before shorter role matching."""
        compact = cls._label_compact(text)
        if cls._is_institution_field(text):
            return True
        party_tokens = ("付款人", "付款方", "汇款人", "收款人", "收款方")
        metadata_tokens = ("账号", "帐号", "账户", "卡号", "开户", "行号", "联行号", "银行", "地址", "电话")
        name_tokens = ("户名", "名称")
        return (
            any(token in compact for token in party_tokens)
            and any(token in compact for token in metadata_tokens)
            and not any(token in compact for token in name_tokens)
        )

    @staticmethod
    def _clean_party_value(text: str) -> str:
        """Remove an optional (possibly OCR-truncated) party-name label."""
        value = text.strip(" ：:;；")
        return re.sub(
            r"^(?:(?:付款|收款|汇款)?人)?(?:户名|戶名|名称|名稱)[：:\s]*",
            "",
            value,
        ).strip(" ：:;；")

    def _looks_like_party_value(self, text: str, role: str) -> bool:
        value = text.strip(" ：:;；")
        compact = self._compact(value)
        if not 2 <= len(compact) <= 80 or self._is_field_label(value):
            return False
        opposite_key = "payee_keywords" if role == "payer" else "payer_keywords"
        if any(self._contains_keyword(value, keyword) for keyword in self.rules[opposite_key]):
            return False
        # A company candidate must never contain a table label between name
        # fragments. This is the final safety net when OCR returns 戶名 instead
        # of 户名 or when a cell boundary is otherwise missed.
        embedded_labels = (
            "户名", "账号", "帐号", "账户", "开户行", "开户银行", "开户机构", "开户网点",
            "付款人", "付款方",
            "收款人", "收款方", "交易币种", "交易金额",
        )
        normalized = self._label_compact(value)
        if any(self._label_compact(label) in normalized for label in embedded_labels):
            return False
        if re.fullmatch(r"[\d\s.,，、/\\_-]+", value):
            return False
        if normalize_date(value) or normalize_amount(value):
            return False
        return True

    def _looks_like_party_fragment(self, text: str, role: str) -> bool:
        """Accept a short continuation such as the final ``司`` in a wrapped name."""
        value = text.strip(" ：:;；")
        compact = self._compact(value)
        if not compact or len(compact) > 80 or self._is_field_label(value):
            return False
        opposite_key = "payee_keywords" if role == "payer" else "payer_keywords"
        if any(self._contains_keyword(value, keyword) for keyword in self.rules[opposite_key]):
            return False
        if re.fullmatch(r"[\d\s.,，、/\\_-]+", value):
            return False
        if normalize_date(value) or normalize_amount(value):
            return False
        return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", value))

    def _party_anchors(
        self, lines: list[OCRLine], role: str
    ) -> list[tuple[int, str, OCRLine, set[int]]]:
        """Return ordinary and OCR-fragmented payer/payee labels.

        Lightweight detection commonly returns a vertical party label as two
        boxes, for example ``付款`` + ``人``. Reconstruct only well-aligned
        adjacent fragments and keep the opposite party out of this role.
        """
        if role == "payer":
            keys = self.rules["payer_keywords"]
            opposites = self.rules["payee_keywords"]
            stems = {"付款", "汇款"}
        else:
            keys = self.rules["payee_keywords"]
            opposites = self.rules["payer_keywords"]
            stems = {"收款"}
        anchors: list[tuple[int, str, OCRLine, set[int]]] = []
        for index, line in enumerate(lines):
            normalized_text = self._label_compact(line.text)
            # Longer, more specific bank-field labels must take precedence over
            # their embedded ``付款人`` / ``收款人`` substrings.
            if self._is_non_name_party_field(line.text):
                continue
            if any(self._label_compact(key) in normalized_text for key in opposites):
                continue
            key = next(
                (key for key in keys if self._label_compact(key) in normalized_text),
                None,
            )
            if key:
                # Ignore explanatory footer sentences such as “如果您是收款方”.
                if normalized_text.find(self._label_compact(key)) > 2:
                    continue
                anchors.append((index, key, line, {index}))

        suffixes = {"人", "方", "单位"}
        for index, line in enumerate(lines):
            stem = self._label_compact(line.text)
            if stem not in stems:
                continue
            x, y = line.center
            width, height = self._box_size(line)
            matches: list[tuple[float, int, OCRLine, str]] = []
            for other_index, other in enumerate(lines):
                if other_index == index:
                    continue
                suffix = self._label_compact(other.text)
                normalized_keys = {self._label_compact(key) for key in keys}
                if suffix not in suffixes or stem + suffix not in normalized_keys:
                    continue
                other_x, other_y = other.center
                other_width, other_height = self._box_size(other)
                vertical = (
                    abs(other_x - x) <= max(45.0, (width + other_width) * .65)
                    and 0 < other_y - y <= max(120.0, height * 2.8)
                )
                horizontal = (
                    abs(other_y - y) <= max(35.0, (height + other_height) * .55)
                    and 0 < other_x - x <= max(150.0, width * 3.0)
                )
                if vertical or horizontal:
                    matches.append((abs(other_x - x) + abs(other_y - y), other_index, other, suffix))
            if not matches:
                continue
            _, other_index, other, suffix = min(matches, key=lambda item: item[0])
            x0, y0, x1, y1 = self._box_bounds(line)
            ox0, oy0, ox1, oy1 = self._box_bounds(other)
            combined = OCRLine(
                stem + suffix,
                [
                    [min(x0, ox0), min(y0, oy0)],
                    [max(x1, ox1), min(y0, oy0)],
                    [max(x1, ox1), max(y1, oy1)],
                    [min(x0, ox0), max(y1, oy1)],
                ],
                min(line.confidence, other.confidence),
            )
            anchors.append((index, stem + suffix, combined, {index, other_index}))
        for stem in sorted(stems):
            key = stem + "人"
            if key not in keys:
                continue
            fragments = [
                [(index, line) for index, line in enumerate(lines)
                 if line.box and self._label_compact(line.text) == character]
                for character in key
            ]
            for first_index, first in fragments[0]:
                paths = [
                    ((first_index, first), middle, last)
                    for middle in fragments[1]
                    if self._vertical_party_neighbors(first, middle[1])
                    for last in fragments[2]
                    if self._vertical_party_neighbors(middle[1], last[1])
                    and abs(last[1].center[0] - first.center[0]) <= max(9.0, self._box_size(first)[0] * .9)
                    and .45 <= (last[1].center[1] - middle[1].center[1])
                    / (middle[1].center[1] - first.center[1]) <= 2.2
                ]
                if len(paths) != 1:
                    continue
                chain = paths[0]
                x0 = min(self._box_bounds(item)[0] for _, item in chain)
                y0 = min(self._box_bounds(item)[1] for _, item in chain)
                x1 = max(self._box_bounds(item)[2] for _, item in chain)
                y1 = max(self._box_bounds(item)[3] for _, item in chain)
                combined = OCRLine(
                    key, [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                    min(item.confidence for _, item in chain),
                )
                anchors.append((first_index, key, combined, {index for index, _ in chain}))
        return anchors

    def _vertical_party_neighbors(self, upper: OCRLine, lower: OCRLine) -> bool:
        uw, uh = self._box_size(upper)
        lw, lh = self._box_size(lower)
        dx = abs(lower.center[0] - upper.center[0])
        dy = lower.center[1] - upper.center[1]
        return dx <= max(8.0, (uw + lw) * .45) and max(3.0, min(uh, lh) * .45) <= dy <= max(15.0, max(uh, lh) * 2.8)

    def _institution_row_party_candidates(
        self, lines: list[OCRLine], role: str
    ) -> list[Candidate]:
        """Recover a name from the same row as a role-specific bank label.

        Low-resolution mobile OCR sometimes drops ``付款人户名`` completely or
        recognizes ``收款人户名`` as merely ``人户名``. On statement layouts the
        party name and ``付款人/收款人开户行`` remain on the same row. Use that
        coordinate relationship as a conservative fallback and cap semantic
        confidence so it is never presented as a 99% exact-label match.
        """
        result: list[Candidate] = []
        for bank_index, bank_line in enumerate(lines):
            if not self._is_institution_field(bank_line.text, role):
                continue
            bank_x, bank_y = bank_line.center
            _, bank_height = self._box_size(bank_line)
            row_tolerance = max(38.0, bank_height * 1.15)
            candidates: list[tuple[float, int, OCRLine, str]] = []
            for candidate_index, candidate in enumerate(lines):
                if candidate_index == bank_index or candidate.center[0] >= bank_x:
                    continue
                vertical_gap = abs(candidate.center[1] - bank_y)
                if vertical_gap > row_tolerance:
                    continue
                value = self._clean_party_value(candidate.text)
                if not self._looks_like_party_value(value, role):
                    continue
                horizontal_gap = bank_x - candidate.center[0]
                if horizontal_gap > 1500:
                    continue
                candidates.append((vertical_gap + horizontal_gap * .01, candidate_index, candidate, value))
            if not candidates:
                continue
            _, candidate_index, candidate, value = min(candidates, key=lambda item: item[0])
            confidence = min(.82, max(.55, candidate.confidence * .84))
            result.append(Candidate(value, confidence, "开户行同排户名回退", candidate_index))
        return result

    def _combine_party_cell(
        self,
        lines: list[OCRLine],
        name_label_index: int,
        name_label: OCRLine,
        anchor_indexes: set[int],
        key: str,
        key_height: float,
        role: str,
        column_end: float = float("inf"),
    ) -> Candidate | None:
        """Reconstruct all OCR boxes inside one party-name table cell."""
        label_x, label_y = name_label.center
        _, _, label_right, _ = self._box_bounds(name_label)
        _, label_height = self._box_size(name_label)
        row_tolerance = max(52.0, label_height * 1.35, key_height * .36)

        # A field label to the right marks the start of the payee column.
        right_labels = [
            candidate.center[0]
            for candidate_index, candidate in enumerate(lines)
            if candidate_index != name_label_index
            and candidate.center[0] > label_x
            and abs(candidate.center[1] - label_y) <= row_tolerance
            and self._is_field_label(candidate.text)
        ]
        right_boundary = min(column_end, min(right_labels) if right_labels else label_x + 1000)

        # The next same-column label (normally 账号) closes the name row. This
        # prevents a wrapped name from absorbing the following account value.
        lower_labels = [
            candidate.center[1]
            for candidate_index, candidate in enumerate(lines)
            if candidate_index != name_label_index
            and candidate_index not in anchor_indexes
            and candidate.center[1] > label_y + label_height * .45
            and abs(candidate.center[0] - label_x) <= max(180.0, label_right - label_x + 100.0)
            and self._is_field_label(candidate.text)
        ]
        lower_boundary = label_y + row_tolerance
        if lower_labels:
            lower_boundary = min(lower_boundary, min(lower_labels) - max(7.0, label_height * .18))

        fragments = [
            (candidate_index, candidate)
            for candidate_index, candidate in enumerate(lines)
            if candidate_index not in anchor_indexes | {name_label_index}
            and candidate.center[0] > label_right - 10
            and candidate.center[0] < right_boundary
            and label_y - row_tolerance <= candidate.center[1] <= lower_boundary
            and self._looks_like_party_fragment(candidate.text, role)
        ]
        if not fragments or not any(
            self._looks_like_party_value(line.text, role) for _, line in fragments
        ):
            return None

        heights = [max(1.0, self._box_size(line)[1]) for _, line in fragments]
        row_gap = max(12.0, median(heights) * .55)
        rows: list[list[tuple[int, OCRLine]]] = []
        for item in sorted(fragments, key=lambda entry: (entry[1].center[1], entry[1].center[0])):
            row = next(
                (
                    row
                    for row in rows
                    if abs(median([member.center[1] for _, member in row]) - item[1].center[1]) <= row_gap
                ),
                None,
            )
            if row is None:
                rows.append([item])
            else:
                row.append(item)
        rows.sort(key=lambda row: median([member.center[1] for _, member in row]))
        ordered = [item for row in rows for item in sorted(row, key=lambda entry: entry[1].center[0])]
        value = "".join(line.text.strip(" ：:;；") for _, line in ordered)
        if not self._looks_like_party_value(value, role):
            return None
        total_chars = sum(max(1, len(self._compact(line.text))) for _, line in ordered)
        confidence = sum(
            line.confidence * max(1, len(self._compact(line.text))) for _, line in ordered
        ) / total_chars
        source = f"关键词:{key}" + ("(跨行户名)" if len(rows) > 1 else "")
        return Candidate(value, min(.99, confidence + .12), source, ordered[0][0])

    @staticmethod
    def _payer_name_incomplete(value: str) -> bool:
        compact = re.sub(r"[\s：:;；（）()]+", "", value)
        incomplete_suffixes = (
            "有限公", "股份有限", "股份有", "集团有限", "集团有", "分公", "合作",
        )
        return any(compact.endswith(suffix) for suffix in incomplete_suffixes)

    def _payer_institution_marker(self, value: str) -> str | None:
        """Return the marker when a payer looks like a bank/branch field."""
        compact = re.sub(r"[\s：:;；（）()]+", "", value)
        defaults = {
            "keywords": (
                "支行", "分行", "营业部", "信用社", "农商行", "村镇银行",
                "农村商业银行", "信用合作联社",
            ),
            "suffixes": (
                "银行", "银行股份有限公司", "银行有限责任公司", "信用合作联社",
            ),
        }
        configured = self.rules.get("payer_institution_review", {})
        keywords = configured.get("keywords", defaults["keywords"])
        suffixes = configured.get("suffixes", defaults["suffixes"])
        for marker in keywords:
            if marker and marker in compact:
                return str(marker)
        for marker in suffixes:
            if marker and compact.endswith(marker):
                return str(marker)
        return None

    def _party_candidates(self, lines: list[OCRLine], role: str) -> list[Candidate]:
        result: list[Candidate] = []
        anchors = self._party_anchors(lines, role)
        opposite = self._party_anchors(lines, "payee" if role == "payer" else "payer")
        for i, key, line, anchor_indexes in anchors:
            original_text = lines[i].text
            inline = self._keyword_tail(original_text, key)
            inline = self._clean_party_value(inline)
            if inline and self._looks_like_party_value(inline, role):
                result.append(Candidate(inline, min(.99, line.confidence + .12), f"关键词:{key}", i))
                continue

            key_x, key_y = line.center
            _, key_height = self._box_size(line)
            column_end = min(
                (self._box_bounds(other)[0] for _, _, other, _ in opposite
                 if self._box_bounds(other)[0] > key_x
                 and abs(other.center[1] - key_y) <= max(42, key_height * .65)),
                default=float("inf"),
            )
            # In bank tables the payer label is often vertical, followed by a
            # separate "户名" cell. Bind the value to the nearest such field
            # label instead of accepting every OCR box in a broad rectangle.
            name_labels: list[tuple[int, OCRLine]] = []
            for j, candidate in enumerate(lines):
                compact = self._label_compact(candidate.text)
                if compact not in {"户名", "名称"}:
                    continue
                x, y = candidate.center
                if key_x < x < column_end and x - key_x <= 650 and abs(y - key_y) <= max(75, key_height * .55):
                    name_labels.append((j, candidate))

            if name_labels:
                label_index, name_label = min(
                    name_labels,
                    key=lambda item: (item[1].center[0] - key_x, abs(item[1].center[1] - key_y)),
                )
                combined = self._combine_party_cell(
                    lines, label_index, name_label, anchor_indexes, key, key_height, role, column_end
                )
                if combined is not None:
                    result.append(combined)
                continue

            # Three fragments identify a table column, so require its name cell.
            if len(anchor_indexes) == 3:
                continue
            same_row = [
                (j, candidate, self._clean_party_value(candidate.text))
                for j, candidate in enumerate(lines)
                if j not in anchor_indexes
                and key_x < candidate.center[0] < column_end
                and candidate.center[0] - key_x <= 1000
                and abs(candidate.center[1] - key_y) <= max(48, key_height * .55)
                and self._looks_like_party_value(self._clean_party_value(candidate.text), role)
            ]
            if same_row:
                j, candidate_line, value = min(same_row, key=lambda item: item[1].center[0] - key_x)
                result.append(Candidate(value, min(.86, candidate_line.confidence), f"关键词:{key}(同排回退)", j))
        result.extend(self._institution_row_party_candidates(lines, role))
        unique = {candidate.value: candidate for candidate in sorted(result, key=lambda candidate: candidate.confidence)}
        return sorted(unique.values(), key=lambda candidate: candidate.confidence, reverse=True)

    def _payer_candidates(self, lines: list[OCRLine]) -> list[Candidate]:
        result = self._party_candidates(lines, "payer")
        text = self._compact("".join(line.text for line in lines)).lower()
        if "贷记通知" in text or "creditadvice" in text:
            result.extend(self._column_value_candidates(
                lines, ("交易对方名字", "counterpartyname"), "payer"
            ))
        elif "借记通知" in text or "debitadvice" in text:
            result.extend(self._column_value_candidates(
                lines, ("客户名称", "customername"), "payer"
            ))
        return self._dedupe_party_candidates(result)

    def _payee_candidates(self, lines: list[OCRLine]) -> list[Candidate]:
        result = self._party_candidates(lines, "payee")
        text = self._compact("".join(line.text for line in lines)).lower()
        if "贷记通知" in text or "creditadvice" in text:
            result.extend(self._column_value_candidates(
                lines, ("客户名称", "customername"), "payee"
            ))
        elif "借记通知" in text or "debitadvice" in text:
            result.extend(self._column_value_candidates(
                lines, ("交易对方名字", "counterpartyname"), "payee"
            ))
        return self._dedupe_party_candidates(result)

    @staticmethod
    def _dedupe_party_candidates(candidates: list[Candidate]) -> list[Candidate]:
        unique: dict[str, Candidate] = {}
        for candidate in candidates:
            current = unique.get(candidate.value)
            if current is None or candidate.confidence > current.confidence:
                unique[candidate.value] = candidate
        return sorted(unique.values(), key=lambda candidate: candidate.confidence, reverse=True)

    def _column_value_candidates(
        self,
        lines: list[OCRLine],
        labels: tuple[str, ...],
        role: str,
    ) -> list[Candidate]:
        """Read a value directly below a bilingual statement column label."""
        result: list[Candidate] = []
        compact_labels = tuple(self._label_compact(label).lower() for label in labels)
        for index, label_line in enumerate(lines):
            compact = self._label_compact(label_line.text).lower()
            if not any(label in compact for label in compact_labels):
                continue
            label_x, label_y = label_line.center
            label_width, label_height = self._box_size(label_line)
            candidates = [
                (candidate_index, candidate)
                for candidate_index, candidate in enumerate(lines)
                if candidate_index != index
                and not any(
                    target in self._label_compact(candidate.text).lower()
                    for target in compact_labels
                )
                and (
                    (
                        candidate.center[0] > label_x
                        and candidate.center[0] - label_x <= 1200
                        and abs(candidate.center[1] - label_y) <= max(48.0, label_height * .75)
                    )
                    or (
                        0 < candidate.center[1] - label_y <= max(145.0, label_height * 5.0)
                        and abs(candidate.center[0] - label_x) <= max(300.0, label_width * .85)
                    )
                )
                and self._looks_like_party_value(candidate.text, role)
            ]
            if not candidates:
                continue
            candidate_index, candidate = min(
                candidates,
                key=lambda item: (
                    item[1].center[1] - label_y,
                    abs(item[1].center[0] - label_x),
                ),
            )
            result.append(Candidate(
                candidate.text.strip(" ：:;；"),
                min(.99, candidate.confidence + .12),
                f"关键词:{label_line.text}",
                candidate_index,
            ))
        return result

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", text)

    def _uppercase_amount_candidates(self, lines: list[OCRLine]) -> tuple[list[Candidate], bool]:
        result: list[Candidate] = []
        invalid_candidate = False
        excluded = self.rules["amount_exclude_keywords"]
        positive_labels = [
            (index, line) for index, line in enumerate(lines)
            if any(keyword in line.text for keyword in self.rules["amount_keywords"])
            and not any(keyword in line.text for keyword in excluded)
        ]
        excluded_labels = [
            (index, line) for index, line in enumerate(lines)
            if any(keyword in line.text for keyword in excluded)
        ]
        for index, line in enumerate(lines):
            value = chinese_amount(line.text)
            has_amount_text = CHINESE_AMOUNT_PATTERN.search(line.text) is not None
            if any(keyword in line.text for keyword in excluded):
                continue
            # A page may contain transaction amount, fee amount and balance in
            # Chinese uppercase text. Associate the value with its closest row
            # label; values belonging to fee/balance rows are not candidates.
            associations: list[tuple[float, bool]] = []
            value_x, value_y = line.center
            _, value_height = self._box_size(line)
            for _, label in positive_labels:
                label_x, label_y = label.center
                _, label_height = self._box_size(label)
                tolerance = max(55, value_height * 1.3, label_height * 1.3)
                if abs(value_y - label_y) <= 120 and abs(value_x - label_x) <= 1500:
                    score = abs(value_y - label_y) / tolerance + abs(value_x - label_x) / 1500
                    associations.append((score, True))
            for _, label in excluded_labels:
                label_x, label_y = label.center
                _, label_height = self._box_size(label)
                tolerance = max(55, value_height * 1.3, label_height * 1.3)
                if abs(value_y - label_y) <= 120 and abs(value_x - label_x) <= 1500:
                    score = abs(value_y - label_y) / tolerance + abs(value_x - label_x) / 1500
                    associations.append((score, False))
            positive_context = (
                min(associations, key=lambda item: item[0])[1]
                if associations else "人民币" in line.text
            )
            if has_amount_text and value is None and positive_context:
                invalid_candidate = True
                continue
            if not value or Decimal(value) <= 0:
                continue
            if associations:
                if not positive_context:
                    continue
            elif "人民币" not in line.text:
                continue
            result.append(Candidate(value, min(.99, line.confidence + .03), "中文大写金额", index))

        # PaddleOCR may return "人民币" / "（大写）" and the Chinese amount as
        # separate boxes, or split a long amount across adjacent boxes. Combine
        # short reading-order windows, but only when their coordinates are close.
        for start in range(len(lines)):
            for size in (2, 3):
                window = lines[start:start + size]
                if len(window) != size:
                    continue
                centers = [line.center for line in window]
                nearby = all(
                    abs(centers[index][1] - centers[index + 1][1]) <= 120
                    or abs(centers[index][0] - centers[index + 1][0]) <= 120
                    for index in range(len(centers) - 1)
                )
                joined = "".join(line.text for line in window)
                anchored = "人民币" in joined or any(
                    keyword in joined for keyword in self.rules["amount_keywords"]
                )
                if (not nearby or not anchored
                        or any(keyword in joined for keyword in excluded)):
                    continue
                value = chinese_amount(joined)
                if CHINESE_AMOUNT_PATTERN.search(joined) and value is None:
                    invalid_candidate = True
                    continue
                if value and Decimal(value) > 0:
                    confidence = min(line.confidence for line in window)
                    result.append(Candidate(value, min(.99, confidence + .05), "跨行中文大写金额", start))
        return self._dedupe_amounts(result), invalid_candidate

    @staticmethod
    def _dedupe_amounts(candidates: list[Candidate]) -> list[Candidate]:
        priority = {"金额表格坐标重组": 3, "跨行中文大写金额": 2, "中文大写金额": 1}
        unique: dict[str, Candidate] = {}
        for item in candidates:
            previous = unique.get(item.value)
            item_rank = (item.confidence, priority.get(item.source, 0))
            previous_rank = (previous.confidence, priority.get(previous.source, 0)) if previous else (-1.0, -1)
            if item_rank > previous_rank:
                unique[item.value] = item
        return sorted(
            unique.values(),
            key=lambda item: (item.confidence, priority.get(item.source, 0)),
            reverse=True,
        )

    def _grid_amount_candidates(self, lines: list[OCRLine]) -> list[Candidate]:
        result: list[Candidate] = []
        excluded = self.rules["amount_exclude_keywords"]
        labels = [
            (index, line) for index, line in enumerate(lines)
            if any(keyword in line.text for keyword in self.rules["amount_keywords"])
            and not any(keyword in line.text for keyword in excluded)
        ]
        symbols = {"元", "圆", "角", "分"}
        for label_index, label in labels:
            label_y = label.center[1]
            nearby_symbols = [
                line for line in lines
                if self._compact(line.text) in symbols and abs(line.center[1] - label_y) <= 100
            ]
            yuan_lines = [line for line in nearby_symbols if self._compact(line.text) in {"元", "圆"}]
            jiao_lines = [line for line in nearby_symbols if self._compact(line.text) == "角"]
            fen_lines = [line for line in nearby_symbols if self._compact(line.text) == "分"]
            triples = [
                (yuan, jiao, fen)
                for yuan in yuan_lines for jiao in jiao_lines for fen in fen_lines
                if yuan.center[0] < jiao.center[0] < fen.center[0]
            ]
            if not triples:
                continue
            yuan, jiao, fen = min(
                triples,
                key=lambda group: abs(group[1].center[0] - group[0].center[0] - (group[2].center[0] - group[1].center[0])),
            )
            gaps = (jiao.center[0] - yuan.center[0], fen.center[0] - jiao.center[0])
            spacing = median(gaps)
            if spacing < 12 or max(gaps) > min(gaps) * 1.65:
                continue

            digit_lines = [
                line for line in lines
                if re.fullmatch(r"\d", self._compact(line.text))
                and abs(line.center[1] - yuan.center[1]) <= 100
                and yuan.center[0] - spacing * 10.5 <= line.center[0] <= fen.center[0] + spacing * .6
            ]
            if len(digit_lines) < 3:
                continue
            # Keep the dominant horizontal digit row; unrelated isolated digits
            # elsewhere on a dense form must not participate in the amount.
            rows: list[list[OCRLine]] = []
            for digit in sorted(digit_lines, key=lambda item: item.center[1]):
                row = next((row for row in rows if abs(median([x.center[1] for x in row]) - digit.center[1]) <= 22), None)
                if row is None:
                    rows.append([digit])
                else:
                    row.append(digit)
            row = max(rows, key=len)
            if len(row) < 3:
                continue

            positioned: dict[int, OCRLine] = {}
            for digit in row:
                position = int(round((digit.center[0] - yuan.center[0]) / spacing))
                expected_x = yuan.center[0] + position * spacing
                if position < -10 or position > 2 or abs(digit.center[0] - expected_x) > spacing * .42:
                    continue
                previous = positioned.get(position)
                if previous is None or previous.confidence < digit.confidence:
                    positioned[position] = digit
            integer_positions = [position for position in positioned if position <= 0]
            if not integer_positions:
                continue
            first = min(integer_positions)
            integer_text = "".join(self._compact(positioned[pos].text) if pos in positioned else "0" for pos in range(first, 1))
            fraction_text = "".join(self._compact(positioned[pos].text) if pos in positioned else "0" for pos in (1, 2))
            try:
                value = Decimal(integer_text + "." + fraction_text)
            except InvalidOperation:
                continue
            if value <= 0:
                continue
            confidence = min(.99, sum(line.confidence for line in positioned.values()) / len(positioned))
            result.append(Candidate(f"{value:.2f}", confidence, "金额表格坐标重组", label_index))
        return self._dedupe_amounts(result)

    def _amount_candidates(self, lines: list[OCRLine]) -> tuple[list[Candidate], list[Candidate], bool]:
        result: list[Candidate] = []
        excludes = self.rules["amount_exclude_keywords"]
        for i, line in enumerate(lines):
            if not any(keyword in line.text for keyword in self.rules["amount_keywords"]):
                continue
            if any(keyword in line.text for keyword in excludes):
                continue
            label_x, label_y = line.center
            _, label_height = self._box_size(line)
            nearby_items = [
                (index, nearby, .12 if index != i else 0.0)
                for index, nearby in enumerate(lines)
                if index == i or (
                    nearby.center[0] >= label_x - 30
                    and nearby.center[0] - label_x <= 1000
                    # Bilingual statements often put the numeric value on the
                    # next row roughly 50-65 px below the Chinese header.
                    and abs(nearby.center[1] - label_y) <= max(70, label_height * 1.5)
                )
            ]
            for index, nearby, bonus in nearby_items:
                if any(keyword in nearby.text for keyword in excludes):
                    continue
                compact = self._compact(nearby.text).replace(",", "")
                # A digit occupying one table cell is not a monetary value.
                if re.fullmatch(r"\d{1,2}", compact):
                    continue
                if re.fullmatch(r"(?:19|20)\d{2}", compact) or re.fullmatch(r"\d{8,}", compact):
                    continue
                if normalize_date(nearby.text):
                    continue
                value = normalize_amount(nearby.text)
                if value:
                    result.append(Candidate(value, min(.99, nearby.confidence + bonus), f"关键词:{line.text}", index))

        uppercase, invalid_uppercase = self._uppercase_amount_candidates(lines)
        result.extend(self._grid_amount_candidates(lines))
        result.extend(uppercase)
        return self._dedupe_amounts(result), uppercase, invalid_uppercase

    def extract(self, lines: list[OCRLine]) -> ExtractedFields:
        fields = ExtractedFields()
        primary_date_keys = self.rules.get("primary_date_keywords", ["交易时间"])
        primary_dates = self._keyword_candidates(lines, primary_date_keys, normalize_date)
        fallback_dates = self._keyword_candidates(lines, self.rules["date_keywords"], normalize_date)
        primary_values = {candidate.value for candidate in primary_dates}
        fields.date_candidates = primary_dates + [candidate for candidate in fallback_dates if candidate.value not in primary_values]
        fields.payer_candidates = self._payer_candidates(lines)
        fields.payee_candidates = self._payee_candidates(lines)
        fields.amount_candidates, uppercase, invalid_uppercase = self._amount_candidates(lines)
        if fields.date_candidates:
            fields.date, fields.date_confidence = fields.date_candidates[0].value, fields.date_candidates[0].confidence
        if fields.payer_candidates:
            fields.payer, fields.payer_confidence = fields.payer_candidates[0].value, fields.payer_candidates[0].confidence
        if fields.payee_candidates:
            fields.payee, fields.payee_confidence = fields.payee_candidates[0].value, fields.payee_candidates[0].confidence
        if fields.amount_candidates:
            fields.amount, fields.amount_confidence = fields.amount_candidates[0].value, fields.amount_candidates[0].confidence
        for label, value, confidence in (
            ("日期", fields.date, fields.date_confidence),
            ("付款人", fields.payer, fields.payer_confidence),
            ("金额", fields.amount, fields.amount_confidence),
        ):
            if not value: fields.review_reasons.append(f"{label}缺失")
            elif confidence < self.threshold: fields.review_reasons.append(f"{label}置信度低")
        if len(fields.amount_candidates) > 1:
            fields.review_reasons.append("找到多个金额候选")
        if len(fields.payer_candidates) > 1:
            fields.review_reasons.append("找到多个付款人候选")
        if len(fields.payee_candidates) > 1:
            fields.review_reasons.append("找到多个收款人候选")
        if fields.payee and fields.payee_confidence < self.threshold:
            fields.review_reasons.append("收款人置信度低")
        if fields.payer and fields.payee and fields.payer == fields.payee:
            fields.review_reasons.append("付款人与收款人无法区分")
        if fields.payer and self._payer_name_incomplete(fields.payer):
            fields.review_reasons.append("付款人名称疑似截断")
        for party_label, party_value in (("付款人", fields.payer), ("收款人", fields.payee)):
            if party_value:
                institution_marker = self._payer_institution_marker(party_value)
                if institution_marker:
                    fields.review_reasons.append(
                        f"{party_label}疑似银行机构（含“{institution_marker}”），请确认未误取开户行"
                    )
        if fields.payee and self._payer_name_incomplete(fields.payee):
            fields.review_reasons.append("收款人名称疑似截断")
        if uppercase and fields.amount and uppercase[0].value != fields.amount:
            fields.review_reasons.append("大小写金额不一致")
        if uppercase and fields.amount and uppercase[0].value == fields.amount:
            fields.amount_confidence = min(.99, fields.amount_confidence + .08)
        if invalid_uppercase:
            fields.review_reasons.append("中文大写金额格式异常")
        return fields
