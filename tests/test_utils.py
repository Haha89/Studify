from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from studify.models.brick import Brick
from studify.models.utils import (
    GRID_MAJOR_EVERY,
    dither_indices,
    get_class,
    get_distribution,
    make_map,
    nearest_color_indices,
    reduce_palette,
    remove_stray_bricks,
    rgb_to_lab,
)


def test_get_distribution_counts_every_brick():
    bricks = [[Brick(1, (0, 0, 0)), Brick(2, (1, 1, 1))], [Brick(1, (0, 0, 0)), Brick(1, (0, 0, 0))]]
    assert get_distribution(bricks) == {1: 3, 2: 1}


def test_get_distribution_is_ordered_by_id_with_plain_ints():
    bricks = [[Brick(5, (0, 0, 0)), Brick(0, (0, 0, 0)), Brick(3, (0, 0, 0))]]
    distribution = get_distribution(bricks)
    assert list(distribution) == [0, 3, 5]
    assert all(type(key) is int and type(value) is int for key, value in distribution.items())


def test_get_distribution_of_empty_canvas():
    assert get_distribution([]) == {}


@pytest.fixture
def grid():
    return pd.DataFrame([[1, 1], [2, 3]])


def test_make_map_closes_every_row(grid):
    html = make_map(grid)
    assert html.count("<tr>") == html.count("</tr>") == 3  # header + two body rows


def test_make_map_has_one_cell_per_brick_plus_headers(grid):
    html = make_map(grid)
    assert html.count("<td") == 4
    assert html.count("</th>") == 1 + 2 + 2  # corner + 2 column headers + 2 row headers


def test_make_map_headers_are_one_based(grid):
    html = make_map(grid)
    assert ">1</th>" in html and ">2</th>" in html
    assert ">3</th>" not in html


def test_make_map_renders_brick_ids(grid):
    html = make_map(grid)
    for brick_id in (1, 2, 3):
        assert f">{brick_id}</td>" in html


def test_make_map_has_no_inline_styling(grid):
    # Sizing and colour are entirely CSS-driven (see .stud-map in style.css), not
    # baked into the HTML, so the guide's stylesheet is the only place to tune them.
    html = make_map(grid)
    assert "style=" not in html
    assert 'class="stud-map"' in html


def test_get_class_borders_the_grid_edges():
    single = np.array([[7]])
    classes = set(get_class(single, 0, 0).split())
    assert {"border-top", "border-bottom", "border-start", "border-end"} <= classes


def test_get_class_omits_borders_between_identical_neighbours():
    same = np.array([[4, 4, 4], [4, 4, 4], [4, 4, 4]])
    # The centre cell: away from every edge and (since GRID_MAJOR_EVERY > 3) from
    # every major boundary too, so nothing should be drawn around it at all.
    assert get_class(same, 1, 1) == ""


def test_get_class_borders_only_where_the_colour_changes():
    # A vertical stripe of 9s: its left edge is drawn by the 0 to its left, its
    # right edge by the stripe itself, each as a right border.
    grid = np.array([[0, 9, 0], [0, 9, 0], [0, 9, 0]])
    assert {"border-start", "border-end"} <= set(get_class(grid, 1, 0).split())  # also the grid's left edge
    assert get_class(grid, 1, 1) == "border-end"


def _drawn_edges(grid: np.ndarray, prefix: str) -> list[tuple]:
    """Every line segment the classes draw, as ("h"/"v", row, col) in grid-line
    coordinates: ("h", 2, 3) is the horizontal segment above row 2 at column 3."""
    rows, cols = grid.shape
    edges = []
    for r in range(rows):
        for c in range(cols):
            classes = get_class(grid, r, c).split()
            if f"{prefix}-top" in classes:
                edges.append(("h", r, c))
            if f"{prefix}-bottom" in classes:
                edges.append(("h", r + 1, c))
            if f"{prefix}-start" in classes:
                edges.append(("v", r, c))
            if f"{prefix}-end" in classes:
                edges.append(("v", r, c + 1))
    return edges


