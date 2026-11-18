from __future__ import annotations

import multiprocessing
import sys
from PySide6.QtWidgets import QApplication
from .gui import MainWindow


def main() -> int:
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    app.setApplicationName("银行回单拆分与重命名工具")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())