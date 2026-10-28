from __future__ import annotations

import numpy as np
import pytest

from legome.models.brickstore import PRICE_BASE_USD, PRICE_TILE_USD
from legome.models.canvas import Canvas
from legome.models.static import DIM_BASE


def test_size_is_expressed_in_studs():
    assert Canvas(3).size == 3 * DIM_BASE


@pytest.mark.parametrize("bases", [0, -1])
def test_rejects_a_canvas_smaller_than_one_baseplate(bases):
    with pytest.raises(ValueError, match="at least 1"):
        Canvas(bases)


def test_empty_canvas_has_no_shape_cost_or_bricks():
    canvas = Canvas(1)
    assert canvas.shape == (0, 0)
    assert canvas.get_cost() == 0
    assert canvas.get_distribution() == {}


def test_plot_canvas_rejects_an_empty_canvas():
    with pytest.raises(ValueError, match="empty"):
        Canvas(1).plot_canvas()


def test_to_pixel_array_rejects_an_empty_canvas():
    with pytest.raises(ValueError, match="empty"):
        Canvas(1).to_pixel_array()


def test_to_pixel_array_is_one_rgb_pixel_per_brick(make_canvas):
    canvas = make_canvas([[1, 2, 3], [4, 5, 6]])
    pixels = canvas.to_pixel_array()
    assert pixels.shape == (2, 3, 3)
    assert pixels.dtype == np.uint8
    assert tuple(pixels[0, 0]) == canvas.bricks[0][0].color
    assert tuple(pixels[1, 2]) == canvas.bricks[1][2].color


def test_source_path_is_unset_until_fitted(make_canvas):
    assert make_canvas([[1]]).source_path is None


def test_shape_and_brick_ids(make_canvas):
    canvas = make_canvas([[1, 2, 3], [4, 5, 6]])
    assert canvas.shape == (2, 3)
    assert np.array_equal(canvas.get_brick_ids(), np.array([[1, 2, 3], [4, 5, 6]]))


def test_cost_charges_per_stud_plus_a_share_of_the_baseplate(make_canvas):
    canvas = make_canvas([[1, 1], [1, 1]])
    expected = 4 * (PRICE_TILE_USD + PRICE_BASE_USD / (DIM_BASE * DIM_BASE))
    assert canvas.get_cost() == pytest.approx(expected)


def test_to_html_contains_a_table_of_brick_ids(make_canvas):
    html = make_canvas([[11, 12]]).to_html()
    assert html.startswith("<table")
    assert ">11</td>" in html and ">12</td>" in html


def test_plot_canvas_runs_headless(make_canvas):
    make_canvas([[1, 2], [3, 4]]).plot_canvas()  # Agg backend, pinned in conftest


class TestResize:
    def test_wide_image_fills_the_long_side(self):
        canvas = Canvas(2)  # 64 studs
        resized = canvas.resize_img(np.zeros((64, 128, 3), dtype=np.uint8))
        assert resized.shape[:2] == (32, 64)  # 2:1 stays 2:1

    def test_tall_image_fills_the_long_side(self):
        canvas = Canvas(2)
        resized = canvas.resize_img(np.zeros((128, 64, 3), dtype=np.uint8))
        assert resized.shape[:2] == (64, 32)

    def test_both_sides_are_whole_baseplates(self):
        canvas = Canvas(3)  # 96 studs
        resized = canvas.resize_img(np.zeros((100, 237, 3), dtype=np.uint8))
        height, width = resized.shape[:2]
        assert width == 96
        assert height % DIM_BASE == 0 and height > 0

    def test_an_extreme_aspect_ratio_keeps_one_baseplate(self):
        canvas = Canvas(1)
        resized = canvas.resize_img(np.zeros((4, 4000, 3), dtype=np.uint8))
        assert resized.shape[:2] == (DIM_BASE, DIM_BASE)

    def test_short_side_is_letterboxed_not_distorted(self):
        # Canvas(1): size=32. A 100 (h) x 50 (w) source is 2:1, so the resized
        # content should be 32 (h) x 16 (w) -- true to its ratio -- then padded
        # with 8 black columns on each side to reach the full 32x32 canvas.
        canvas = Canvas(1)
        white = np.full((100, 50, 3), 255, dtype=np.uint8)
        resized = canvas.resize_img(white)

        assert resized.shape[:2] == (32, 32)
        assert np.all(resized[:, :8] == 0)  # left pad
        assert np.all(resized[:, 24:] == 0)  # right pad
        assert np.all(resized[:, 8:24] == 255)  # the actual (undistorted) content

    def test_letterbox_padding_is_centred(self):
        canvas = Canvas(1)
        resized = canvas.resize_img(np.full((100, 50, 3), 255, dtype=np.uint8))
        content_cols = np.where(resized[16, :, 0] == 255)[0]
        left_pad, right_pad = content_cols.min(), resized.shape[1] - 1 - content_cols.max()
        assert left_pad == right_pad

    def test_no_padding_needed_when_already_a_whole_number_of_baseplates(self):
        canvas = Canvas(2)  # 64 studs
        resized = canvas.resize_img(np.full((64, 128, 3), 255, dtype=np.uint8))
        assert resized.shape[:2] == (32, 64)
        assert np.all(resized == 255)  # no letterbox bars anywhere


