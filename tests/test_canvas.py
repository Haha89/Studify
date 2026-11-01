from __future__ import annotations

import cv2
import numpy as np
import pytest

from studify.models.brick import Brick
from studify.models.brickstore import BrickStore
from studify.models.canvas import FRAME_COLOR, Canvas
from studify.models.static import COLORS, DIM_BASE


def test_size_is_expressed_in_studs():
    assert Canvas(3).size == 3 * DIM_BASE


@pytest.mark.parametrize("bases", [0, -1])
def test_rejects_a_canvas_smaller_than_one_baseplate(bases):
    with pytest.raises(ValueError, match="at least 1"):
        Canvas(bases)


def test_empty_canvas_has_no_shape_cost_or_bricks():
    canvas = Canvas(1)
    assert canvas.shape == (0, 0)
    assert canvas.baseplate_count == 0
    assert canvas.get_price().high == 0
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


def test_baseplate_count_rounds_partial_plates_up(make_canvas):
    assert make_canvas([[1, 1], [1, 1]]).baseplate_count == 1
    assert make_canvas([[1] * (DIM_BASE + 1)] * DIM_BASE).baseplate_count == 2


def test_price_covers_the_bricks_and_the_baseplates(make_canvas):
    canvas = make_canvas([[1] * DIM_BASE] * DIM_BASE)  # one baseplate of brick #1
    price = canvas.get_price("official", "EUR")
    assert price.baseplate_count == 1
    assert price.low == pytest.approx(price.bricks_low + price.baseplates)
    assert price.high > price.low > price.baseplates > 0


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


