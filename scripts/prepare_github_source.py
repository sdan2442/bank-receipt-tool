from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from privacy_audit import audit


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ".gitignore", "LICENSE", "NOTICE", "README.md", "README_使用说明.md",
    "THIRD_PARTY_NOTICES.md", "RELEASE_NOTES.md", "PUBLIC_RELEASE_CHECKLIST.md",
    "BankReceiptTool.spec", "build.ps1", "setup.ps1", "run.ps1", "run.py",
    "pyproject.toml", "requirements.txt", "requirements-dev.txt",
)
DIRECTORIES = ("bank_receipt_tool", "config", "scripts", "tests", "sample_data", "licenses")


def main() -> int:
    destination = ROOT / "release" / "github" / "bank-receipt-tool"
    archive = ROOT / "release" / "BankReceiptTool_v0.3.10_github_source_20261118.zip"
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Source destination already contains files: {destination.name}")
    if archive.exists():
        raise FileExistsError(f"Archive already exists: {archive.name}")
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(ROOT / name, destination / name)
    for name in DIRECTORIES:
        shutil.copytree(
            ROOT / name, destination / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
    findings = audit(destination, source=False)
    if findings:
        print("\n".join(findings), file=sys.stderr)
        return 1
    files = sorted(path for path in destination.rglob("*") if path.is_file())
    with ZipFile(archive, "x", compression=ZIP_DEFLATED, compresslevel=9) as package:
        for path in files:
            package.write(path, arcname=f"bank-receipt-tool/{path.relative_to(destination).as_posix()}")
    with ZipFile(archive) as package:
        if package.testzip() is not None:
            raise OSError("Source ZIP integrity check failed")
    manifest = {
        "repository": "sdan2442/bank-receipt-tool",
        "version": "0.3.10",
        "publication_date": "2026-11-18",
        "archive": archive.name,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "files": [
            {"path": path.relative_to(destination).as_posix(),
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "size": path.stat().st_size}
            for path in files
        ],
    }
    (ROOT / "release" / "github_source_manifest_20261118.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"Source package prepared: {len(files)} files, {archive.stat().st_size} bytes")
    print(f"SHA-256: {manifest['archive_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
