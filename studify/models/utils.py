from __future__ import annotations

from collections import Counter
from itertools import chain
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from studify.models.brick import Brick

# Rows of pixels processed per nearest-colour chunk. Bounds peak memory to roughly
# CHUNK_SIZE * len(palette) * 4 bytes, independent of image size.
CHUNK_SIZE = 8192

# Studs per bold sub-square in a stud map, to help count position while building.
GRID_MAJOR_EVERY = 8

# When a lone brick counts as noise rather than detail (see remove_stray_bricks).
STRAY_MAX_DISTANCE = 25.0
STRAY_MIN_SUPPORT = 3


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
    # As in nearest_color_indices: ||p||^2 can't change which colour is nearest.
    palette_sq = np.sum(palette_lab**2, axis=1)
    palette_t2 = 2 * palette_lab.T
    indices = np.empty((height, width), dtype=np.intp)

    for y in range(height):
        step = 1 if y % 2 == 0 else -1
        rows = (lab[y], lab[y + 1] if y + 1 < height else None)
        for x in range(width) if step == 1 else range(width - 1, -1, -1):
            pixel = rows[0][x]
            index = int((palette_sq - pixel @ palette_t2).argmin())
            indices[y, x] = index
            error = (pixel - palette_lab[index]) * strength
            for dy, dx, share in _DITHER_WEIGHTS:
                row, nx = rows[dy], x + dx * step
                if row is not None and 0 <= nx < width:
                    row[nx] += error * share
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

    selected = np.array(sorted(set(np.argmin(dist, axis=1).tolist()) | set(keep)), dtype=np.intp)
    if len(selected) <= max_colors:
        return selected

    sub = dist[:, selected]
    protected = np.isin(selected, keep)
    dropped = np.zeros(len(selected), dtype=bool)
    best, second = np.empty(len(keys), dtype=np.intp), np.empty(len(keys), dtype=np.intp)
    best_dist, second_dist = np.empty(len(keys)), np.empty(len(keys))

    def rank(rows: np.ndarray) -> None:
        """Work out the two nearest remaining colours of the given keys."""
        two_best = np.argpartition(sub[rows], 1, axis=1)[:, :2]
        first = np.take_along_axis(sub[rows], two_best, axis=1)
        swap = first[:, 0] > first[:, 1]
        best[rows] = np.where(swap, two_best[:, 1], two_best[:, 0])
        second[rows] = np.where(swap, two_best[:, 0], two_best[:, 1])
        best_dist[rows], second_dist[rows] = first.min(axis=1), first.max(axis=1)

    rank(np.arange(len(keys)))
    for _ in range(len(selected) - max_colors):
        # The cost of dropping each colour: its pixels move to their runner-up.
        cost = np.bincount(best, weights=counts * (second_dist - best_dist), minlength=len(selected))
        cost[protected | dropped] = np.inf
        drop = int(np.argmin(cost))
        dropped[drop] = True
        sub[:, drop] = np.inf
        # Only the keys that ranked this colour first or second can change.
        affected = np.flatnonzero((best == drop) | (second == drop))
        if len(affected):
            rank(affected)
    return selected[~dropped]


def remove_stray_bricks(
    indices: np.ndarray,
    palette: np.ndarray,
    max_distance: float = STRAY_MAX_DISTANCE,
    min_support: int = STRAY_MIN_SUPPORT,
) -> np.ndarray:
    """
    Merge isolated bricks into their surroundings: a brick none of whose eight
    neighbours shares its colour takes the colour most of them have. Such bricks
    read as noise, and each odd colour is one more to buy.

    A brick stays if too few neighbours agree on a colour (a busy area, not a
    region), or if that colour looks too different (a real detail, like the glint
    in an eye).

    :param indices: (H, W) array of indices into ``palette``.
    :param palette: (K, 3) array of RGB palette colours.
    :param max_distance: Largest CIELAB distance a brick may change colour by.
    :param min_support: How many neighbours must share the replacement colour.
    :return: A new (H, W) array of indices.
    """
    palette_lab = rgb_to_lab(palette)
    windows = np.lib.stride_tricks.sliding_window_view(np.pad(indices, 1, constant_values=-1), (3, 3))
    neighbours = np.delete(windows.reshape(*indices.shape, 9), 4, axis=2)
    cleaned = indices.copy()
    for y, x in np.argwhere(~(neighbours == indices[..., None]).any(axis=2)):
        around = neighbours[y, x]
        counts = np.bincount(around[around >= 0], minlength=1)
        replacement = int(np.argmax(counts))
        distance = np.linalg.norm(palette_lab[indices[y, x]] - palette_lab[replacement])
        if counts[replacement] >= min_support and distance <= max_distance:
            cleaned[y, x] = replacement
    return cleaned


def get_distribution(bricks: list[list[Brick]]) -> dict[int, int]:
    """
    Count how many of each brick a grid needs.

    :param bricks: Brick grid, row-major.
    :return: A mapping of brick id to its count, ordered by id.
    """
    counts = Counter(brick.id for brick in chain.from_iterable(bricks))
    return dict(sorted(counts.items()))


def _merged_cell(grid: np.ndarray, regions: np.ndarray, row: int, col: int) -> str:
    """Return the cell for the rectangle of ``regions`` whose top-left stud is here.
    It takes its top and left edges from that stud and its bottom and right edges
    from the opposite one (see ``get_class``)."""
    region = regions[row, col]
    height = int(np.argmin(np.append(regions[row:, col] == region, False)))
    width = int(np.argmin(np.append(regions[row, col:] == region, False)))
    first = get_class(regions, row, col).split()
    last = get_class(regions, row + height - 1, col + width - 1).split()
    classes = [name for name in first if name.endswith(("top", "start"))]
    classes += [name for name in last if name.endswith(("bottom", "end"))]
    spans = (f' rowspan="{height}"' if height > 1 else "") + (f' colspan="{width}"' if width > 1 else "")
    return f'<td{spans} class="c{grid[row, col]} {" ".join(classes)}">{grid[row, col]}</td>'


def make_map(df: pd.DataFrame, regions: np.ndarray | None = None) -> str:
    """
    Render a grid of brick ids as an HTML table (sizing and borders are entirely
    CSS-driven, see ``.stud-map`` in style.css), with 1-based row and column
    headers and a border drawn around each run of same-coloured bricks. Each cell
    has a ``c<id>`` class, which the guide can use to tint it with its brick's colour.

    :param df: Grid of brick ids, one row per canvas row.
    :param regions: Draw each of these numbered rectangles (e.g. each plate) as one
        merged cell showing its brick id, rather than one cell per stud.
    :return: An HTML ``<table>`` fragment.
    """
    grid = np.asarray(df, dtype=int)
    rows, cols = grid.shape

    header = "".join(f"<th>{col + 1}</th>" for col in range(cols))
    body = ""
    seen = set()
    for row in range(rows):
        cells = ""
        for col in range(cols):
            if regions is None:
                cells += f'<td class="c{grid[row, col]} {get_class(grid, row, col)}">{grid[row, col]}</td>'
            elif regions[row, col] not in seen:
                seen.add(regions[row, col])
                cells += _merged_cell(grid, regions, row, col)
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
