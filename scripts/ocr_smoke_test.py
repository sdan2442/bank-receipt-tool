from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bank_receipt_tool.ocr_engine import LocalOCREngine

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--offline", action="store_true"); parser.add_argument("--mode", choices=("server", "balanced", "mobile"), default="server")
    args = parser.parse_args()
    if args.offline:
        os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
        def blocked(*_args, **_kwargs): raise OSError("offline test: network blocked")
        socket.socket.connect = blocked
    image = Image.new("RGB", (1000, 300), "white")
    draw = ImageDraw.Draw(image); draw.text((40, 80), "2026-09-08  12500.00", fill="black")
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "smoke.png"; image.save(path)
        engine = LocalOCREngine(ROOT / "models", args.mode)
        lines = engine.recognize(path)
    print(f"OCR 模型加载成功，返回 {len(lines)} 行结果；offline={args.offline}")

if __name__ == "__main__": main()
