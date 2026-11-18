from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps


@dataclass(slots=True)
class SplitDetection:
    crops: list[tuple[np.ndarray, float]]
    ambiguous: bool = False
    reasons: list[str] = field(default_factory=list)
    suggested_boxes: list[tuple[int, int, int, int]] = field(default_factory=list)


def normalize_rect(
    box: tuple[int, int, int, int], image_shape: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    """Normalize an ``x, y, width, height`` box and clamp it to the image."""
    height, width = image_shape
    x, y, box_width, box_height = box
    x1, x2 = sorted((int(round(x)), int(round(x + box_width))))
    y1, y2 = sorted((int(round(y)), int(round(y + box_height))))
    x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
    y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
    if x1 >= x2 or y1 >= y2:
        return None
    return x1, y1, x2 - x1, y2 - y1


def rotate_rects(
    boxes: list[tuple[int, int, int, int]],
    source_shape: tuple[int, int],
    rotation_degrees: int,
) -> list[tuple[int, int, int, int]]:
    """Rotate suggested crop boxes with the image and keep valid coordinates."""
    source_height, source_width = source_shape
    if rotation_degrees in {90, -90}:
        target_shape = (source_width, source_height)
    else:
        target_shape = source_shape
    transformed: list[tuple[int, int, int, int]] = []
    for raw_box in boxes:
        normalized = normalize_rect(raw_box, source_shape)
        if normalized is None:
            continue
        x, y, box_width, box_height = normalized
        x2, y2 = x + box_width, y + box_height
        if rotation_degrees == 90:
            corners = (source_height - y2, x, source_height - y, x2)
        elif rotation_degrees == -90:
            corners = (y, source_width - x2, y2, source_width - x)
        elif abs(rotation_degrees) == 180:
            corners = (source_width - x2, source_height - y2, source_width - x, source_height - y)
        elif rotation_degrees == 0:
            corners = (x, y, x2, y2)
        else:
            raise ValueError(f"不支持的裁切框旋转角度: {rotation_degrees}")
        x1, y1, new_x2, new_y2 = corners
        rotated = normalize_rect((x1, y1, new_x2 - x1, new_y2 - y1), target_shape)
        if rotated is not None:
            transformed.append(rotated)
    return transformed


def read_image(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        rgb = ImageOps.exif_transpose(im).convert("RGB")
        return cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)


def write_image_atomic(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp" + path.suffix)
    ok, encoded = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise OSError(f"无法编码图像: {path.name}")
    tmp.write_bytes(encoded.tobytes())
    tmp.replace(path)


def detect_orientation(image: np.ndarray) -> int:
    """Estimate whether text baselines are vertical; aspect ratio alone is unsafe."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=80,
        minLineLength=max(50, min(image.shape[:2]) // 8), maxLineGap=20,
    )
    horizontal = vertical = 0.0
    for raw in lines if lines is not None else []:
        x1, y1, x2, y2 = raw[0]
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        length = float(np.hypot(dx, dy))
        if dx > dy * 2:
            horizontal += length
        elif dy > dx * 2:
            vertical += length
    return 90 if vertical > horizontal * 1.25 and vertical > 200 else 0


def deskew(image: np.ndarray) -> tuple[np.ndarray, float]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(inv > 0))
    if len(coords) < 100:
        return image, 0.0
    angle = cv2.minAreaRect(coords[:, ::-1].astype(np.float32))[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) > 12 or abs(angle) < 0.15:
        return image, 0.0
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        image, matrix, (w, h), flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    ), angle


def _quality_metrics(image: np.ndarray) -> tuple[float, float]:
    h, w = image.shape[:2]
    scale = min(1.0, 800 / max(h, w))
    sample = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image
    gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    return float(gray.std()), float(cv2.Laplacian(gray, cv2.CV_64F).var())


def enhance(image: np.ndarray) -> np.ndarray:
    """Fast adaptive enhancement; expensive denoising is reserved for poor scans."""
    contrast, sharpness = _quality_metrics(image)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    light, a, b = cv2.split(lab)
    light = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8)).apply(light)
    enhanced = cv2.cvtColor(cv2.merge((light, a, b)), cv2.COLOR_LAB2BGR)
    if contrast >= 34 and sharpness >= 65:
        return enhanced
    h, w = enhanced.shape[:2]
    scale = min(1.0, 1200 / max(h, w))
    work = cv2.resize(enhanced, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else enhanced
    work = cv2.fastNlMeansDenoisingColored(work, None, 3, 3, 5, 7)
    return cv2.resize(work, (w, h), interpolation=cv2.INTER_CUBIC) if scale < 1 else work


def preprocess(image: np.ndarray) -> tuple[np.ndarray, dict]:
    rotation = detect_orientation(image)
    if rotation == 90:
        image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    image, skew = deskew(image)
    return enhance(image), {"rotation": rotation, "skew": skew}


def _dedupe_boxes(boxes: list[tuple[int, int, int, int, float]], h: int, w: int):
    boxes.sort(key=lambda box: (box[1], box[0]))
    result: list[tuple[int, int, int, int, float]] = []
    for box in boxes:
        x, y, bw, bh, _ = box
        if any(
            abs(x - x2) < w * .03 and abs(y - y2) < h * .03
            and abs(bw - bw2) < w * .05 and abs(bh - bh2) < h * .05
            for x2, y2, bw2, bh2, _ in result
        ):
            continue
        result.append(box)
    return result


def _contour_boxes(image: np.ndarray) -> tuple[list[tuple[int, int, int, int, float]], int]:
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 130)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area = h * w
    boxes: list[tuple[int, int, int, int, float]] = []
    possible = 0
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        ratio = (bw * bh) / area
        if ratio >= .08 and bw > w * .28 and bh > h * .14:
            possible += 1
        if .14 <= ratio <= .96 and bw > w * .35 and bh > h * .18:
            rectangularity = cv2.contourArea(contour) / max(1, bw * bh)
            confidence = min(.96, .55 + ratio * .2 + rectangularity * .25)
            boxes.append((x, y, bw, bh, confidence))
    return _dedupe_boxes(boxes, h, w), possible


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    padded = np.concatenate(([False], mask.astype(bool), [False])).astype(np.int8)
    changes = np.diff(padded)
    starts, ends = np.where(changes == 1)[0], np.where(changes == -1)[0]
    return list(zip(starts.tolist(), ends.tolist()))


def _whitespace_boxes(image: np.ndarray) -> tuple[list[tuple[int, int, int, int]], float]:
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    ink = gray < 235
    choices: list[tuple[float, list[tuple[int, int, int, int]]]] = []
    for axis, length in ((0, h), (1, w)):
        density = ink.mean(axis=1 if axis == 0 else 0)
        kernel = max(5, int(length * .008) | 1)
        smooth = np.convolve(density, np.ones(kernel) / kernel, mode="same")
        for start, end in _true_runs(smooth < .004):
            gap = end - start
            if gap < length * .045 or start < length * .18 or end > length * .82:
                continue
            cut = (start + end) // 2
            if min(cut, length - cut) < length * .22:
                continue
            if axis == 0:
                boxes = [(0, 0, w, cut), (0, cut, w, h - cut)]
                densities = [ink[:cut].mean(), ink[cut:].mean()]
            else:
                boxes = [(0, 0, cut, h), (cut, 0, w - cut, h)]
                densities = [ink[:, :cut].mean(), ink[:, cut:].mean()]
            if min(densities) < .006:
                continue
            choices.append((gap / length, boxes))
    return max(choices, key=lambda item: item[0]) if choices else (0.0, [])


def _horizontal_separator_boxes(
    image: np.ndarray,
) -> tuple[list[tuple[int, int, int, int]], float]:
    """Split stacked receipts at isolated full-width solid/dashed rules.

    Some statement scans place several receipts on one page and separate them
    with a single narrow dashed rule.  Such a rule is deliberately not treated
    as a whitespace band by :func:`_whitespace_boxes`, and the receipts often
    have no enclosing contour.  Detect only long, isolated horizontal peaks;
    the pipeline still OCR-validates every resulting region before accepting
    the split.
    """
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # A conservative fixed threshold keeps pale paper noise out while retaining
    # the dark dashed separators used by scanned bank statements.
    ink = (gray < 215).astype(np.uint8)
    close_width = max(9, int(w * .025))
    connected = cv2.morphologyEx(
        ink, cv2.MORPH_CLOSE, np.ones((1, close_width), np.uint8)
    )
    connected_coverage = connected.mean(axis=1)
    raw_coverage = ink.mean(axis=1)

    candidate_rows: list[tuple[int, float]] = []
    for y in range(int(h * .10), int(h * .90)):
        if connected_coverage[y] < .55 or raw_coverage[y] < .32:
            continue
        outer = max(10, int(h * .008))
        inner = max(3, int(h * .0025))
        above = ink[max(0, y - outer):max(0, y - inner)]
        below = ink[min(h, y + inner):min(h, y + outer)]
        surroundings = np.concatenate((above.ravel(), below.ravel()))
        surrounding_density = float(surroundings.mean()) if surroundings.size else 1.0
        isolation = float(raw_coverage[y] - surrounding_density)
        if isolation >= .20:
            candidate_rows.append((y, isolation))

    if not candidate_rows:
        return [], 0.0

    # A blank gutter between two bordered receipts produces two strong rules.
    # Coalesce nearby peaks and cut at their midpoint.  Dense table grids form
    # large clusters and consequently fail the minimum-region test below.
    cluster_gap = max(12, int(h * .075))
    clusters: list[list[tuple[int, float]]] = []
    for row in candidate_rows:
        if not clusters or row[0] - clusters[-1][-1][0] > cluster_gap:
            clusters.append([row])
        else:
            clusters[-1].append(row)
    # A dense table can contain many full-width rules connected into one tall
    # cluster.  That is internal layout, not a gutter between documents.
    clusters = [
        cluster for cluster in clusters
        if cluster[-1][0] - cluster[0][0] <= h * .15
    ]
    if not clusters:
        return [], 0.0
    cuts = [
        int(round((cluster[0][0] + cluster[-1][0]) / 2))
        for cluster in clusters
    ]
    cuts = [cut for cut in cuts if h * .14 <= cut <= h * .86]
    if not cuts:
        return [], 0.0

    boundaries = [0, *cuts, h]
    boxes = [(0, start, w, end - start) for start, end in zip(boundaries, boundaries[1:])]
    if not 2 <= len(boxes) <= 6 or any(height < h * .18 for _, _, _, height in boxes):
        return [], 0.0
    if any(ink[y:y + height].mean() < .006 for _, y, _, height in boxes):
        return [], 0.0
    strength = min(.95, .72 + min(score for cluster in clusters for _, score in cluster) * .35)
    return boxes, strength


def _overlap_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    iw = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    ih = max(0, min(ay + ah, by + bh) - max(ay, by))
    return (iw * ih) / max(1, min(aw * ah, bw * bh))


def _aligned_receipt_frames(contours, page_width: int) -> bool:
    """A whitespace cut can corroborate one gap without detecting every gap."""
    xs, _, widths, heights, scores = zip(*contours)
    return (
        min(scores) >= .79 and min(widths) >= page_width * .50
        and max(xs) - min(xs) <= page_width * .06
        and min(widths) >= max(widths) * .86
        and min(heights) >= max(heights) * .72
        and all(a[1] + a[3] <= b[1] for a, b in zip(contours, contours[1:]))
    )


def detect_receipts_detailed(image: np.ndarray) -> SplitDetection:
    h, w = image.shape[:2]
    contours, possible_count = _contour_boxes(image)
    whitespace_strength, whitespace = _whitespace_boxes(image)
    separator_boxes, separator_strength = _horizontal_separator_boxes(image)
    contour_rects = [(x, y, bw, bh) for x, y, bw, bh, _ in contours]
    contour_safe = 2 <= len(contours) <= 6 and all(
        _overlap_ratio(a, b) < .08
        for index, a in enumerate(contour_rects)
        for b in contour_rects[index + 1:]
    )
    if contour_safe:
        if whitespace and len(contours) != len(whitespace) and whitespace_strength >= .07:
            horizontal_cut = whitespace[1][1] if whitespace[0][2] == w else 0
            in_gap = any(a[1] + a[3] <= horizontal_cut <= b[1] for a, b in zip(contours, contours[1:]))
            if not (in_gap and _aligned_receipt_frames(contours, w)):
                return SplitDetection(
                    [(image, .40)], True,
                    ["检测到多种不一致的裁切边界"], contour_rects,
                )
        return SplitDetection(
            [(image[y:y + bh, x:x + bw].copy(), confidence) for x, y, bw, bh, confidence in contours],
            suggested_boxes=contour_rects,
        )
    if separator_boxes:
        return SplitDetection(
            [
                (image[y:y + bh, x:x + bw].copy(), separator_strength)
                for x, y, bw, bh in separator_boxes
            ],
            suggested_boxes=separator_boxes,
        )
    if whitespace and whitespace_strength >= .10:
        confidence = min(.88, .72 + whitespace_strength)
        return SplitDetection(
            [(image[y:y + bh, x:x + bw].copy(), confidence) for x, y, bw, bh in whitespace],
            suggested_boxes=whitespace,
        )
    if whitespace or possible_count >= 2 or len(contours) > 6:
        suggestions = contour_rects or whitespace
        return SplitDetection(
            [(image, .40)], True,
            ["疑似一页多张回单，自动拆分边界不可靠"], suggestions,
        )
    return SplitDetection([(image, .95)])


def detect_receipts(image: np.ndarray) -> list[tuple[np.ndarray, float]]:
    """Backward-compatible crop-only API."""
    return detect_receipts_detailed(image).crops
