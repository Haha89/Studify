from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from legome.models.brick import Brick
from legome.models.utils import GRID_MAJOR_EVERY, get_class, get_distribution, make_map, nearest_color_indices


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
    # A vertical stripe of 9s: the centre brick is bordered left and right only.
    grid = np.array([[0, 9, 0], [0, 9, 0], [0, 9, 0]])
    assert get_class(grid, 1, 1) == "border-start border-end"


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

    def test_marks_an_internal_boundary_between_two_sub_squares(self):
        grid = self._uniform_grid(GRID_MAJOR_EVERY * 2)
        mid = GRID_MAJOR_EVERY
        assert "grid-major-bottom" in get_class(grid, mid - 1, 2).split()
        assert "grid-major-top" in get_class(grid, mid, 2).split()

    def test_omits_major_lines_away_from_any_boundary(self):
        grid = self._uniform_grid(GRID_MAJOR_EVERY * 2)
        middle = GRID_MAJOR_EVERY // 2
        classes = get_class(grid, middle, middle).split()
        assert not any(c.startswith("grid-major") for c in classes)


class TestNearestColorIndices:
    def test_matches_brute_force_nearest_neighbour(self):
        rng = np.random.default_rng(0)
        palette = rng.integers(0, 256, size=(20, 3)).astype(np.float32)
        pixels = rng.integers(0, 256, size=(500, 3)).astype(np.uint8)

        got = nearest_color_indices(pixels, palette)

        brute = np.argmin(np.linalg.norm(palette[:, None, :] - pixels[None, :, :].astype(np.float32), axis=2), axis=0)
        assert np.array_equal(got, brute)

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
