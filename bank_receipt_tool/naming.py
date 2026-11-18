from __future__ import annotations

import re
from pathlib import Path

from .domain import ExtractedFields

ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def clean_component(value: str, fallback: str, max_length: int = 80) -> str:
    value = ILLEGAL.sub("_", value or "")
    value = re.sub(r"[\s._-]+$", "", value.strip(" \t\r\n·•，,。;；:："))
    value = re.sub(r"\s+", " ", value)
    return (value or fallback)[:max_length]


def suggested_filename(fields: ExtractedFields, document_type: str = "receipt") -> str:
    if document_type == "invoice":
        number = clean_component(fields.invoice_number or "", "发票号待确认", 24)
        buyer = clean_component(fields.buyer or "", "购买方待确认")
        amount = clean_component(fields.amount or "", "金额待确认", 30)
        date = clean_component(fields.date or "", "开票日期待确认", 20)
        return f"{number}_{buyer}_{amount}_{date}.pdf"
    date = clean_component(fields.date or "", "日期待确认", 20)
    payer = clean_component(fields.payer or "", "付款人待确认")
    amount = clean_component(fields.amount or "", "金额待确认", 30)
    return f"{date}_{payer}_{amount}.pdf"


def unique_path(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = directory / f"{path.stem}_{index:02d}{path.suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError("同名文件过多")