class TestFrame:
    @pytest.mark.parametrize("frame", [-1, DIM_BASE // 2])
    def test_rejects_a_frame_too_wide_for_a_baseplate(self, frame):
        with pytest.raises(ValueError, match="frame"):
            Canvas(1, frame=frame)

    @pytest.mark.parametrize("shape", [(64, 128), (128, 64), (100, 237), (50, 50)])
    def test_frame_is_uniform_on_all_four_sides(self, shape):
        canvas = Canvas(2, frame=2)
        resized = canvas.resize_img(np.full((*shape, 3), 255, dtype=np.uint8))

        height, width = resized.shape[:2]
        assert height % DIM_BASE == 0 and width % DIM_BASE == 0
        assert max(height, width) == 2 * DIM_BASE
        assert np.all(resized[:2] == FRAME_COLOR) and np.all(resized[-2:] == FRAME_COLOR)
        assert np.all(resized[:, :2] == FRAME_COLOR) and np.all(resized[:, -2:] == FRAME_COLOR)
        assert np.all(resized[2:-2, 2:-2] == 255)  # image fills the inside, no letterbox bars

    def test_image_is_centre_cropped_to_fit_inside_the_frame(self):
        # A wide 32x96 source into one baseplate: the middle third (white)
        # survives, the black left and right thirds are cropped away.
        src = np.zeros((32, 96, 3), dtype=np.uint8)
        src[:, 32:64] = 255
        resized = Canvas(1, frame=1).resize_img(src)

        assert resized.shape[:2] == (DIM_BASE, DIM_BASE)
        assert np.all(resized[1:-1, 1:-1] == 255)

    def test_frame_maps_to_black_bricks(self, red_blue_image):
        canvas = Canvas(1, frame=3)
        canvas.fit_from_image(red_blue_image)
        ids = canvas.get_brick_ids()
        black = canvas.store.available_bricks[0].id  # "Black" is the first colour
        assert np.all(ids[:3] == black) and np.all(ids[-3:] == black)
        assert np.all(ids[:, :3] == black) and np.all(ids[:, -3:] == black)
        assert not np.all(ids[3:-3, 3:-3] == black)


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
        available = canvas.store.palette("buyable")
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

        from studify.models.static import COLORS

        path = tmp_path / "black.png"
        cv2.imwrite(str(path), np.zeros((32, 32, 3), dtype=np.uint8))

        canvas = Canvas(1)
        canvas.fit_from_image(path)
        assert list(COLORS)[canvas.bricks[0][0].id] == "Black"

    def test_full_palette_opts_into_speciality_finishes(self, red_blue_image):
        from studify.models.brickstore import BrickStore

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


class TestPaletteOptions:
    def test_max_colors_caps_the_distinct_bricks(self, tmp_path):
        rng = np.random.default_rng(3)
        path = tmp_path / "noise.png"
        cv2.imwrite(str(path), rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8))
        canvas = Canvas(1)
        canvas.fit_from_image(path, max_colors=5)
        assert len(canvas.get_distribution()) <= 5

    def test_max_colors_keeps_the_frame_black(self, tmp_path):
        path = tmp_path / "white.png"
        cv2.imwrite(str(path), np.full((64, 64, 3), 255, dtype=np.uint8))
        canvas = Canvas(1, frame=1)
        canvas.fit_from_image(path, max_colors=1)
        assert set(np.unique(canvas.get_brick_ids())) == {BrickStore.bricks_named(["Black"])[0].id}

    def test_dither_leaves_the_letterbox_untouched(self, tmp_path):
        path = tmp_path / "grey.png"
        cv2.imwrite(str(path), np.full((64, 32, 3), 119, dtype=np.uint8))  # tall: letterboxed
        palette = BrickStore.bricks_named(["Black", "White"])
        canvas = Canvas(1)
        canvas.fit_from_image(path, palette=palette, dither=True)

        top, bottom, left, right = canvas.content_box
        ids = canvas.get_brick_ids()
        black, white = palette[0].id, palette[1].id
        assert np.all(ids[:, :left] == black) and np.all(ids[:, right:] == black)
        inside = ids[top:bottom, left:right]
        assert (inside == black).any() and (inside == white).any()

    def test_dither_leaves_the_frame_untouched(self, tmp_path):
        path = tmp_path / "grey.png"
        cv2.imwrite(str(path), np.full((64, 64, 3), 119, dtype=np.uint8))
        palette = BrickStore.bricks_named(["Black", "White"])
        canvas = Canvas(1, frame=2)
        canvas.fit_from_image(path, palette=palette, dither=True)

        ids = canvas.get_brick_ids()
        black = palette[0].id
        assert np.all(ids[:2] == black) and np.all(ids[-2:] == black)
        assert np.all(ids[:, :2] == black) and np.all(ids[:, -2:] == black)


def test_bricks_named_is_case_insensitive_and_deduplicated():
    bricks = BrickStore.bricks_named(["black", " White", "BLACK"])
    assert [b.color for b in bricks] == [(5, 19, 29), (255, 255, 255)]


def test_bricks_named_lists_every_unknown_name():
    with pytest.raises(ValueError, match="Foo, Bar"):
        BrickStore.bricks_named(["Black", "Foo", "Bar"])


def test_fit_from_image_reads_non_ascii_paths(tmp_path):
    # cv2.imread can't open these on Windows; Canvas must still manage.
    path = tmp_path / "photo été ü.png"
    ok, png = cv2.imencode(".png", np.full((32, 32, 3), 255, dtype=np.uint8))
    path.write_bytes(png.tobytes())
    canvas = Canvas(1)
    canvas.fit_from_image(path)
    assert canvas.shape == (DIM_BASE, DIM_BASE)


class TestCleanStrays:
    WHITE, LIGHT_GREY = Brick(0, (255, 255, 255)), Brick(1, (235, 235, 235))

    @pytest.fixture
    def speck(self, tmp_path):
        """A white 32x32 picture with one light grey pixel."""
        img = np.full((32, 32, 3), 255, dtype=np.uint8)
        img[10, 10] = 235
        path = tmp_path / "speck.png"
        cv2.imwrite(str(path), img)
        return path

    def test_merges_a_stray_brick_by_default(self, speck):
        canvas = Canvas(1)
        canvas.fit_from_image(speck, palette=[self.WHITE, self.LIGHT_GREY])
        assert canvas.get_distribution() == {0: 32 * 32}

    def test_can_be_turned_off(self, speck):
        canvas = Canvas(1)
        canvas.fit_from_image(speck, palette=[self.WHITE, self.LIGHT_GREY], clean_strays=False)
        assert canvas.get_distribution() == {0: 32 * 32 - 1, 1: 1}

    def test_a_dithered_mosaic_keeps_its_scattered_bricks(self, speck, monkeypatch):
        def fail(*args):
            raise AssertionError("dithered bricks must not be cleaned")

        monkeypatch.setattr("studify.models.canvas.remove_stray_bricks", fail)
        Canvas(1).fit_from_image(speck, palette=[self.WHITE, self.LIGHT_GREY], dither=True)

    def test_the_frame_is_left_alone(self, speck):
        black = Brick(2, COLORS["Black"])
        canvas = Canvas(1, frame=2)
        canvas.fit_from_image(speck, palette=[self.WHITE, self.LIGHT_GREY, black])
        ids = canvas.get_brick_ids()
        assert (ids[:2] == 2).all() and (ids[-2:] == 2).all() and (ids[:, :2] == 2).all() and (ids[:, -2:] == 2).all()
