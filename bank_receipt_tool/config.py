from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def app_data_root() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BankReceiptTool"
    root.mkdir(parents=True, exist_ok=True)
    return root


def desktop_dir() -> Path:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                desktop, _ = winreg.QueryValueEx(key, "Desktop")
                if desktop:
                    return Path(os.path.expandvars(desktop))
        except OSError:
            pass
    return Path.home() / "Desktop"


@dataclass(slots=True)
class AppConfig:
    rules_path: Path
    models_dir: Path
    output_dir: Path
    confidence_threshold: float = 0.72
    boundary_threshold: float = 0.70
    render_dpi: int = 220
    model_mode: str = "mobile"

    @classmethod
    def default(cls) -> "AppConfig":
        root = resource_root()
        return cls(
            rules_path=root / "config" / "bank_rules.yaml",
            models_dir=root / "models",
            output_dir=desktop_dir() / "银行回单处理结果",
        )

    def load_rules(self) -> dict:
        with self.rules_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)

