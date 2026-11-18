from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .domain import Candidate, ExtractedFields, OCRLine, ProcessResult, ReceiptUnit


def parse_page_range(value: str, total: int) -> list[int]:
    if not value.strip():
        return list(range(1, total + 1))
    pages: set[int] = set()
    for part in value.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"页码范围顺序错误：{part}")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    if not pages or min(pages) < 1 or max(pages) > total:
        raise ValueError(f"页码必须在 1-{total} 之间")
    return sorted(pages)


def source_fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return {
        "path": str(path.resolve()), "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns, "sha256": digest.hexdigest(),
    }


def result_to_dict(result: ProcessResult) -> dict:
    return {
        "unit": {
            "source_path": str(result.unit.source_path), "source_name": result.unit.source_name,
            "page_number": result.unit.page_number, "crop_number": result.unit.crop_number,
            "image_path": str(result.unit.image_path), "boundary_confidence": result.unit.boundary_confidence,
            "boundary_reasons": result.unit.boundary_reasons, "suggested_boxes": result.unit.suggested_boxes,
        },
        "fields": asdict(result.fields),
        "ocr_lines": [asdict(line) for line in result.ocr_lines],
        "suggested_name": result.suggested_name,
        "output_path": str(result.output_path) if result.output_path else None,
        "status": result.status, "reasons": result.reasons,
        "error": result.error, "processed_at": result.processed_at,
        "document_type": result.document_type,
        "document_type_confidence": result.document_type_confidence,
        "document_type_evidence": result.document_type_evidence,
        "merged_page_paths": [str(path) for path in result.merged_page_paths],
        "merged_page_labels": result.merged_page_labels,
        "needs_split": result.needs_split,
    }


def result_from_dict(data: dict) -> ProcessResult:
    unit_data = data["unit"]
    unit = ReceiptUnit(
        source_path=Path(unit_data["source_path"]), source_name=unit_data["source_name"],
        page_number=int(unit_data["page_number"]), crop_number=int(unit_data["crop_number"]),
        image_path=Path(unit_data["image_path"]),
        boundary_confidence=float(unit_data.get("boundary_confidence", 1.0)),
        boundary_reasons=list(unit_data.get("boundary_reasons", [])),
        suggested_boxes=[tuple(box) for box in unit_data.get("suggested_boxes", [])],
    )
    fields_data = dict(data["fields"])
    for key in ("date_candidates", "payer_candidates", "payee_candidates", "amount_candidates",
                "invoice_number_candidates", "buyer_candidates"):
        fields_data[key] = [Candidate(**item) for item in fields_data.get(key, [])]
    fields = ExtractedFields(**fields_data)
    return ProcessResult(
        unit=unit, fields=fields,
        ocr_lines=[OCRLine(**line) for line in data.get("ocr_lines", [])],
        suggested_name=data.get("suggested_name", ""),
        output_path=Path(data["output_path"]) if data.get("output_path") else None,
        status=data.get("status", "failed"), reasons=list(data.get("reasons", [])),
        error=data.get("error", ""), processed_at=data.get("processed_at", ""),
        document_type=data.get("document_type", "unknown"),
        document_type_confidence=float(data.get("document_type_confidence", 0.0)),
        document_type_evidence=list(data.get("document_type_evidence", [])),
        merged_page_paths=[Path(path) for path in data.get("merged_page_paths", [])],
        merged_page_labels=list(data.get("merged_page_labels", [])),
        needs_split=bool(data.get("needs_split", False)),
    )


