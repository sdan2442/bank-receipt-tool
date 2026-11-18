from __future__ import annotations

import os
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean, median

import cv2
import numpy as np

from .domain import OCRLine
from .image_processing import read_image, write_image_atomic


MODEL_SETS = {
    "server": {
        "det_name": "PP-OCRv5_server_det",
        "det_dir": "PP-OCRv5_server_det_infer",
        "rec_name": "PP-OCRv5_server_rec",
        "rec_dir": "PP-OCRv5_server_rec_infer",
    },
    "mobile": {
        "det_name": "PP-OCRv5_mobile_det",
        "det_dir": "PP-OCRv5_mobile_det_infer",
        "rec_name": "PP-OCRv5_mobile_rec",
        "rec_dir": "PP-OCRv5_mobile_rec_infer",
    },
    "balanced": {
        "det_name": "PP-OCRv5_mobile_det",
        "det_dir": "PP-OCRv5_mobile_det_infer",
        "rec_name": "PP-OCRv5_server_rec",
        "rec_dir": "PP-OCRv5_server_rec_infer",
    },
}


class LocalOCREngine:
    def __init__(self, models_dir: Path, mode: str = "server") -> None:
        if mode not in MODEL_SETS:
            raise ValueError(f"未知模型模式: {mode}")
        model_set = MODEL_SETS[mode]
        det_dir = models_dir / model_set["det_dir"]
        rec_dir = models_dir / model_set["rec_dir"]
        self._validate_model(det_dir)
        self._validate_model(rec_dir)
        # Disallow framework telemetry/update checks and force model source offline.
        os.environ.update({
            "PADDLE_PDX_EAGER_INIT": "False",
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
            # PaddleX 3.3 checks this unprefixed switch before it creates a
            # pipeline. Keep both names for compatibility and ensure an
            # explicitly local model never triggers a hoster connectivity test.
            "DISABLE_MODEL_SOURCE_CHECK": "True",
            "PADDLE_DISABLE_TELEMETRY": "1",
            "FLAGS_call_stack_level": "2",
        })
        from paddleocr import PaddleOCR

        kwargs = dict(
            # PaddleOCR 3.x validates that the model name embedded in the local
            # inference directory matches the requested architecture. Without
            # these explicit names it defaults to the server models, causing the
            # mobile directories to fail with "Model name mismatch".
            text_detection_model_name=model_set["det_name"],
            text_detection_model_dir=str(det_dir),
            text_recognition_model_name=model_set["rec_name"],
            text_recognition_model_dir=str(rec_dir),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
        )
        try:
            self.ocr = PaddleOCR(**kwargs)
        except TypeError:
            # PaddleOCR 2.x compatibility; still uses only explicit local paths.
            self.ocr = PaddleOCR(
                det_model_dir=str(det_dir), rec_model_dir=str(rec_dir),
                use_angle_cls=False, use_gpu=False, lang="ch", show_log=False,
            )

    @staticmethod
    def _validate_model(path: Path) -> None:
        if not path.is_dir():
            raise FileNotFoundError(f"本地模型不存在: {path}")
        files = {p.name for p in path.iterdir() if p.is_file()}
        if not ({"inference.json", "inference.pdmodel"} & files) or "inference.pdiparams" not in files:
            raise RuntimeError(f"模型文件不完整: {path}")

    def _run_once(self, path: Path) -> list[OCRLine]:
        if hasattr(self.ocr, "predict"):
            results = list(self.ocr.predict(str(path)))
            lines: list[OCRLine] = []
            for result in results:
                data = getattr(result, "json", result)
                if callable(data):
                    data = data()
                if isinstance(data, dict) and "res" in data:
                    data = data["res"]
                texts = data.get("rec_texts", []) if isinstance(data, dict) else []
                scores = data.get("rec_scores", []) if isinstance(data, dict) else []
                boxes = data.get("rec_polys", data.get("dt_polys", [])) if isinstance(data, dict) else []
                for text, score, box in zip(texts, scores, boxes):
                    lines.append(OCRLine(str(text), [[float(x), float(y)] for x, y in box], float(score)))
            return lines
        raw = self.ocr.ocr(str(path), cls=False)
        page = raw[0] if raw and len(raw) == 1 and isinstance(raw[0], list) else raw
        return [OCRLine(str(item[1][0]), [[float(x), float(y)] for x, y in item[0]], float(item[1][1])) for item in (page or [])]

    @staticmethod
    def _bottom_heavy(lines: list[OCRLine], image_height: int) -> bool:
        if not lines or image_height <= 0:
            return False
        centers = [line.center[1] for line in lines]
        return median(centers) > image_height * .66 and min(centers) > image_height * .30

    @staticmethod
    def _box_orientation_ratios(lines: list[OCRLine]) -> tuple[float, float]:
        horizontal = vertical = valid = 0
        for line in lines:
            if not line.box:
                continue
            xs = [point[0] for point in line.box]
            ys = [point[1] for point in line.box]
            width, height = max(xs) - min(xs), max(ys) - min(ys)
            if width <= 0 or height <= 0:
                continue
            valid += 1
            if width > height * 1.3:
                horizontal += 1
            elif height > width * 1.3:
                vertical += 1
        if not valid:
            return 0.0, 0.0
        return horizontal / valid, vertical / valid

    @classmethod
    def _orientation_score(cls, lines: list[OCRLine]) -> float:
        if not lines:
            return -1.0
        horizontal, vertical = cls._box_orientation_ratios(lines)
        confidence = mean(line.confidence for line in lines)
        hints = ("交易", "日期", "时间", "金额", "付款", "收款", "户名", "回单", "汇票", "票据")
        keyword_hits = sum(any(hint in line.text for hint in hints) for line in lines)
        return horizontal * 1.6 - vertical * 1.2 + confidence + min(keyword_hits, 10) * .035 + min(len(lines), 100) * .001

    def _run_rotated(self, path: Path, image, rotation: int, suffix: str) -> tuple[list[OCRLine], object]:
        rotated_image = cv2.rotate(image, rotation)
        return self._run_image(path, rotated_image, suffix), rotated_image

    def _run_image(self, path: Path, image, suffix: str) -> list[OCRLine]:
        temporary_path = path.with_name(path.stem + suffix + ".png")
        write_image_atomic(temporary_path, image)
        try:
            return self._run_once(temporary_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _orientation_probe(image, lines: list[OCRLine], rotation: int) -> tuple[object, str]:
        height, width = image.shape[:2]
        candidates: list[tuple[int, OCRLine, tuple[int, int, int, int]]] = []
        hints = ("交易", "日期", "金额", "付款", "收款", "户名", "回单", "汇票", "票据")
        for line in lines:
            if len(line.text.strip()) < 2 or not line.box:
                continue
            xs = [point[0] for point in line.box]
            ys = [point[1] for point in line.box]
            x0, x1 = max(0, int(min(xs)) - 6), min(width, int(max(xs)) + 7)
            y0, y1 = max(0, int(min(ys)) - 6), min(height, int(max(ys)) + 7)
            if x1 <= x0 or y1 <= y0 or (y1 - y0) <= (x1 - x0) * 1.3:
                continue
            priority = (1000 if any(hint in line.text for hint in hints) else 0) + min(len(line.text), 40) * 10
            candidates.append((priority, line, (x0, y0, x1, y1)))
        selected = sorted(candidates, key=lambda item: item[0], reverse=True)[:10]
        if not selected:
            return cv2.rotate(image, rotation), ""

        patches = []
        target_text = []
        for _, line, (x0, y0, x1, y1) in selected:
            patch = cv2.rotate(image[y0:y1, x0:x1], rotation)
            scale = min(1.0, 70 / max(1, patch.shape[0]), 1000 / max(1, patch.shape[1]))
            if scale < 1:
                patch = cv2.resize(patch, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            patches.append(patch)
            target_text.append(line.text)
        canvas_height = sum(patch.shape[0] for patch in patches) + 12 * (len(patches) + 1)
        canvas_width = max(patch.shape[1] for patch in patches) + 24
        canvas = np.full((canvas_height, canvas_width, 3), 255, dtype=np.uint8)
        y = 12
        for patch in patches:
            canvas[y:y + patch.shape[0], 12:12 + patch.shape[1]] = patch
            y += patch.shape[0] + 12
        return canvas, "".join(target_text)

    @classmethod
    def _probe_score(cls, lines: list[OCRLine], expected_text: str) -> float:
        recognized = "".join(line.text for line in lines)
        similarity = SequenceMatcher(None, expected_text, recognized).ratio() if expected_text and recognized else 0.0
        return cls._orientation_score(lines) + similarity * 2.0

    @staticmethod
    def _rotate_lines(lines: list[OCRLine], image_shape: tuple[int, int], rotation: int) -> list[OCRLine]:
        height, width = image_shape
        transformed: list[OCRLine] = []
        for line in lines:
            if rotation == cv2.ROTATE_90_CLOCKWISE:
                box = [[float(height - 1 - y), float(x)] for x, y in line.box]
            elif rotation == cv2.ROTATE_90_COUNTERCLOCKWISE:
                box = [[float(y), float(width - 1 - x)] for x, y in line.box]
            else:
                raise ValueError("仅支持转换 90 度 OCR 坐标")
            transformed.append(OCRLine(line.text, box, line.confidence))
        return sorted(transformed, key=lambda line: (line.center[1], line.center[0]))

    def recognize(self, path: Path) -> list[OCRLine]:
        self.last_rotation_degrees = 0
        self.last_source_shape: tuple[int, int] | None = None
        image = read_image(path)
        self.last_source_shape = image.shape[:2]
        upright = self._run_once(path)
        score_a = mean([line.confidence for line in upright]) if upright else 0

        # Dense table borders can defeat the Hough-line preprocessor: OCR still
        # recognizes the glyphs, but nearly every text box is tall instead of
        # wide. In that case compare both 90-degree directions and persist the
        # selected orientation to the temporary page/crop image.
        horizontal, vertical = self._box_orientation_ratios(upright)
        if len(upright) >= 3 and vertical >= .62 and horizontal <= .25:
            clockwise_probe, expected = self._orientation_probe(image, upright, cv2.ROTATE_90_CLOCKWISE)
            counterclockwise_probe, _ = self._orientation_probe(image, upright, cv2.ROTATE_90_COUNTERCLOCKWISE)
            clockwise = self._run_image(path, clockwise_probe, "_rot90cw_probe")
            counterclockwise = self._run_image(path, counterclockwise_probe, "_rot90ccw_probe")
            choices = (
                (clockwise, cv2.ROTATE_90_CLOCKWISE),
                (counterclockwise, cv2.ROTATE_90_COUNTERCLOCKWISE),
            )
            probe_lines, rotation = max(choices, key=lambda item: self._probe_score(item[0], expected))
            chosen_horizontal, chosen_vertical = self._box_orientation_ratios(probe_lines)
            if chosen_horizontal >= .50 and chosen_vertical < .35:
                chosen_image = cv2.rotate(image, rotation)
                suffix = "_rot90cw_full" if rotation == cv2.ROTATE_90_CLOCKWISE else "_rot90ccw_full"
                # Probe OCR only decides the direction. The initial OCR text was
                # produced from the sideways page and must never be reused by
                # merely rotating its coordinates. Run the complete upright page
                # once and return only that OCR result.
                corrected = self._run_image(path, chosen_image, suffix)
                full_choices = [(corrected, rotation, chosen_image)]
                # A lightweight model can occasionally choose the upside-down
                # direction from small probe patches. If the full-page result is
                # still implausibly weak, OCR the opposite 90-degree direction
                # and compare complete-page content rather than trusting the probe.
                corrected_confidence = mean([line.confidence for line in corrected]) if corrected else 0.0
                if corrected_confidence < .68 or self._orientation_score(corrected) < 2.15:
                    other_rotation = (
                        cv2.ROTATE_90_COUNTERCLOCKWISE
                        if rotation == cv2.ROTATE_90_CLOCKWISE
                        else cv2.ROTATE_90_CLOCKWISE
                    )
                    other_image = cv2.rotate(image, other_rotation)
                    other_suffix = (
                        "_rot90cw_full"
                        if other_rotation == cv2.ROTATE_90_CLOCKWISE
                        else "_rot90ccw_full"
                    )
                    other_lines = self._run_image(path, other_image, other_suffix)
                    full_choices.append((other_lines, other_rotation, other_image))
                corrected, rotation, chosen_image = max(
                    full_choices, key=lambda item: self._orientation_score(item[0])
                )
                corrected_horizontal, corrected_vertical = self._box_orientation_ratios(corrected)
                if corrected and corrected_horizontal >= .45 and corrected_vertical < .45:
                    write_image_atomic(path, chosen_image)
                    self.last_rotation_degrees = (
                        90 if rotation == cv2.ROTATE_90_CLOCKWISE else -90
                    )
                    return corrected

        bottom_heavy = False
        if len(upright) >= 3 and score_a >= 0.80:
            bottom_heavy = self._bottom_heavy(upright, image.shape[0])
            if not bottom_heavy:
                return upright
        rotated, rotated_image = self._run_rotated(path, image, cv2.ROTATE_180, "_rot180")
        score_b = mean([line.confidence for line in rotated]) if rotated else 0
        rotated_is_better = score_b > score_a + 0.025 and len(rotated) >= len(upright) * .7
        layout_recovered = (
            bottom_heavy
            and not self._bottom_heavy(rotated, image.shape[0])
            and score_b >= score_a - .01
            and len(rotated) >= len(upright) * .85
        )
        if rotated_is_better or layout_recovered:
            # The OCR result and the image shown/saved must use the same chosen
            # orientation. This path is a temporary crop, never the source file.
            write_image_atomic(path, rotated_image)
            self.last_rotation_degrees = 180
            return rotated
        return upright
