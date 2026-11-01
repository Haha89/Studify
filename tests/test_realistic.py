from __future__ import annotations

import numpy as np
import pytest

from studify.models.realistic import MAX_SCALE, MIN_SCALE, ROUND_BASE_COLOR, render_round, render_tiles, tile_scale

SCALE = 16


def two_tiles(color=(200, 100, 50)) -> tuple[np.ndarray, np.ndarray]:
    """A 2x4 mosaic in one colour, made of two 2x2 tiles side by side."""
    colors = np.full((2, 4, 3), color, dtype=np.uint8)
    return colors, np.array([[0, 0, 1, 1], [0, 0, 1, 1]])


def test_scale_stays_within_bounds():
    assert tile_scale(32, 32) == MAX_SCALE
    assert tile_scale(192, 192) == MIN_SCALE
    assert tile_scale(96, 32) == 25


def test_image_has_the_mosaic_shape_scaled_up():
    assert render_tiles(*two_tiles(), scale=SCALE).shape == (2 * SCALE, 4 * SCALE, 3)


def test_a_seam_is_drawn_between_tiles_but_not_inside_one():
    pixels = render_tiles(*two_tiles(), scale=SCALE)
    middle_row = pixels[SCALE, :, 0].astype(int)
    assert middle_row[SCALE] == middle_row[SCALE // 2] == 200  # between two studs of one tile: flat
    assert middle_row[2 * SCALE] < 100  # between the tiles: dark


def test_light_comes_from_the_top_left():
    pixels = render_tiles(*two_tiles(), scale=SCALE)
    bevel_near_seam = 2
    lit = int(pixels[SCALE, 2 * SCALE + bevel_near_seam, 0])
    shadowed = int(pixels[SCALE, 2 * SCALE - bevel_near_seam, 0])
    assert lit > 200 > shadowed


def test_black_tiles_still_show_their_edges():
    pixels = render_tiles(*two_tiles(color=(0, 0, 0)), scale=SCALE)
    assert pixels.max() > 0
    assert pixels[SCALE, SCALE].tolist() == [0, 0, 0]


def test_bright_colours_do_not_wrap_around():
    pixels = render_tiles(*two_tiles(color=(255, 255, 255)), scale=SCALE)
    assert pixels[SCALE, 2 * SCALE + 2].tolist() == [255, 255, 255]


@pytest.mark.parametrize("scale", [1, 2, 3, 40])
def test_tiny_or_large_scales_still_draw(scale):
    assert render_tiles(*two_tiles(), scale=scale).shape == (2 * scale, 4 * scale, 3)
    assert render_round(two_tiles()[0], scale=scale).shape == (2 * scale, 4 * scale, 3)


def test_round_tiles_show_their_colour_inside_and_the_baseplate_at_the_corners():
    pixels = render_round(two_tiles()[0], scale=SCALE)
    assert pixels.shape == (2 * SCALE, 4 * SCALE, 3)
    assert abs(int(pixels[SCALE // 2, SCALE // 2, 0]) - 200) < 15
    assert pixels[0, 0].tolist() == list(ROUND_BASE_COLOR)


def test_plates_are_drawn_flat_among_round_tiles():
    colors = two_tiles()[0]
    regions = np.array([[0, 0, -1, -2], [0, 0, -3, -4]])
    pixels = render_round(colors, SCALE, regions)
    assert pixels[SCALE, SCALE, 0] == 200  # Inside the plate: flat colour.
    assert pixels[0, 3 * SCALE].tolist() == list(ROUND_BASE_COLOR)  # Corner of a round tile's stud.


def test_round_black_tiles_stand_out_from_the_baseplate():
    pixels = render_round(np.zeros((1, 1, 3), dtype=np.uint8), scale=40)
    assert pixels[20, 20].tolist() == [0, 0, 0]
    assert pixels[7, 7].sum() > 0  # The rim catches the light.
