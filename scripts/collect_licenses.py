from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGAL_NAME = re.compile(r"^(?:license|licence|copying|notice|copyright|authors)(?:[._-].*)?$", re.I)


def collect(destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    packages = []
    for distribution in sorted(
        metadata.distributions(), key=lambda item: item.metadata.get("Name", "").lower()
    ):
        name = distribution.metadata.get("Name")
        if not name:
            continue
        package_dir = re.sub(r"[^a-zA-Z0-9_.-]", "_", name) + "-" + distribution.version
        license_name = (
            distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License")
            or "; ".join(
                value for value in distribution.metadata.get_all("Classifier", [])
                if value.startswith("License ::")
            )
        )
        entry = {
            "name": name,
            "version": distribution.version,
            "license_metadata": license_name,
            "project_urls": distribution.metadata.get_all("Project-URL", []),
            "license_files": [],
        }
        for relative in distribution.files or []:
            if not (
                LEGAL_NAME.fullmatch(relative.name)
                or any("license" in part.lower() for part in relative.parts[:-1])
            ):
                continue
            parts = relative.parts
            if ".." in parts or Path(str(relative)).is_absolute():
                continue
            source = Path(distribution.locate_file(relative))
            if not source.is_file():
                continue
            target = destination / "third_party" / package_dir / Path(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            entry["license_files"].append({
                "path": target.relative_to(destination).as_posix(),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            })
        packages.append(entry)
    manifest = {
        "format": 1,
        "scope": "installed build environment, including development tools",
        "packages": packages,
    }
    path = destination / "third_party_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect distribution-supplied legal notices")
    parser.add_argument("--output", type=Path, default=ROOT / "licenses")
    args = parser.parse_args()
    manifest = collect(args.output)
    count = sum(len(package["license_files"]) for package in manifest["packages"])
    print(f"Collected {count} legal files from {len(manifest['packages'])} distributions")
    missing = [package["name"] for package in manifest["packages"] if not package["license_files"]]
    if missing:
        print("No bundled legal file in metadata for: " + ", ".join(missing))


if __name__ == "__main__":
    main()