def test_every_line_is_drawn_by_exactly_one_cell():
    # Shared lines drawn by both neighbours showed up doubled in printed PDFs.
    rng = np.random.default_rng(5)
    grid = rng.integers(0, 3, size=(GRID_MAJOR_EVERY * 2 + 3, GRID_MAJOR_EVERY + 5))
    rows, cols = grid.shape
    for prefix in ("border", "grid-major"):
        edges = _drawn_edges(grid, prefix)
        assert len(edges) == len(set(edges)), prefix

    # And thin lines still mark exactly the colour changes plus the outer edge.
    expected = set()
    for r in range(rows + 1):
        for c in range(cols):
            if r in (0, rows) or grid[r - 1, c] != grid[r, c]:
                expected.add(("h", r, c))
    for r in range(rows):
        for c in range(cols + 1):
            if c in (0, cols) or grid[r, c - 1] != grid[r, c]:
                expected.add(("v", r, c))
    assert set(_drawn_edges(grid, "border")) == expected


class TestGridMajorLines:
    """The bold sub-square lines drawn every GRID_MAJOR_EVERY studs, independent of
    colour, so a builder can count position on a large, same-coloured expanse."""

    @staticmethod
    def _uniform_grid(size: int) -> np.ndarray:
        return np.zeros((size, size), dtype=int)  # one flat colour: isolates the major-line logic from borders

    def test_marks_the_grid_s_outer_edges(self):
        grid = self._uniform_grid(GRID_MAJOR_EVERY)
        last = GRID_MAJOR_EVERY - 1
        assert "grid-major-top" in get_class(grid, 0, 2).split()
        assert "grid-major-bottom" in get_class(grid, last, 2).split()
        assert "grid-major-start" in get_class(grid, 2, 0).split()
        assert "grid-major-end" in get_class(grid, 2, last).split()

    def test_marks_an_internal_boundary_between_two_sub_squares_once(self):
        grid = self._uniform_grid(GRID_MAJOR_EVERY * 2)
        mid = GRID_MAJOR_EVERY
        assert "grid-major-bottom" in get_class(grid, mid - 1, 2).split()
        assert "grid-major-top" not in get_class(grid, mid, 2).split()  # drawn by the cell above

    def test_omits_major_lines_away_from_any_boundary(self):
        grid = self._uniform_grid(GRID_MAJOR_EVERY * 2)
        middle = GRID_MAJOR_EVERY // 2
        classes = get_class(grid, middle, middle).split()
        assert not any(c.startswith("grid-major") for c in classes)


