from __future__ import annotations

from collections import Counter
from itertools import chain
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from legome.models.brick import Brick

# Rows of pixels processed per nearest-colour chunk. Bounds peak memory to roughly
# CHUNK_SIZE * len(palette) * 4 bytes, independent of image size.
CHUNK_SIZE = 8192

# Studs per bold sub-square in a stud map, to help count position while building.
GRID_MAJOR_EVERY = 8


# Floyd-Steinberg error diffusion: (row offset, column offset along the scan
# direction, share of the error).
_DITHER_WEIGHTS = ((0, 1, 7 / 16), (1, -1, 3 / 16), (1, 0, 5 / 16), (1, 1, 1 / 16))


def read_image(path: str | Path) -> np.ndarray | None:
    """Read an image file as a BGR array, like ``cv2.imread``, or None if it
    isn't one. Goes through numpy because ``cv2.imread`` can't open non-ASCII
    paths on Windows (e.g. a photo named "été.jpg")."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def fit_within(img: np.ndarray, max_dimension: int) -> np.ndarray:
    """Shrink an image so its longest side is at most ``max_dimension`` pixels,
    keeping its proportions. An image that already fits comes back unchanged."""
    height, width = img.shape[:2]
    scale = max_dimension / max(height, width)
    if scale >= 1:
        return img
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Convert (N, 3) RGB colours (0-255) to (N, 3) float32 CIELAB (L in 0-100).

    Euclidean distance in CIELAB tracks how different two colours *look* far
    better than it does in RGB, notably for skin tones, greys and dark colours.
    """
    rgb = np.asarray(rgb, dtype=np.float32).reshape(-1, 1, 3) / 255
    if not len(rgb):
        return np.empty((0, 3), dtype=np.float32)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2Lab).reshape(-1, 3)


def nearest_color_indices(
    pixels: np.ndarray, palette: np.ndarray, chunk_size: int = CHUNK_SIZE, perceptual: bool = True
) -> np.ndarray:
    """
    Map each pixel to the index of its nearest colour in a palette, by squared
    Euclidean distance in CIELAB (or in raw RGB if ``perceptual`` is False).

    Real images are full of exact-duplicate pixels, especially once resizing has
    rounded them to whole RGB values, so the distinct colours are deduplicated
    into a small "key" matrix first and solved once each; every pixel then looks
    up its key's answer instead of repeating the same distance calculation. What
    remains is processed in chunks of rows rather than as one
    (pixels x palette x 3) array, so memory stays bounded for large images
    instead of scaling with both sizes at once.

    :param pixels: (N, 3) array of RGB pixels.
    :param palette: (K, 3) array of RGB palette colours.
    :param perceptual: Compare colours in CIELAB rather than raw RGB.
    :return: (N,) array of indices into ``palette``.
    """
    palette = rgb_to_lab(palette) if perceptual else palette.astype(np.float32, copy=False)
    # ||p - c||^2 = ||p||^2 - 2 p.c + ||c||^2 ; ||p||^2 is the same across every
    # palette entry for a given pixel, so it can't change which is nearest and is
    # dropped. The remaining term is one matrix multiply per chunk.
    palette_sq = np.sum(palette**2, axis=1)

    keys, pixel_to_key = np.unique(pixels, axis=0, return_inverse=True)
    pixel_to_key = pixel_to_key.reshape(-1)  # (N, 1) on numpy < 2.0.1; flatten either way.
    keys = rgb_to_lab(keys) if perceptual else keys.astype(np.float32, copy=False)

    key_indices = np.empty(len(keys), dtype=np.intp)
    for start in range(0, len(keys), chunk_size):
        chunk = keys[start : start + chunk_size]
        dist2 = palette_sq - 2 * chunk @ palette.T
        key_indices[start : start + chunk_size] = np.argmin(dist2, axis=1)
    return key_indices[pixel_to_key]