class TestToPreviewImage:
    def test_scales_up_and_preserves_aspect_ratio(self, make_canvas):
        canvas = make_canvas([[1] * DIM_BASE for _ in range(DIM_BASE * 2)])  # 64x32
        preview = canvas.to_preview_image()
        rows, cols = canvas.shape
        assert preview.shape[0] / preview.shape[1] == pytest.approx(rows / cols)
        assert preview.shape[0] > rows and preview.shape[1] > cols

    def test_draws_a_line_at_every_baseplate_boundary(self, two_base_canvas):
        preview = two_base_canvas.to_preview_image()
        # two_base_canvas is one baseplate wide (32 cols) with a second alongside it
        # (64 cols total): a boundary line should cross the midpoint column.
        mid_col = preview.shape[1] // 2
        assert np.all(preview[:, mid_col] == 0)

    def test_regions_away_from_grid_lines_keep_their_brick_colour(self, two_base_canvas):
        preview = two_base_canvas.to_preview_image()
        left_brick, right_brick = two_base_canvas.bricks[0][0], two_base_canvas.bricks[0][-1]
        # Sample well inside each baseplate, away from any grid line.
        quarter_col, three_quarter_col = preview.shape[1] // 4, 3 * preview.shape[1] // 4
        mid_row = preview.shape[0] // 2
        assert tuple(preview[mid_row, quarter_col]) == left_brick.color
        assert tuple(preview[mid_row, three_quarter_col]) == right_brick.color

    def test_raises_on_an_empty_canvas(self):
        with pytest.raises(ValueError, match="empty"):
            Canvas(1).to_preview_image()


class TestFitFromImage:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Could not read image"):
            Canvas(1).fit_from_image(tmp_path / "nope.png")

    def test_source_is_taken_from_the_filename(self, red_blue_image):
        canvas = Canvas(1)
        canvas.fit_from_image(red_blue_image)
        assert canvas.source == "red_blue"
        assert canvas.source_path == red_blue_image

    def test_canvas_is_whole_baseplates(self, red_blue_image):
        canvas = Canvas(2)
        canvas.fit_from_image(red_blue_image)
        rows, cols = canvas.shape
        assert (rows, cols) == (32, 64)

    def test_every_brick_comes_from_the_palette(self, red_blue_image):
        canvas = Canvas(1)
        canvas.fit_from_image(red_blue_image)
        palette = set(canvas.store.available_bricks)
        assert {brick for row in canvas.bricks for brick in row} <= palette

    def test_flat_colours_map_to_their_nearest_palette_colour(self, red_blue_image):
        canvas = Canvas(1)
        canvas.fit_from_image(red_blue_image)
        available = canvas.store.available_bricks
        palette = np.array([brick.color for brick in available])

        def nearest(rgb: tuple[int, int, int]) -> int:
            return available[int(np.argmin(np.linalg.norm(palette - np.array(rgb), axis=1)))].id

        # red_blue_image is 2:1, so on a 1-baseplate (square) canvas it's letterboxed
        # top and bottom; the middle row is unpadded content, flat red on the left
        # and flat blue on the right, so its outermost columns are unaffected by the
        # resize blend.
        middle_row = canvas.bricks[canvas.shape[0] // 2]
        assert middle_row[0].id == nearest((255, 0, 0))
        assert middle_row[-1].id == nearest((0, 0, 255))

    def test_matches_only_against_the_buildable_palette_by_default(self, tmp_path):
        # Pure black sits exactly on a non-buildable decoy ("Speckle
        # Black-Copper" == (0, 0, 0)) rather than on "Black" itself ((5, 19,
        # 29)). The default palette must exclude that decoy so matching still
        # finds the plain colour.
        import cv2
        import numpy as np

        from legome.models.static import COLORS

        path = tmp_path / "black.png"
        cv2.imwrite(str(path), np.zeros((32, 32, 3), dtype=np.uint8))

        canvas = Canvas(1)
        canvas.fit_from_image(path)
        assert list(COLORS)[canvas.bricks[0][0].id] == "Black"

    def test_full_palette_opts_into_speciality_finishes(self, red_blue_image):
        from legome.models.brickstore import BrickStore

        canvas = Canvas(1)
        canvas.fit_from_image(red_blue_image, palette=BrickStore.all_bricks)
        assert all(brick in BrickStore.all_bricks for row in canvas.bricks for brick in row)

    def test_different_source_colours_give_different_bricks(self, red_blue_image):
        canvas = Canvas(1)
        canvas.fit_from_image(red_blue_image)
        middle_row = canvas.bricks[canvas.shape[0] // 2]
        assert middle_row[0] != middle_row[-1]

    def test_fitting_twice_replaces_the_grid_instead_of_appending(self, red_blue_image):
        canvas = Canvas(1)
        canvas.fit_from_image(red_blue_image)
        first = canvas.shape
        canvas.fit_from_image(red_blue_image)
        assert canvas.shape == first

    def test_tall_image_is_taller_than_wide(self, tall_image):
        canvas = Canvas(2)
        canvas.fit_from_image(tall_image)
        rows, cols = canvas.shape
        assert rows > cols

    def test_distribution_accounts_for_every_stud(self, red_blue_image):
        canvas = Canvas(1)
        canvas.fit_from_image(red_blue_image)
        rows, cols = canvas.shape
        assert sum(canvas.get_distribution().values()) == rows * cols

    def test_repr_mentions_the_size(self, red_blue_image):
        canvas = Canvas(1)
        canvas.fit_from_image(red_blue_image)
        assert "Canvas L32" in repr(canvas)
