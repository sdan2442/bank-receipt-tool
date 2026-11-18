from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event, RLock
from typing import Callable, Literal

from PIL import Image

from . import __version__
from .checkpoint import CheckpointStore, JobCheckpoint, parse_page_range, source_fingerprint
from .config import AppConfig
from .document_classifier import DocumentClassification, classify_document
from .domain import ExtractedFields, OCRLine, ProcessResult, ReceiptUnit
from .exporter import export_xlsx, image_to_pdf_atomic, redact_sensitive, save_review_metadata
from .extractor import FieldExtractor
from .invoice import extract_invoice
from .file_reader import collect_inputs, input_page_count, iter_input_pages, split_page, whole_page_unit
from .naming import suggested_filename, unique_path
from .ocr_engine import LocalOCREngine
from .image_processing import read_image, rotate_rects, write_image_atomic
from .page_regions import crosses_region_boundary, lines_in_rect, repeated_receipt_regions


@dataclass(slots=True)
class RunOptions:
    mode: Literal["resume", "restart", "range"] = "resume"
    page_range: str = ""


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact_sensitive(super().format(record))


CONTINUOUS_DOCUMENT_MARKERS = (
    "电子银行承兑汇票",
    "电子商业承兑汇票",
    "增信信息",
    "票据行为人详细信息",
)


def split_fallback_reason(results: list[ProcessResult]) -> str | None:
    if any(result.document_type == "invoice" for result in results):
        return "检测到发票，已回退为整页处理"
    if any(result.document_type.startswith("acceptance_bill") for result in results):
        return "检测到承兑汇票复杂版面，已回退为整页处理"
    combined_text = "".join(result.full_text.replace(" ", "") for result in results)
    if any(marker in combined_text for marker in CONTINUOUS_DOCUMENT_MARKERS):
        return "检测到电子承兑汇票连续版面，已回退为整页处理"
    if any(
        result.status == "failed"
        or not all((result.fields.date, result.fields.payer, result.fields.amount))
        for result in results
    ):
        return "裁切候选缺少完整日期、付款人或金额，已回退为整页处理"
    if any(result.document_type != "receipt" for result in results):
        return "裁切候选缺少可靠回单特征，已回退为整页处理"
    return None


def setup_logging(output_dir: Path) -> logging.Logger:
    logs = output_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("bank_receipt_tool")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for existing in list(logger.handlers):
        existing.close()
        logger.removeHandler(existing)
    handler = logging.FileHandler(logs / "app.log", encoding="utf-8")
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


