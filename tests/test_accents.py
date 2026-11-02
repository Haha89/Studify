from __future__ import annotations

import cv2
import numpy as np
import pytest

from studify.models.bricklink import BRICKLINK_COLOR_IDS, ROUND_TILE, comes_in, wanted_list_xml
from studify.models.canvas import ACCENT_COLORS, ACCENT_MAX_SHARE, ACCENT_SPOT_STUDS
from studify.models.static import COLORS
from studify.process import build_canvas

NAMES = list(COLORS)
BLUE = (200, 80, 20)  # BGR, as OpenCV writes.
YELLOW = (20, 205, 245)
GREY = (150, 150, 150)
STUD = 4  # Pixels per stud in these pictures, all 32 studs wide.


def bgr(name: str) -> tuple[int, int, int]:
    return COLORS[name][::-1]


def picture(tmp_path, paint) -> object:
    img = np.zeros((32 * STUD, 32 * STUD, 3), dtype=np.uint8)
    img[:] = BLUE
    paint(img)
    ok, data = cv2.imencode(".png", img)
    assert ok
    path = tmp_path / "picture.png"
    path.write_bytes(data.tobytes())
    return path


def block(img, row, col, rows, cols, color):
    img[row * STUD : (row + rows) * STUD, col * STUD : (col + cols) * STUD] = color


def counts(canvas) -> dict[str, int]:
    return {NAMES[brick_id]: count for brick_id, count in canvas.get_distribution().items()}


def metal(canvas) -> int:
    used = counts(canvas)
    return sum(used.get(name, 0) for name in ACCENT_COLORS)


def build(path, **options):
    return build_canvas(1, path, round_tiles=True, accents=True, **options)


def test_every_accent_is_known_on_round_tiles():
    assert all(comes_in(NAMES.index(name), ROUND_TILE) for name in ACCENT_COLORS)


class TestSmallSpots:
    def test_a_small_golden_spot_turns_gold(self, tmp_path):
        path = picture(tmp_path, lambda img: block(img, 10, 10, 3, 3, YELLOW))
        canvas = build(path)
        assert NAMES[canvas.get_brick_ids()[11, 11]] == "Metallic Gold"

    def test_a_small_grey_spot_turns_silver(self, tmp_path):
        path = picture(tmp_path, lambda img: block(img, 10, 10, 3, 3, GREY))
        assert NAMES[build(path).get_brick_ids()[11, 11]] == "Flat Silver"

    def test_there_is_no_metal_unless_asked(self, tmp_path):
        path = picture(tmp_path, lambda img: block(img, 10, 10, 3, 3, YELLOW))
        assert metal(build_canvas(1, path, round_tiles=True)) == 0

    def test_many_spots_stay_within_the_share_allowed(self, tmp_path):
        def paint(img):
            for row in range(1, 31, 3):
                for col in range(1, 31, 3):
                    block(img, row, col, 2, 2, YELLOW)

        canvas = build(picture(tmp_path, paint))
        assert 0 < metal(canvas) <= 32 * 32 * ACCENT_MAX_SHARE


class TestLargeAreas:
    @pytest.mark.parametrize("color", [YELLOW, GREY], ids=["yellow", "grey"])
    def test_a_plain_large_area_stays_as_it_is(self, tmp_path, color):
        assert ACCENT_SPOT_STUDS < 10 * 10
        path = picture(tmp_path, lambda img: block(img, 5, 5, 10, 10, color))
        assert metal(build(path)) == 0

    def test_a_flat_area_in_the_exact_colour_of_metal_stays_as_it_is(self, tmp_path):
        path = picture(tmp_path, lambda img: block(img, 5, 5, 10, 10, bgr("Metallic Gold")))
        assert metal(build(path)) == 0

    def test_a_large_area_that_really_looks_like_gold_turns(self, tmp_path):
        def paint(img):
            for col in range(5, 25):
                name = "Metallic Gold" if col % 2 else "Pearl Gold"
                block(img, 5, col, 20, 1, bgr(name))

        canvas = build(picture(tmp_path, paint))
        assert metal(canvas) >= 20 * 20 * 0.9

    def test_a_large_textured_area_in_another_colour_stays_as_it_is(self, tmp_path):
        def paint(img):
            for col in range(5, 25):
                block(img, 5, col, 20, 1, (20, 230, 250) if col % 2 else (10, 150, 170))

        assert metal(build(picture(tmp_path, paint))) <= 32 * 32 * ACCENT_MAX_SHARE


def test_accents_reach_the_wanted_list(tmp_path):
    def paint(img):
        block(img, 5, 5, 3, 3, GREY)
        block(img, 20, 20, 3, 3, YELLOW)

    canvas = build(picture(tmp_path, paint))
    xml = wanted_list_xml(canvas.get_distribution(), canvas.layout(round_tiles=True).parts(canvas.get_brick_ids()))
    for name in ("Metallic Gold", "Flat Silver"):
        assert f"<ITEMID>98138</ITEMID><COLOR>{BRICKLINK_COLOR_IDS[name]}</COLOR>" in xml


def test_accents_need_round_tiles(tmp_path):
    with pytest.raises(ValueError):
        build_canvas(1, picture(tmp_path, lambda img: None), accents=True)


def test_colours_given_by_hand_stay_as_they_are(tmp_path):
    path = picture(tmp_path, lambda img: block(img, 10, 10, 3, 3, YELLOW))
    assert set(counts(build(path, colors=["Black", "White"]))) <= {"Black", "White"}


def test_a_colour_limit_is_respected(tmp_path):
    path = picture(tmp_path, lambda img: block(img, 10, 10, 3, 3, YELLOW))
    assert len(counts(build(path, max_colors=2))) <= 2
