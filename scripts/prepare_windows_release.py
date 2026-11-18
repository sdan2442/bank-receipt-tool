"""Prepare a separate public copy. Never change the existing dist or outputs."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from privacy_audit import audit

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_VIDEO = {
    "opencv_videoio_ffmpeg4100_64.dll", "opencv_videoio_ffmpeg4120_64.dll",
}
DOCUMENTS = (
    "README.md", "README_使用说明.md", "LICENSE", "NOTICE",
    "THIRD_PARTY_NOTICES.md", "RELEASE_NOTES.md", "SOURCE_CODE.md",
)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main() -> int:
    original = ROOT / "dist/BankReceiptTool_v0_3_10"
    destination = ROOT / "release/publication/BankReceiptTool_v0_3_10"
    archive = ROOT / "release/BankReceiptTool_v0.3.10_windows_x64.zip"
    source_archive = ROOT / "release/ThirdPartySources_v0.3.10.zip"
    if destination.exists() or archive.exists() or source_archive.exists():
        raise FileExistsError("Public release destination already exists; do not overwrite it")
    if audit(original, source=False):
        raise ValueError("Original distribution failed privacy audit")
    upstream = json.loads((ROOT / "licenses/upstream_sources.json").read_text("utf-8"))
    sources = ROOT / ".cache/release-sources"
    for item in upstream["archives"]:
        path = sources / item["filename"]
        if path.stat().st_size != item["size"] or digest(path) != item["sha256"]:
            raise ValueError(f"Upstream source verification failed: {path.name}")
    print("Verified upstream source sizes and SHA-256", flush=True)
    shutil.copytree(original, destination, ignore=shutil.ignore_patterns(*EXCLUDED_VIDEO))
    for name in DOCUMENTS:
        shutil.copy2(ROOT / name, destination / name)
    shutil.copytree(ROOT / "licenses", destination / "licenses")
    if audit(destination, source=False):
        raise ValueError("Public copy failed privacy audit")
    files = sorted(path for path in destination.rglob("*") if path.is_file())
    # Every runtime file must remain byte-identical, apart from excluded video
    # plugins and the release documentation which was deliberately replaced.
    for path in original.rglob("*"):
        if not path.is_file() or path.name in EXCLUDED_VIDEO:
            continue
        relative = path.relative_to(original)
        if relative.as_posix() in DOCUMENTS:
            continue
        if digest(path) != digest(destination / relative):
            raise ValueError(f"Runtime file changed: {relative}")
    print(f"Verified unchanged runtime and models; {len(files)} public files", flush=True)
    with ZipFile(source_archive, "x", compression=ZIP_STORED) as package:
        for item in upstream["archives"]:
            if item["filename"].startswith("qt-everywhere"):
                continue  # Full Qt archive is published as its own release asset.
            package.write(sources / item["filename"], "ThirdPartySources/" + item["filename"])
        package.write(ROOT / "licenses/upstream_sources.json", "ThirdPartySources/upstream_sources.json")
        package.write(ROOT / "SOURCE_CODE.md", "ThirdPartySources/SOURCE_CODE.md")
    with ZipFile(archive, "x", compression=ZIP_DEFLATED, compresslevel=6) as package:
        for path in files:
            package.write(path, f"{destination.name}/{path.relative_to(destination).as_posix()}")
    for path in (archive, source_archive):
        with ZipFile(path) as package:
            if package.testzip() is not None:
                raise ValueError(f"ZIP CRC failed: {path.name}")
    with ZipFile(archive) as package:
        names = set(package.namelist())
        expected = {f"{destination.name}/{p.relative_to(destination).as_posix()}" for p in files}
        if names != expected:
            raise ValueError("Windows ZIP file list mismatch")
        if f"{destination.name}/银行回单工具.exe" not in names:
            raise ValueError("Windows ZIP Unicode filename was not preserved")
    report = {
        "application_version": "0.3.10", "release_tag": "v0.3.10-preview",
        "runtime_preserved": True, "models_included": 4,
        "privacy_audit": "passed", "zip_crc": "passed", "unicode_filenames": "passed",
        "runtime_file_comparison": "sha256 of all original runtime files",
        "excluded_unused_video_libraries": sorted(EXCLUDED_VIDEO),
        "documents_replaced": list(DOCUMENTS),
        "windows_archive": {"name": archive.name, "size": archive.stat().st_size, "sha256": digest(archive)},
        "third_party_sources": {"name": source_archive.name, "size": source_archive.stat().st_size, "sha256": digest(source_archive)},
    }
    (ROOT / "release/PUBLICATION_VERIFICATION_v0.3.10.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