class ReceiptProcessor:
    def __init__(
        self,
        config: AppConfig,
        progress: Callable[[int, int, str], None] | None = None,
        result_callback: Callable[[ProcessResult], None] | None = None,
    ) -> None:
        self.config = config
        self.progress = progress or (lambda done, total, message: None)
        self.result_callback = result_callback or (lambda result: None)
        self.cancel_event = Event()
        self.pause_event = Event()
        self.termination_mode: Literal["stopped", "cancelled"] | None = None
        self.logger = setup_logging(config.output_dir)
        self.engine: LocalOCREngine | None = None
        self.rules = config.load_rules()
        # OCR runs independently. Only output and checkpoint writes are
        # serialized with reviews of pages already committed by the worker.
        self.result_lock = RLock()
        self.committed_results: list[ProcessResult] = []
        self._result_checkpoints: dict[int, JobCheckpoint] = {}

    def checkpoint_for_result(self, result: ProcessResult) -> JobCheckpoint | None:
        with self.result_lock:
            return self._result_checkpoints.get(id(result))

    def _record_results(self, results: list[ProcessResult], checkpoint: JobCheckpoint) -> None:
        self.committed_results.extend(results)
        self._result_checkpoints.update((id(result), checkpoint) for result in results)

    def stop(self):
        self.termination_mode = "stopped"
        self.cancel_event.set()

    def cancel(self):
        self.termination_mode = "cancelled"
        self.cancel_event.set()

    def pause(self): self.pause_event.set()
    def resume(self): self.pause_event.clear()

    def _wait_if_paused(self):
        while self.pause_event.is_set() and not self.cancel_event.wait(.2):
            pass

    def _analyze_unit(
        self, unit: ReceiptUnit, extractor: FieldExtractor, lines: list[OCRLine] | None = None,
    ) -> ProcessResult:
        now = datetime.now().isoformat(timespec="seconds")
        reuse_ocr = lines is not None
        try:
            if lines is None:
                assert self.engine is not None
                lines = self.engine.recognize(unit.image_path)
        except Exception as exc:
            self.logger.exception("OCR执行失败 %s 第%d页: %s", unit.source_name, unit.page_number, exc)
            fields = ExtractedFields(review_reasons=["OCR执行失败"])
            name = f"{unit.source_path.stem}_第{unit.page_number}页_裁切{unit.crop_number}.pdf"
            return ProcessResult(
                unit, fields, [], name, None, "failed", ["OCR执行失败"],
                str(exc), now,
            )

        analysis_errors: list[str] = []
        try:
            rotation = int(getattr(self.engine, "last_rotation_degrees", 0) or 0)
            source_shape = getattr(self.engine, "last_source_shape", None)
            if (
                not reuse_ocr
                and rotation
                and source_shape
                and unit.suggested_boxes
                and (unit.boundary_reasons or unit.image_path.stem.endswith("_full"))
            ):
                unit.suggested_boxes = rotate_rects(
                    unit.suggested_boxes, tuple(source_shape), rotation
                )
        except Exception as exc:
            self.logger.exception("OCR结果方向后处理失败 %s 第%d页: %s", unit.source_name, unit.page_number, exc)
            unit.suggested_boxes = []
            analysis_errors.append("OCR结果方向后处理异常")

        try:
            classification = classify_document(lines, self.rules)
        except Exception as exc:
            self.logger.exception("文件类型识别失败 %s 第%d页: %s", unit.source_name, unit.page_number, exc)
            classification = DocumentClassification("unknown", 0.0, [])
            analysis_errors.append("文件类型识别异常")

        try:
            fields = (extract_invoice(lines, self.config.confidence_threshold)
                      if classification.document_type == "invoice" else extractor.extract(lines))
        except Exception as exc:
            self.logger.exception("字段解析失败 %s 第%d页: %s", unit.source_name, unit.page_number, exc)
            fields = ExtractedFields(review_reasons=["字段解析异常，需人工复核"])
            analysis_errors.append(f"字段解析异常：{exc}")

        reasons = list(fields.review_reasons) + list(unit.boundary_reasons) + analysis_errors
        if self.config.model_mode == "mobile":
            reasons.append("快速模式（纯 mobile）识别结果需人工复核")
        if classification.document_type.startswith("acceptance_bill"):
            # Bill extraction has different semantics from a bank receipt.
            # Preserve the whole page and its OCR for dedicated review; an
            # unreliable crop suggestion must not be shown on this layout.
            unit.suggested_boxes = []
            reasons.append(
                f"识别为{classification.label}，已整页保留并进入人工复核"
            )
        elif classification.document_type == "unknown" and "文件类型识别异常" not in reasons:
            reasons.append("文件类型无法可靠识别")
        elif classification.document_type == "invoice":
            unit.suggested_boxes = []
        if unit.boundary_confidence < self.config.boundary_threshold and "拆分边界不可靠" not in reasons:
            reasons.append("拆分边界不可靠")
        name = suggested_filename(fields, classification.document_type)
        status = "review" if reasons else "success"
        return ProcessResult(
            unit, fields, lines, name, None, status, reasons,
            error="；".join(analysis_errors), processed_at=now,
            document_type=classification.document_type,
            document_type_confidence=classification.confidence,
            document_type_evidence=classification.evidence,
        )

    def _persist_result(self, result: ProcessResult) -> ProcessResult:
        with self.result_lock:
            return self._write_result(result)

    def _write_result(self, result: ProcessResult) -> ProcessResult:
        unit = result.unit
        try:
            target = unique_path(self.config.output_dir / result.status, result.suggested_name)
            image_to_pdf_atomic(unit.image_path, target)
            result.output_path = target
            audit_dir = self.config.output_dir / "logs" / "ocr_audit"
            audit_dir.mkdir(parents=True, exist_ok=True)
            audit_target = unique_path(audit_dir, target.with_suffix(".json").name)
            save_review_metadata(result, audit_target)
            if result.status == "review":
                preview_dir = self.config.output_dir / "review" / ".previews"
                preview_dir.mkdir(parents=True, exist_ok=True)
                preview = unique_path(preview_dir, target.with_suffix(".png").name)
                tmp_preview = preview.with_name(preview.stem + ".tmp" + preview.suffix)
                shutil.copy2(unit.image_path, tmp_preview)
                tmp_preview.replace(preview)
                unit.image_path = preview
                save_review_metadata(result, target)
            self.logger.info(
                "处理完成 %s 第%d页 裁切%d 状态=%s",
                unit.source_name, unit.page_number, unit.crop_number, result.status,
            )
            return result
        except Exception as exc:
            self.logger.exception("输出失败 %s 第%d页: %s", unit.source_name, unit.page_number, exc)
            result.status = "failed"
            result.error = str(exc)
            if "OCR或输出失败" not in result.reasons:
                result.reasons.append("OCR或输出失败")
            failed_name = result.suggested_name or f"{unit.source_path.stem}_第{unit.page_number}页_裁切{unit.crop_number}.pdf"
            target = unique_path(self.config.output_dir / "failed", failed_name)
            try:
                image_to_pdf_atomic(unit.image_path, target)
                result.output_path = target
            except Exception:
                result.output_path = None
            return result

    def _process_unit(self, unit: ReceiptUnit, extractor: FieldExtractor) -> ProcessResult:
        return self._persist_result(self._analyze_unit(unit, extractor))

    @staticmethod
    def _mark_pending_split(result: ProcessResult, reason: str) -> ProcessResult:
        result.needs_split = True
        result.status = "review"
        result.fields = ExtractedFields()
        result.reasons = [reason, *result.unit.boundary_reasons]
        result.reasons = list(dict.fromkeys(result.reasons))
        result.suggested_name = f"多回单待拆分_第{result.unit.page_number}页.pdf"
        return result

    def _split_using_text(
        self, whole: ProcessResult, extractor: FieldExtractor, work: Path,
    ) -> list[ProcessResult]:
        if whole.status == "failed" or whole.document_type == "invoice" or whole.document_type.startswith("acceptance_bill"):
            return [whole]
        if any(marker in "".join(whole.full_text.split()) for marker in CONTINUOUS_DOCUMENT_MARKERS):
            return [whole]
        if not any(line.box for line in whole.ocr_lines):
            return [whole]
        with Image.open(whole.unit.image_path) as preview:
            shape = preview.height, preview.width
        regions = repeated_receipt_regions(whole.ocr_lines, shape)
        if not regions:
            return [whole]

        whole.unit.suggested_boxes = regions
        whole.unit.boundary_reasons = ["同页存在多个回单标题"]
        if crosses_region_boundary(whole.ocr_lines, regions):
            return [self._mark_pending_split(whole, "裁切线可能穿过文字，请手动拆分")]

        image = read_image(whole.unit.image_path)
        candidates = []
        for index, rect in enumerate(regions, 1):
            self._wait_if_paused()
            x, y, width, height = rect
            page = whole.unit.page_number
            target = work / f"p{page:04d}_text{index:02d}.png"
            write_image_atomic(target, image[y:y + height, x:x + width])
            unit = ReceiptUnit(whole.unit.source_path, whole.unit.source_name, page, index, target, .84)
            candidate = self._analyze_unit(unit, extractor, lines_in_rect(whole.ocr_lines, rect))
            if split_fallback_reason([candidate]) and not self.cancel_event.is_set():
                # The smaller image lets the detector retain more text detail.
                candidate = self._analyze_unit(unit, extractor)
            candidates.append(candidate)

        fallback = split_fallback_reason(candidates)
        return [self._mark_pending_split(whole, fallback)] if fallback else candidates

    def _signature(self) -> dict:
        return {
            "app_version": __version__,
            "model_mode": self.config.model_mode,
            "render_dpi": self.config.render_dpi,
            "confidence_threshold": self.config.confidence_threshold,
            "boundary_threshold": self.config.boundary_threshold,
        }

    def _page_failure(self, source: Path, page_number: int, image_path: Path, error: Exception) -> ProcessResult:
        unit = ReceiptUnit(source, source.name, page_number, 0, image_path, 0.0, ["页面拆分或预处理失败"])
        return ProcessResult(
            unit, ExtractedFields(review_reasons=["页面处理失败"]), [], "", None,
            "failed", ["页面处理失败"], str(error), datetime.now().isoformat(timespec="seconds"),
        )

    def run(self, selected: list[Path], options: RunOptions | None = None) -> list[ProcessResult]:
        options = options or RunOptions()
        files = collect_inputs(selected)
        output = self.config.output_dir
        for folder in ("success", "review", "failed", "logs"):
            (output / folder).mkdir(parents=True, exist_ok=True)
        store = CheckpointStore(output)
        signature = self._signature()
        jobs: list[tuple[Path, int, list[int], JobCheckpoint]] = []
        self.committed_results.clear()
        self._result_checkpoints.clear()
        results = self.committed_results

        self.progress(0, 1, "正在读取页数并校验断点……")
        for source in files:
            total_pages = input_page_count(source)
            fingerprint = source_fingerprint(source)
            checkpoint = store.latest(fingerprint, signature) if options.mode == "resume" else None
            if checkpoint:
                requested = [int(page) for page in checkpoint.data.get("requested_pages", range(1, total_pages + 1))]
                previous = checkpoint.results
                with self.result_lock:
                    checkpoint.set_status("running")
                    self._record_results(previous, checkpoint)
                for result in previous:
                    self.result_callback(result)
            else:
                requested = parse_page_range(options.page_range, total_pages) if options.mode == "range" else list(range(1, total_pages + 1))
                checkpoint = store.create(fingerprint, signature, total_pages, requested)
            jobs.append((source, total_pages, requested, checkpoint))

        total_work = sum(len(requested) for _, _, requested, _ in jobs)
        done = sum(len(set(requested) & checkpoint.completed_pages) for _, _, requested, checkpoint in jobs)
        self.progress(done, max(total_work, 1), "正在加载本地 OCR 模型……")
        self.engine = LocalOCREngine(self.config.models_dir, self.config.model_mode)
        extractor = FieldExtractor(self.rules, self.config.confidence_threshold)
        work_root = output / ".work"
        work_root.mkdir(exist_ok=True)

        try:
            for source, source_total, requested, checkpoint in jobs:
                remaining = set(requested) - checkpoint.completed_pages
                if not remaining:
                    with self.result_lock:
                        checkpoint.set_status("completed")
                    continue
                file_work = Path(tempfile.mkdtemp(prefix="receipt_", dir=work_root))
                try:
                    for page_number, image_path in iter_input_pages(
                        source, file_work, self.config.render_dpi,
                        selected_pages=remaining, cancel=self.cancel_event,
                    ):
                        self._wait_if_paused()
                        if self.cancel_event.is_set():
                            break
                        self.progress(done, max(total_work, 1), f"正在预处理第 {page_number}/{source_total} 页：{source.name}")
                        page_results: list[ProcessResult] = []
                        try:
                            units = split_page(source, page_number, image_path, file_work)
                            if len(units) > 1:
                                candidates: list[ProcessResult] = []
                                for crop_index, unit in enumerate(units, 1):
                                    self._wait_if_paused()
                                    self.progress(
                                        done, max(total_work, 1),
                                        f"正在验证第 {page_number}/{source_total} 页，候选裁切 {crop_index}/{len(units)}",
                                    )
                                    candidates.append(self._analyze_unit(unit, extractor))
                                fallback_reason = split_fallback_reason(candidates)
                                if fallback_reason:
                                    self.progress(
                                        done, max(total_work, 1),
                                        f"第 {page_number}/{source_total} 页裁切验证未通过，正在回退整页 OCR",
                                    )
                                    fallback = whole_page_unit(
                                        source, page_number, image_path, file_work, fallback_reason,
                                        units[0].suggested_boxes,
                                    )
                                    whole = self._analyze_unit(fallback, extractor)
                                    confirmed_crops = sum(split_fallback_reason([candidate]) is None for candidate in candidates)
                                    if (whole.status != "failed" and whole.document_type != "invoice"
                                            and not whole.document_type.startswith("acceptance_bill")
                                            and confirmed_crops >= 2):
                                        self._mark_pending_split(whole, fallback_reason)
                                    page_results.append(self._persist_result(whole))
                                else:
                                    page_results.extend(self._persist_result(result) for result in candidates)
                            else:
                                self._wait_if_paused()
                                self.progress(
                                    done, max(total_work, 1),
                                    f"正在 OCR 第 {page_number}/{source_total} 页，裁切 1/1",
                                )
                                whole = self._analyze_unit(units[0], extractor)
                                page_results.extend(
                                    self._persist_result(result)
                                    for result in self._split_using_text(whole, extractor, file_work)
                                )
                        except Exception as exc:
                            self.logger.exception("页面处理失败 %s 第%d页: %s", source.name, page_number, exc)
                            result = self._page_failure(source, page_number, image_path, exc)
                            page_results.append(result)
                        with self.result_lock:
                            checkpoint.complete_page(page_number, page_results)
                            self._record_results(page_results, checkpoint)
                            export_xlsx(results, output / "processing_results.xlsx")
                        for result in page_results:
                            self.result_callback(result)
                        done += 1
                        self.progress(done, max(total_work, 1), f"已完成第 {page_number}/{source_total} 页：{source.name}")
                        if self.cancel_event.is_set():
                            break
                finally:
                    shutil.rmtree(file_work, ignore_errors=True)
                if self.cancel_event.is_set():
                    break
                with self.result_lock:
                    checkpoint.set_status("completed")

            final_status = self.termination_mode or "completed"
            with self.result_lock:
                for _, _, requested, checkpoint in jobs:
                    if set(requested) <= checkpoint.completed_pages:
                        checkpoint.set_status("completed")
                    elif final_status != "completed":
                        checkpoint.set_status(final_status)
                export_xlsx(results, output / "processing_results.xlsx")
            return results
        finally:
            for handler in list(self.logger.handlers):
                handler.close()
                self.logger.removeHandler(handler)