def dither_indices(pixels: np.ndarray, palette: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """
    Map an image to palette indices with Floyd-Steinberg error diffusion: each
    pixel's rounding error is pushed onto its unvisited neighbours, so an area in
    a colour the palette lacks becomes a mix of nearby colours that averages out
    to it from a distance. Works in CIELAB and scans rows in alternating
    directions (serpentine), which avoids diagonal streaks.

    :param pixels: (H, W, 3) RGB image.
    :param palette: (K, 3) array of RGB palette colours.
    :param strength: Share of the error to diffuse; 1.0 is classic Floyd-Steinberg.
    :return: (H, W) array of indices into ``palette``.
    """
    height, width = pixels.shape[:2]
    lab = rgb_to_lab(pixels.reshape(-1, 3)).reshape(height, width, 3).astype(np.float64)
    palette_lab = rgb_to_lab(palette).astype(np.float64)
    indices = np.empty((height, width), dtype=np.intp)

    for y in range(height):
        step = 1 if y % 2 == 0 else -1
        for x in range(width) if step == 1 else range(width - 1, -1, -1):
            pixel = lab[y, x]
            index = int(np.argmin(np.sum((palette_lab - pixel) ** 2, axis=1)))
            indices[y, x] = index
            error = (pixel - palette_lab[index]) * strength
            for dy, dx, share in _DITHER_WEIGHTS:
                ny, nx = y + dy, x + dx * step
                if ny < height and 0 <= nx < width:
                    lab[ny, nx] += error * share
    return indices


def reduce_palette(pixels: np.ndarray, palette: np.ndarray, max_colors: int, keep: tuple[int, ...] = ()) -> np.ndarray:
    """
    Pick the ``max_colors`` palette colours that best represent an image.

    Starts from every colour the image would use with the full palette, then
    greedily drops whichever colour is cheapest to lose -- the smallest increase
    in total CIELAB error once its pixels fall back to their runner-up colour --
    until ``max_colors`` remain. Unlike clustering the image (k-means) and then
    snapping the centroids to bricks, every step only considers real colours.

    :param pixels: (..., 3) RGB image or pixel array.
    :param palette: (K, 3) array of RGB palette colours.
    :param max_colors: How many colours to keep, at least 1.
    :param keep: Palette indices that must survive (e.g. black for a frame).
    :return: Sorted array of at most ``max_colors`` indices into ``palette``.
    """
    if max_colors < 1:
        raise ValueError(f"max_colors must be at least 1, got {max_colors}")
    if len(set(keep)) > max_colors:
        raise ValueError(f"cannot keep {len(set(keep))} colours with max_colors={max_colors}")

    keys, counts = np.unique(pixels.reshape(-1, 3), axis=0, return_counts=True)
    keys_lab, palette_lab = rgb_to_lab(keys), rgb_to_lab(palette)
    dist = np.sum((keys_lab[:, None, :] - palette_lab[None, :, :]) ** 2, axis=2)

    selected = sorted(set(np.argmin(dist, axis=1).tolist()) | set(keep))
    rows = np.arange(len(keys))
    while len(selected) > max_colors:
        sub = dist[:, selected]
        two_best = np.argpartition(sub, 1, axis=1)[:, :2]
        first, second = sub[rows, two_best[:, 0]], sub[rows, two_best[:, 1]]
        best = np.where(first <= second, two_best[:, 0], two_best[:, 1])
        # The cost of dropping each colour: its pixels move to their runner-up.
        cost = np.bincount(best, weights=counts * np.abs(second - first), minlength=len(selected))
        cost[[i for i, index in enumerate(selected) if index in keep]] = np.inf
        selected.pop(int(np.argmin(cost)))
    return np.array(selected, dtype=np.intp)


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
    Return the CSS classes for one cell: a thin border where the neighbouring
    brick differs (the grid's outer edge is always bordered), plus a bold
    border on every ``GRID_MAJOR_EVERY``-stud boundary, to help count position
    while building.

    Each line between two cells is drawn by exactly one of them: the cell
    above or to the left, via its bottom or right border. Only the first row
    and column also draw their top and left edges. Were both cells to draw a
    shared line, a printed PDF can render the two strokes a fraction of a
    pixel apart, which reads as a double line at some zoom levels.
    """
    rows, cols = grid.shape
    value = grid[row, col]

    def differs(r: int, c: int) -> bool:
        return not (0 <= r < rows and 0 <= c < cols) or grid[r, c] != value

    classes = []
    if row == 0:
        classes.append("border-top")
    if differs(row + 1, col):
        classes.append("border-bottom")
    if col == 0:
        classes.append("border-start")
    if differs(row, col + 1):
        classes.append("border-end")

    if row == 0:
        classes.append("grid-major-top")
    if (row + 1) % GRID_MAJOR_EVERY == 0 or row == rows - 1:
        classes.append("grid-major-bottom")
    if col == 0:
        classes.append("grid-major-start")
    if (col + 1) % GRID_MAJOR_EVERY == 0 or col == cols - 1:
        classes.append("grid-major-end")

    return " ".join(classes)