class TestNearestColorIndices:
    def test_rgb_mode_matches_brute_force_nearest_neighbour(self):
        rng = np.random.default_rng(0)
        palette = rng.integers(0, 256, size=(20, 3)).astype(np.float32)
        pixels = rng.integers(0, 256, size=(500, 3)).astype(np.uint8)

        got = nearest_color_indices(pixels, palette, perceptual=False)

        brute = np.argmin(np.linalg.norm(palette[:, None, :] - pixels[None, :, :].astype(np.float32), axis=2), axis=0)
        assert np.array_equal(got, brute)

    def test_perceptual_mode_matches_brute_force_in_lab(self):
        rng = np.random.default_rng(0)
        palette = rng.integers(0, 256, size=(20, 3)).astype(np.float32)
        pixels = rng.integers(0, 256, size=(500, 3)).astype(np.uint8)

        got = nearest_color_indices(pixels, palette)

        lab_palette, lab_pixels = rgb_to_lab(palette), rgb_to_lab(pixels)
        brute = np.argmin(np.linalg.norm(lab_palette[:, None, :] - lab_pixels[None, :, :], axis=2), axis=0)
        assert np.array_equal(got, brute)

    def test_perceptual_mode_can_disagree_with_rgb(self):
        # Over many random colours, CIELAB and raw RGB must pick a different
        # nearest colour at least some of the time, or the option does nothing.
        rng = np.random.default_rng(4)
        palette = rng.integers(0, 256, size=(12, 3)).astype(np.float32)
        pixels = rng.integers(0, 256, size=(500, 3)).astype(np.uint8)
        lab = nearest_color_indices(pixels, palette)
        rgb = nearest_color_indices(pixels, palette, perceptual=False)
        assert (lab != rgb).any()

    def test_duplicate_pixels_get_the_same_answer(self):
        palette = np.array([[0, 0, 0], [255, 255, 255]], dtype=np.float32)
        pixels = np.array([[10, 10, 10], [250, 250, 250], [10, 10, 10]], dtype=np.uint8)
        assert list(nearest_color_indices(pixels, palette)) == [0, 1, 0]

    def test_chunking_does_not_change_the_result(self):
        rng = np.random.default_rng(1)
        palette = rng.integers(0, 256, size=(10, 3)).astype(np.float32)
        pixels = rng.integers(0, 256, size=(300, 3)).astype(np.uint8)

        whole = nearest_color_indices(pixels, palette, chunk_size=10_000)
        chunked = nearest_color_indices(pixels, palette, chunk_size=7)
        assert np.array_equal(whole, chunked)

    def test_handles_an_empty_pixel_array(self):
        palette = np.array([[0, 0, 0]], dtype=np.float32)
        assert len(nearest_color_indices(np.empty((0, 3), dtype=np.uint8), palette)) == 0


def test_rgb_to_lab_known_values():
    lab = rgb_to_lab(np.array([[0, 0, 0], [255, 255, 255]]))
    assert lab[0] == pytest.approx([0, 0, 0], abs=0.5)
    assert lab[1] == pytest.approx([100, 0, 0], abs=0.5)


class TestDitherIndices:
    def test_exact_palette_colours_are_left_alone(self):
        palette = np.array([[0, 0, 0], [255, 255, 255]], dtype=np.float32)
        img = np.zeros((4, 4, 3), dtype=np.uint8)
        img[:, 2:] = 255
        assert np.array_equal(dither_indices(img, palette), np.array([[0, 0, 1, 1]] * 4))

    def test_mid_grey_becomes_a_mix_of_black_and_white(self):
        palette = np.array([[0, 0, 0], [255, 255, 255]], dtype=np.float32)
        grey = np.full((16, 16, 3), 119, dtype=np.uint8)  # ~50% lightness
        indices = dither_indices(grey, palette)
        # Plain nearest-colour would make it one flat colour; dithering mixes both.
        assert 0.3 < indices.mean() < 0.7

    def test_zero_strength_is_plain_nearest_colour(self):
        rng = np.random.default_rng(2)
        palette = rng.integers(0, 256, size=(6, 3)).astype(np.float32)
        img = rng.integers(0, 256, size=(8, 8, 3)).astype(np.uint8)
        plain = nearest_color_indices(img.reshape(-1, 3), palette).reshape(8, 8)
        assert np.array_equal(dither_indices(img, palette, strength=0), plain)


class TestReducePalette:
    PALETTE = np.array([[0, 0, 0], [255, 255, 255], [200, 0, 0], [0, 0, 200], [210, 10, 10]], dtype=np.float32)

    def _image(self):
        img = np.zeros((10, 10, 3), dtype=np.uint8)  # mostly black
        img[:, 6:] = (255, 255, 255)
        img[0, 0] = (200, 0, 0)  # a single red pixel
        img[1, 0] = (0, 0, 200)  # a single blue pixel
        return img

    def test_keeps_at_most_max_colors(self):
        assert len(reduce_palette(self._image(), self.PALETTE, 2)) == 2

    def test_drops_the_least_used_colours_first(self):
        assert list(reduce_palette(self._image(), self.PALETTE, 2)) == [0, 1]

    def test_never_adds_unused_colours(self):
        assert list(reduce_palette(self._image(), self.PALETTE, 10)) == [0, 1, 2, 3]

    def test_keep_survives_even_when_rarely_used(self):
        assert list(reduce_palette(self._image(), self.PALETTE, 2, keep=(3,))) == [0, 3]

    def test_rejects_bad_limits(self):
        with pytest.raises(ValueError, match="at least 1"):
            reduce_palette(self._image(), self.PALETTE, 0)
        with pytest.raises(ValueError, match="cannot keep"):
            reduce_palette(self._image(), self.PALETTE, 1, keep=(0, 1))


