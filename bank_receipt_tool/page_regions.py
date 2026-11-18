from __future__ import annotations

import re

from .domain import OCRLine


Rect = tuple[int, int, int, int]
RECEIPT_TITLE = re.compile(
    r"贷记通知|借记通知|creditadvice|debitadvice|结算业务回单|业务回单|电子回单|银行回单",
    re.IGNORECASE,
)


def lines_in_rect(lines: list[OCRLine], rect: Rect) -> list[OCRLine]:
    x, y, width, height = rect
    return [
        OCRLine(line.text, [[px - x, py - y] for px, py in line.box], line.confidence)
        for line in lines
        if line.box and all(x <= px <= x + width and y <= py <= y + height for px, py in line.box)
    ]


def repeated_receipt_regions(lines: list[OCRLine], shape: tuple[int, int]) -> list[Rect]:
    """Find stacked receipts from aligned titles and cuts between OCR boxes."""
    height, width = shape
    titles = []
    for line in lines:
        title = re.sub(r"[\s:：()（）]", "", line.text)
        if line.box and len(title) <= 48 and RECEIPT_TITLE.search(title):
            titles.append((min(p[1] for p in line.box), line.center[0]))

    rows: list[list[tuple[float, float]]] = []
    for title in sorted(titles):
        if not rows or title[0] - rows[-1][0][0] > height * .025:
            rows.append([])
        rows[-1].append(title)
    if not 2 <= len(rows) <= 6 or rows[0][0][0] > height * .25:
        return []
    if not any(
        all(any(abs(other_x - x) <= width * .20 for _, other_x in row) for row in rows[1:])
        for _, x in rows[0]
    ):
        return []

    text_ranges = [
        (min(p[1] for p in line.box), max(p[1] for p in line.box))
        for line in lines if line.box
    ]
    cuts = [0]
    for row in rows[1:]:
        title_y = int(row[0][0])
        preferred = title_y - int(height * .015)
        nearby = [(top, bottom) for top, bottom in text_ranges
                  if bottom >= title_y - height * .055 and top <= title_y]
        safe = [
            y for y in range(max(1, title_y - int(height * .055)), title_y)
            if all(not top - 2 <= y <= bottom + 2 for top, bottom in nearby)
        ]
        # Keep tentative bands for manual review if text crosses a cut.
        cuts.append(min(safe, key=lambda y: abs(y - preferred)) if safe else preferred)
    cuts.append(height)
    if any(end - start < height * .12 for start, end in zip(cuts, cuts[1:])):
        return []
    return [(0, start, width, end - start) for start, end in zip(cuts, cuts[1:])]


def crosses_region_boundary(lines: list[OCRLine], regions: list[Rect]) -> bool:
    cuts = [y for _, y, _, _ in regions[1:]]
    return any(
        min(p[1] for p in line.box) < cut < max(p[1] for p in line.box)
        for line in lines if line.box and len(line.text.strip()) > 1
        for cut in cuts
    )
