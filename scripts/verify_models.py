from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"

def sha256(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""): d.update(block)
    return d.hexdigest()

def main():
    manifest = json.loads((MODELS / "models_manifest.json").read_text(encoding="utf-8"))
    for model in manifest["models"]:
        archive = MODELS / ".downloads" / f'{model["name"]}.tar'
        if archive.stat().st_size != model["archive_size"] or sha256(archive) != model["archive_sha256"]:
            raise SystemExit(f'校验失败: {model["name"]}')
        path = MODELS / model["name"]
        names = {p.name for p in path.iterdir()}
        if "inference.pdiparams" not in names or not ({"inference.json", "inference.pdmodel"} & names):
            raise SystemExit(f'模型目录不完整: {model["name"]}')
    print("所有模型校验通过。")

if __name__ == "__main__": main()

