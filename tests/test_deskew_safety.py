"""Synthetic geometry checks: a correction must improve, never invent, skew."""
import math

import cv2
import numpy as np
import pytest

import bank_receipt_tool.image_processing as processing
from bank_receipt_tool.image_processing import deskew


def receipt():
    image = np.full((1100, 1250, 3), 255, np.uint8)
    for index in range(9):
        y = 220 + index * 65
        cv2.putText(image, f"RECEIPT {index} DATE 2026 VALUE 12500", (170, y),
                    cv2.FONT_HERSHEY_SIMPLEX, .85, (0, 0, 0), 2)
        # Blue rules let the test measure the actual baseline independently of
        # the production text-component/minimum-rectangle estimator.
        cv2.line(image, (160, y + 12), (900, y + 12), (230, 110, 50), 2)
    return image


def rotate(image, angle):
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1)
    return cv2.warpAffine(image, matrix, (w, h), borderValue=(255, 255, 255))


def baseline_angle(image):
    blue = ((image[:, :, 0] > 170) & (image[:, :, 1] < 160)
            & (image[:, :, 2] < 120)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(blue)
    angles = []
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] < 180:
            continue
        ys, xs = np.where(labels == index)
        vx, vy, *_ = cv2.fitLine(np.column_stack((xs, ys)).astype(np.float32),
                                    cv2.DIST_L2, 0, .01, .01).ravel()
        value = math.degrees(math.atan2(float(vy), float(vx)))
        angles.append((value + 90) % 180 - 90)
    assert len(angles) >= 3
    return float(np.median(angles))


def test_upright_page_with_diagonal_edge_noise_is_pixel_identical():
    image = receipt()
    # Four scanner specks define a slanted *page envelope*, although the text
    # baselines are horizontal. The old global minAreaRect follows these dots.
    corners = np.array([[[45, 90], [1150, 90], [1150, 970], [45, 970]]], np.float32)
    matrix = cv2.getRotationMatrix2D((625, 550), -2.5, 1)
    for x, y in cv2.transform(corners, matrix)[0]:
        cv2.circle(image, (round(x), round(y)), 4, (0, 0, 0), -1)
    cv2.putText(image, '315', (1040, 170), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,
                1.4, (0, 0, 0), 2)
    cv2.circle(image, (1030, 870), 60, (20, 20, 20), 3)
    actual, correction = deskew(image)
    assert correction == 0
    assert np.array_equal(actual, image)
    assert abs(baseline_angle(actual)) < .02


@pytest.mark.parametrize('angle', [-6, -3, -1, 1, 3, 6])
def test_both_skew_directions_are_corrected_toward_horizontal(angle):
    source = rotate(receipt(), angle)
    before = baseline_angle(source)
    actual, correction = deskew(source)
    assert correction * angle < 0
    assert abs(correction + angle) < .4
    after = baseline_angle(actual)
    assert abs(after) < .3
    assert abs(after) < abs(before) / 2
    # Reprocessing an already corrected page must not introduce a new angle.
    again, second_correction = deskew(actual)
    assert second_correction == 0
    assert np.array_equal(again, actual)


@pytest.mark.parametrize('angle', [-.2, 0, .2])
def test_nearly_horizontal_pages_remain_pixel_identical(angle):
    source = rotate(receipt(), angle)
    actual, correction = deskew(source)
    assert correction == 0
    assert np.array_equal(actual, source)


def test_blank_table_and_sparse_handwriting_do_not_define_skew():
    blank = np.full((1000, 900, 3), 255, np.uint8)
    table = blank.copy()
    for y in range(100, 900, 55):
        cv2.line(table, (50, y), (850, y), (0, 0, 0), 2)
    for x in range(50, 851, 100):
        cv2.line(table, (x, 100), (x, 870), (0, 0, 0), 2)
    sparse = blank.copy()
    cv2.putText(sparse, '315', (650, 160), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,
                2.2, (0, 0, 0), 3)
    cv2.circle(sparse, (600, 650), 100, (0, 0, 0), 3)
    for source in (blank, rotate(table, -4), rotate(sparse, 4)):
        actual, correction = deskew(source)
        assert correction == 0
        assert np.array_equal(actual, source)


def test_different_receipt_angles_are_not_forced_into_one_page_angle():
    source = receipt()
    first = rotate(source, -4)
    second = rotate(source, 4)
    mixed = np.vstack((first[:550], second[550:]))
    actual, correction = deskew(mixed)
    assert correction == 0
    assert np.array_equal(actual, mixed)


def test_correction_keeps_edge_content_and_uses_white_padding():
    source = rotate(receipt(), -3)
    h, w = source.shape[:2]
    for x, y in [(5, 5), (w-6, 5), (5, h-6), (w-6, h-6)]:
        cv2.rectangle(source, (x-3, y-3), (x+3, y+3), (0, 0, 255), -1)
    actual, correction = deskew(source)
    assert 2.6 < correction < 3.4
    assert actual.shape[0] > h and actual.shape[1] > w
    red = ((actual[:, :, 2] > 180) & (actual[:, :, 1] < 80)
           & (actual[:, :, 0] < 80)).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(red)
    assert sum(stats[i, cv2.CC_STAT_AREA] > 20 for i in range(1, count)) == 4
    assert np.all(actual[0, 0] == 255)
    assert np.all(actual[-1, -1] == 255)


@pytest.mark.parametrize('verification', [None, 3.5, -1.0])
def test_unverified_or_unimproved_candidate_rolls_back(monkeypatch, verification):
    source = rotate(receipt(), -3)
    estimates = iter([3.0, verification])
    monkeypatch.setattr(processing, '_text_skew_angle', lambda _: next(estimates))
    actual, correction = deskew(source)
    assert correction == 0
    assert np.array_equal(actual, source)
