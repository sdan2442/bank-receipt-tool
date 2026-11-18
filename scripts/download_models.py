from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
BASE = "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0"
NAMES = [
    "PP-OCRv5_server_det_infer", "PP-OCRv5_server_rec_infer",
    "PP-OCRv5_mobile_det_infer", "PP-OCRv5_mobile_rec_infer",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_directory(path: Path) -> None:
    files = {p.name for p in path.iterdir() if p.is_file()}
    if not ({"inference.json", "inference.pdmodel"} & files):
        raise RuntimeError(f"{path.name} 缺少模型结构文件")
    if "inference.pdiparams" not in files:
        raise RuntimeError(f"{path.name} 缺少 inference.pdiparams")
    for file in path.iterdir():
        if file.is_file() and file.stat().st_size == 0:
            raise RuntimeError(f"模型文件为空: {file}")


def download(name: str, archive_dir: Path) -> dict:
    url = f"{BASE}/{name}.tar"
    archive = archive_dir / f"{name}.tar"
    partial = archive.with_suffix(".tar.part")
    if not archive.exists():
        print(f"从 Paddle 官方源下载 {name} ...", flush=True)
        request = urllib.request.Request(url, headers={"User-Agent": "BankReceiptTool/0.1"})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out, 1024 * 1024)
        if partial.stat().st_size < 100_000:
            raise RuntimeError(f"下载文件异常小: {name}")
        partial.replace(archive)
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if not members or any(".." in Path(m.name).parts or Path(m.name).is_absolute() for m in members):
            raise RuntimeError(f"模型压缩包非法: {name}")
        target = MODELS / name
        if not target.exists():
            tar.extractall(MODELS, filter="data")
    validate_directory(MODELS / name)
    return {"name": name, "url": url, "archive_sha256": sha256(archive), "archive_size": archive.stat().st_size}


def main() -> None:
    MODELS.mkdir(parents=True, exist_ok=True)
    archives = MODELS / ".downloads"; archives.mkdir(exist_ok=True)
    entries = [download(name, archives) for name in NAMES]
    manifest = {"format": 1, "models": entries}
    (MODELS / "models_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("模型下载、解压和完整性校验完成。")


if __name__ == "__main__":
    main()

