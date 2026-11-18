import os
import socket
import sys
import tempfile
from pathlib import Path

# Must be set before PaddleX/PaddleOCR is imported. PyInstaller may discover
# PaddleX modules in a different order; eager repository initialization is not
# needed for explicit local inference models and can otherwise run twice.
os.environ.setdefault("PADDLE_PDX_EAGER_INIT", "False")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("PADDLE_DISABLE_TELEMETRY", "1")

# Python 3.8+ no longer searches PATH for dependent DLLs automatically. The
# Paddle wheel keeps MKL/oneDNN next to the package, so register that directory
# explicitly both in source runs and in a PyInstaller distribution.
_runtime_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
_paddle_libs = _runtime_root / "paddle" / "libs"
_dll_directory_handle = None
if sys.platform == "win32" and _paddle_libs.is_dir():
    os.environ["PATH"] = str(_paddle_libs) + os.pathsep + os.environ.get("PATH", "")
    _dll_directory_handle = os.add_dll_directory(str(_paddle_libs))


def packaged_ocr_self_test(offline: bool) -> int:
    if offline:
        os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
        socket.socket.connect = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline self-test"))
    from PIL import Image, ImageDraw
    from bank_receipt_tool.config import AppConfig
    from bank_receipt_tool.ocr_engine import LocalOCREngine
    with tempfile.TemporaryDirectory() as temp:
        image_path = Path(temp) / "self_test.png"
        image = Image.new("RGB", (1000, 260), "white")
        ImageDraw.Draw(image).text((50, 90), "2026-09-08 12500.00", fill="black")
        image.save(image_path)
        if "--balanced" in sys.argv:
            mode = "balanced"
        elif "--mobile" in sys.argv:
            mode = "mobile"
        else:
            mode = "server"
        lines = LocalOCREngine(AppConfig.default().models_dir, mode).recognize(image_path)
    return 0 if lines else 2


if __name__ == "__main__" and "--ocr-self-test" in sys.argv:
    try:
        raise SystemExit(packaged_ocr_self_test("--offline" in sys.argv))
    except SystemExit:
        raise
    except Exception:
        import traceback
        report = Path(tempfile.gettempdir()) / "bank_receipt_tool_selftest_error.log"
        report.write_text(traceback.format_exc(), encoding="utf-8")
        raise SystemExit(1)

from bank_receipt_tool.app import main

if __name__ == "__main__":
    raise SystemExit(main())
