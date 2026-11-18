from pathlib import Path

from bank_receipt_tool.domain import ExtractedFields
from bank_receipt_tool.naming import suggested_filename, unique_path


def test_illegal_filename_characters():
    fields = ExtractedFields("2026-09-08", '上海/某:*?"<>|公司', "12500.00")
    name = suggested_filename(fields)
    assert not any(ch in name for ch in '\\/:*?"<>|')


def test_missing_fields_are_not_guessed():
    assert suggested_filename(ExtractedFields()) == "日期待确认_付款人待确认_金额待确认.pdf"


def test_name_collision(tmp_path: Path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    assert unique_path(tmp_path, "a.pdf").name == "a_02.pdf"
    (tmp_path / "a_02.pdf").write_bytes(b"x")
    assert unique_path(tmp_path, "a.pdf").name == "a_03.pdf"

