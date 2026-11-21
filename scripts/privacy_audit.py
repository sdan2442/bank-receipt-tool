from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PRIVATE_NAMES = {
    "logs", ".jobs", ".work", "ocr_audit", "processing_results.xlsx",
}
PRIVATE_SUFFIXES = {
    ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp",
    ".xlsx", ".xls", ".csv", ".log",
}
SOURCE_EXCLUDES = {
    ".git", ".venv", ".python311", "__pycache__", ".pytest_cache",
    ".cache", "build", "dist", "models", "snapshots", "release", "tmp",
}
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".toml", ".ps1", ".txt"}
LOCAL_PATH = re.compile(r"[A-Za-z]:\\Users\\[^\\\r\n]+", re.IGNORECASE)


def iter_files(root: Path, source: bool):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if source and any(part in SOURCE_EXCLUDES for part in relative.parts):
            continue
        yield path, relative


def audit(root: Path, source: bool) -> list[str]:
    findings: list[str] = []
    for path, relative in iter_files(root, source):
        lowered_parts = {part.lower() for part in relative.parts}
        if lowered_parts & PRIVATE_NAMES or path.name.lower() in PRIVATE_NAMES:
            findings.append(f"禁止的运行数据路径：{relative}")
        if path.suffix.lower() in PRIVATE_SUFFIXES:
            findings.append(f"禁止的银行文档/运行结果：{relative}")
        if path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size <= 5_000_000:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if LOCAL_PATH.search(text):
                findings.append(f"包含本机用户绝对路径：{relative}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="发布前检查本地历史数据和绝对路径")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=Path, help="检查公开源码目录")
    group.add_argument("--binary", type=Path, help="检查可运行发布目录")
    args = parser.parse_args()
    root = (args.source or args.binary).resolve()
    if not root.is_dir():
        print(f"目录不存在：{root}", file=sys.stderr)
        return 2
    findings = audit(root, source=args.source is not None)
    if findings:
        print("隐私审计失败：")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print(f"隐私审计通过：{root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