class JobCheckpoint:
    def __init__(self, path: Path, data: dict):
        self.path, self.data = path, data

    @property
    def completed_pages(self) -> set[int]:
        return {int(page) for page in self.data.get("completed_pages", [])}

    @property
    def results(self) -> list[ProcessResult]:
        return [result_from_dict(item) for item in self.data.get("results", [])]

    def save(self) -> None:
        self.data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp.json")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def complete_page(self, page: int, results: list[ProcessResult]) -> None:
        stored = [item for item in self.data.get("results", []) if int(item["unit"]["page_number"]) != page]
        stored.extend(result_to_dict(result) for result in results)
        self.data["results"] = stored
        self.data["completed_pages"] = sorted(self.completed_pages | {page})
        self.save()

    def set_status(self, status: str) -> None:
        self.data["status"] = status
        self.save()

    def update_result(self, result: ProcessResult) -> bool:
        """Persist a manually reviewed result back into this checkpoint."""
        target = _result_identity(result_to_dict(result))
        stored = list(self.data.get("results", []))
        for index, item in enumerate(stored):
            if _result_identity(item) == target:
                stored[index] = result_to_dict(result)
                previous = self.data["results"]
                self.data["results"] = stored
                try:
                    self.save()
                except Exception:
                    self.data["results"] = previous
                    raise
                return True
        return False

    def append_result(self, result: ProcessResult) -> None:
        """Append a synthetic local result such as a manually merged bill."""
        stored = list(self.data.get("results", []))
        identity = _result_identity(result_to_dict(result))
        stored = [item for item in stored if _result_identity(item) != identity]
        stored.append(result_to_dict(result))
        self.data["results"] = stored
        self.save()

    def commit_split(self, parent: ProcessResult, children: list[ProcessResult]) -> None:
        identity = _result_identity(result_to_dict(parent))
        stored = list(self.data.get("results", []))
        for index, item in enumerate(stored):
            if _result_identity(item) == identity:
                updated = [*stored[:index], result_to_dict(parent),
                           *(result_to_dict(child) for child in children), *stored[index + 1:]]
                self.data["results"] = updated
                try:
                    self.save()
                except Exception:
                    self.data["results"] = stored
                    raise
                return
        raise ValueError("历史任务中未找到拆分前的页面")


def _result_identity(data: dict) -> tuple[str, int, int]:
    unit = data.get("unit", {})
    source = os.path.normcase(os.path.abspath(str(unit.get("source_path", ""))))
    return source, int(unit.get("page_number", 0)), int(unit.get("crop_number", 0))


class CheckpointStore:
    def __init__(self, output_dir: Path):
        self.root = output_dir / ".jobs"

    def latest(self, fingerprint: dict, signature: dict) -> JobCheckpoint | None:
        if not self.root.exists():
            return None
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("status") not in {"running", "stopped"}:
                continue
            if data.get("source") == fingerprint and data.get("config") == signature:
                return JobCheckpoint(path, data)
        return None

    def history(self) -> list[JobCheckpoint]:
        """Return readable task records of every status, newest first."""
        if not self.root.exists():
            return []
        checkpoints: list[JobCheckpoint] = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or not isinstance(data.get("results", []), list):
                continue
            checkpoints.append(JobCheckpoint(path, data))
        return checkpoints

    def update_result(self, result: ProcessResult) -> JobCheckpoint | None:
        """Update the newest matching task when no explicit checkpoint is loaded."""
        for checkpoint in self.history():
            if checkpoint.update_result(result):
                return checkpoint
        return None

    def append_result_for_member(
        self, result: ProcessResult, member: ProcessResult
    ) -> JobCheckpoint | None:
        """Store a merged result beside the newest task containing a source page."""
        member_identity = _result_identity(result_to_dict(member))
        for checkpoint in self.history():
            if any(
                _result_identity(item) == member_identity
                for item in checkpoint.data.get("results", [])
            ):
                checkpoint.append_result(result)
                return checkpoint
        return None

    def find_for_member(self, member: ProcessResult) -> JobCheckpoint | None:
        record = result_to_dict(member)
        for checkpoint in self.history():
            if any(
                _result_identity(item) == _result_identity(record)
                and item.get("output_path") == record["output_path"]
                for item in checkpoint.data.get("results", [])
            ):
                return checkpoint
        return None

    def create(self, fingerprint: dict, signature: dict, total_pages: int, requested_pages: list[int]) -> JobCheckpoint:
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self.root / f"{fingerprint['sha256'][:16]}_{stamp}.json"
        checkpoint = JobCheckpoint(path, {
            "schema_version": 1, "source": fingerprint, "config": signature,
            "total_pages": total_pages, "requested_pages": requested_pages,
            "completed_pages": [], "results": [], "status": "running",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        checkpoint.save()
        return checkpoint