def _reduce_palette_one_step_at_a_time(pixels, palette, max_colors, keep=()):
    """The definition of reduce_palette, without its bookkeeping: everything is
    worked out afresh for each colour dropped."""
    keys, counts = np.unique(pixels.reshape(-1, 3), axis=0, return_counts=True)
    dist = np.sum((rgb_to_lab(keys)[:, None, :] - rgb_to_lab(palette)[None, :, :]) ** 2, axis=2)
    selected = sorted(set(np.argmin(dist, axis=1).tolist()) | set(keep))
    while len(selected) > max_colors:
        ordered = np.sort(dist[:, selected], axis=1)
        best = np.argmin(dist[:, selected], axis=1)
        cost = np.bincount(best, weights=counts * (ordered[:, 1] - ordered[:, 0]), minlength=len(selected))
        cost[[i for i, index in enumerate(selected) if index in keep]] = np.inf
        selected.pop(int(np.argmin(cost)))
    return np.array(selected)


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("max_colors", [1, 3, 8])
def test_reduce_palette_matches_its_definition(seed, max_colors):
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 256, (200, 3))
    palette = rng.integers(0, 256, (30, 3))
    keep = (int(np.argmin(np.sum((palette - pixels[0]) ** 2, axis=1))),) if max_colors > 1 else ()
    expected = _reduce_palette_one_step_at_a_time(pixels, palette, max_colors, keep)
    assert np.array_equal(reduce_palette(pixels, palette, max_colors, keep), expected)


class TestRemoveStrayBricks:
    # Black, white, then a mid grey and a light grey close to white.
    PALETTE = np.array([(0, 0, 0), (255, 255, 255), (128, 128, 128), (235, 235, 235)])

    def test_a_lone_brick_close_to_its_surroundings_is_merged(self):
        grid = np.ones((5, 5), dtype=np.intp)
        grid[2, 2] = 3
        assert (remove_stray_bricks(grid, self.PALETTE) == 1).all()

    def test_a_lone_brick_unlike_its_surroundings_is_a_detail_and_stays(self):
        grid = np.ones((5, 5), dtype=np.intp)
        grid[2, 2] = 0
        assert np.array_equal(remove_stray_bricks(grid, self.PALETTE), grid)

    def test_a_pair_of_touching_bricks_is_not_stray(self):
        grid = np.ones((5, 5), dtype=np.intp)
        grid[2, 2] = grid[3, 3] = 3  # Diagonal neighbours count.
        assert np.array_equal(remove_stray_bricks(grid, self.PALETTE), grid)

    def test_a_brick_stays_if_too_few_neighbours_agree(self):
        grid = np.array([[0, 1, 0], [2, 3, 2], [0, 2, 0]], dtype=np.intp)
        grid[0, 1] = 1
        assert remove_stray_bricks(grid, self.PALETTE, max_distance=1000, min_support=5)[1, 1] == 3

    def test_corners_only_count_their_real_neighbours(self):
        grid = np.ones((3, 3), dtype=np.intp)
        grid[0, 0] = 3
        assert remove_stray_bricks(grid, self.PALETTE)[0, 0] == 1

    def test_the_input_is_left_untouched(self):
        grid = np.ones((3, 3), dtype=np.intp)
        grid[1, 1] = 3
        remove_stray_bricks(grid, self.PALETTE)
        assert grid[1, 1] == 3
