from pathlib import Path
from threading import Event

import cv2
import fitz
import numpy as np

from bank_receipt_tool.exporter import image_to_pdf_atomic, redact_sensitive
from bank_receipt_tool.file_reader import collect_inputs, split_input
from bank_receipt_tool.image_processing import deskew, preprocess, write_image_atomic


def test_multipage_pdf_split(tmp_path: Path):
    source = tmp_path / "input.pdf"
    doc = fitz.open()
    for _ in range(2): doc.new_page(width=400, height=600)
    doc.save(source); doc.close()
    units = split_input(source, tmp_path / "work", dpi=72)
    assert [u.page_number for u in units] == [1, 2]
    assert source.exists()


def test_rotation_and_deskew_do_not_destroy_image():
    upright = np.full((300, 900, 3), 255, np.uint8)
    cv2.putText(upright, "TEST RECEIPT 2026", (80, 170), cv2.FONT_HERSHEY_SIMPLEX, 2, (0,0,0), 4)
    sideways = cv2.rotate(upright, cv2.ROTATE_90_CLOCKWISE)
    rotated, meta = preprocess(sideways)
    assert meta["rotation"] == 90 and rotated.shape[0] < rotated.shape[1]
    skewed, angle = deskew(rotated)
    assert skewed.size > 0 and abs(angle) <= 12


def test_low_contrast_scan_is_enhanced():
    image = np.full((240, 800, 3), 180, np.uint8)
    cv2.putText(image, "LOW CONTRAST", (50, 140), cv2.FONT_HERSHEY_SIMPLEX, 2, (145,145,145), 3)
    enhanced, _ = preprocess(image)
    assert enhanced.shape == image.shape
    assert enhanced.std() >= image.std() * 0.8


def test_atomic_pdf_and_original_preserved(tmp_path: Path):
    image = tmp_path / "source.png"; write_image_atomic(image, np.full((100, 200, 3), 255, np.uint8))
    original = image.read_bytes(); target = tmp_path / "out.pdf"
    image_to_pdf_atomic(image, target)
    assert target.exists() and target.stat().st_size > 100
    assert image.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp.pdf"))


def test_cancel_before_split(tmp_path: Path):
    image = tmp_path / "source.png"; write_image_atomic(image, np.full((100, 200, 3), 255, np.uint8))
    cancel = Event(); cancel.set()
    assert split_input(image, tmp_path / "work", cancel=cancel) == []


def test_collect_supported_only(tmp_path: Path):
    (tmp_path / "a.jpg").write_bytes(b"x"); (tmp_path / "secret.txt").write_text("x")
    assert [p.name for p in collect_inputs([tmp_path])] == ["a.jpg"]


def test_sensitive_log_redaction():
    text = redact_sensitive("账号 6222000000000000000 身份证 110000200001010000")
    assert "6222000000000000000" not in text and "110000200001010000" not in text
