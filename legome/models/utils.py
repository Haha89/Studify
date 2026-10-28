from __future__ import annotations

from collections import Counter
from itertools import chain

import numpy as np
import pandas as pd

from legome.models.brick import Brick

# Rows of pixels processed per nearest-colour chunk. Bounds peak memory to roughly
# CHUNK_SIZE * len(palette) * 4 bytes, independent of image size.
CHUNK_SIZE = 8192

# Studs per bold sub-square in a stud map, to help count position while building.
GRID_MAJOR_EVERY = 8


def nearest_color_indices(pixels: np.ndarray, palette: np.ndarray, chunk_size: int = CHUNK_SIZE) -> np.ndarray:
    """
    Map each pixel to the index of its nearest colour in a palette, by squared
    Euclidean RGB distance.

    Real images are full of exact-duplicate pixels, especially once resizing has
    rounded them to whole RGB values, so the distinct colours are deduplicated
    into a small "key" matrix first and solved once each; every pixel then looks
    up its key's answer instead of repeating the same distance calculation. What
    remains is processed in chunks of rows rather than as one
    (pixels x palette x 3) array, so memory stays bounded for large images
    instead of scaling with both sizes at once.

    :param pixels: (N, 3) array of RGB pixels.
    :param palette: (K, 3) array of RGB palette colours.
    :return: (N,) array of indices into ``palette``.
    """
    palette = palette.astype(np.float32, copy=False)
    # ||p - c||^2 = ||p||^2 - 2 p.c + ||c||^2 ; ||p||^2 is the same across every
    # palette entry for a given pixel, so it can't change which is nearest and is
    # dropped. The remaining term is one matrix multiply per chunk.
    palette_sq = np.sum(palette**2, axis=1)

    keys, pixel_to_key = np.unique(pixels, axis=0, return_inverse=True)
    pixel_to_key = pixel_to_key.reshape(-1)  # (N, 1) on numpy < 2.0.1; flatten either way.
    keys = keys.astype(np.float32, copy=False)

    key_indices = np.empty(len(keys), dtype=np.intp)
    for start in range(0, len(keys), chunk_size):
        chunk = keys[start : start + chunk_size]
        dist2 = palette_sq - 2 * chunk @ palette.T
        key_indices[start : start + chunk_size] = np.argmin(dist2, axis=1)
    return key_indices[pixel_to_key]


def get_distribution(bricks: list[list[Brick]]) -> dict[int, int]:
    """
    Count how many of each brick a grid needs.

    :param bricks: Brick grid, row-major.
    :return: A mapping of brick id to its count, ordered by id.
    """
    counts = Counter(brick.id for brick in chain.from_iterable(bricks))
    return dict(sorted(counts.items()))


def make_map(df: pd.DataFrame) -> str:
    """
    Render a grid of brick ids as an HTML table (sizing and borders are entirely
    CSS-driven, see ``.stud-map`` in style.css), with 1-based row and column
    headers and a border drawn around each run of same-coloured bricks.

    :param df: Grid of brick ids, one row per canvas row.
    :return: An HTML ``<table>`` fragment.
    """
    grid = np.asarray(df, dtype=int)
    rows, cols = grid.shape

    header = "".join(f"<th>{col + 1}</th>" for col in range(cols))
    body = ""
    for row in range(rows):
        cells = "".join(f'<td class="{get_class(grid, row, col)}">{grid[row, col]}</td>' for col in range(cols))
        body += f"<tr><th>{row + 1}</th>{cells}</tr>"

    return f'<table class="stud-map"><thead><tr><th></th>{header}</tr></thead><tbody>{body}</tbody></table>'


def get_class(grid: np.ndarray, row: int, col: int) -> str:
    """
    Return the CSS classes for one cell: a thin border on every side where the
    neighbouring brick differs (cells on the edge of the grid are always
    bordered on that side), plus a bold border on every side that falls on a
    ``GRID_MAJOR_EVERY``-stud boundary, to help count position while building.
    """
    rows, cols = grid.shape
    value = grid[row, col]

    def differs(r: int, c: int) -> bool:
        return not (0 <= r < rows and 0 <= c < cols) or grid[r, c] != value

    classes = []
    if differs(row - 1, col):
        classes.append("border-top")
    if differs(row + 1, col):
        classes.append("border-bottom")
    if differs(row, col - 1):
        classes.append("border-start")
    if differs(row, col + 1):
        classes.append("border-end")

    if row % GRID_MAJOR_EVERY == 0:
        classes.append("grid-major-top")
    if (row + 1) % GRID_MAJOR_EVERY == 0:
        classes.append("grid-major-bottom")
    if col % GRID_MAJOR_EVERY == 0:
        classes.append("grid-major-start")
    if (col + 1) % GRID_MAJOR_EVERY == 0:
        classes.append("grid-major-end")

    return " ".join(classes)
