"""Draw a mosaic as if built from smooth tiles, lit from the top left.

``render_tiles`` is an impression of a build from plates with studs: each plate is
drawn as one flat tile, with a dark seam around it and a slightly raised edge.
``render_round`` shows a build from round tiles, on a dark baseplate.
"""

from __future__ import annotations

import cv2
import numpy as np

# Pixels per stud: enough for the shading to show, without a huge image for a large
# mosaic. Between these bounds, the image's longest side aims for TARGET_DIMENSION.
MIN_SCALE = 16
MAX_SCALE = 40
TARGET_DIMENSION = 2400

SEAM_SHADE = 0.35
LIT_EDGE_SHADE = 1.12
SHADOW_EDGE_SHADE = 0.78
# Added to lit edges, because no shading factor can make black any lighter.
LIT_EDGE_GLOW = 22

# What shows between round tiles: a dark baseplate.
ROUND_BASE_COLOR = (32, 34, 38)
ROUND_RADIUS = 0.46  # Of a stud's width.
ROUND_RIM = 0.88  # Where a tile's rim starts, as a share of its radius.
ROUND_RIM_SHADE = 0.82
ROUND_RIM_GLOW = 18
ROUND_CAST_SHADOW = 0.6  # How much a tile's shadow darkens the baseplate.
ROUND_CAST_OFFSET = 0.03  # Of a stud's width, down and right.
ROUND_LIGHT_SLOPE = 0.1  # Brightness change from a tile's centre to its edge, along the light.
SUPERSAMPLING = 4  # Smooths the tiles' edges.


def tile_scale(rows: int, cols: int) -> int:
    """Return how many pixels per stud to draw a mosaic of this size at."""
    return min(MAX_SCALE, max(MIN_SCALE, TARGET_DIMENSION // max(rows, cols)))


def _band(studs: np.ndarray, scale: int, start: int, stop: int, axis: int) -> np.ndarray:
    """Return a pixel mask holding, in each of these studs, the lines from ``start``
    to ``stop`` along an axis (0: rows, 1: columns)."""
    cell = np.zeros((scale, scale), dtype=bool)
    cell[(slice(start, stop), slice(None)) if axis == 0 else (slice(None), slice(start, stop))] = True
    return np.kron(studs, cell)


def render_tiles(colors: np.ndarray, regions: np.ndarray, scale: int | None = None) -> np.ndarray:
    """
    Draw a mosaic as smooth tiles.

    :param colors: (rows, cols, 3) uint8 RGB array, one pixel per stud.
    :param regions: (rows, cols) array numbering each stud by its tile
        (see ``plates.plate_regions``).
    :param scale: Pixels per stud. Defaults to ``tile_scale``.
    :return: (rows x scale, cols x scale, 3) uint8 RGB array.
    """
    rows, cols = regions.shape
    scale = scale if scale is not None else tile_scale(rows, cols)
    seam = max(1, scale // 16)
    bevel = max(2, scale // 8)

    # Which studs lie on each side of their tile.
    top, left = np.ones(regions.shape, dtype=bool), np.ones(regions.shape, dtype=bool)
    top[1:] = regions[1:] != regions[:-1]
    left[:, 1:] = regions[:, 1:] != regions[:, :-1]
    bottom, right = np.ones(regions.shape, dtype=bool), np.ones(regions.shape, dtype=bool)
    bottom[:-1] = top[1:]
    right[:, :-1] = left[:, 1:]

    shade = np.ones((rows * scale, cols * scale), dtype=np.float32)
    glow = np.zeros(shade.shape, dtype=np.float32)
    for studs, axis in ((top, 0), (left, 1)):
        lit = _band(studs, scale, seam, seam + bevel, axis)
        shade[lit] = LIT_EDGE_SHADE
        glow[lit] = LIT_EDGE_GLOW
    for studs, axis in ((bottom, 0), (right, 1)):
        shade[_band(studs, scale, scale - seam - bevel, scale - seam, axis)] = SHADOW_EDGE_SHADE
    # Last, so that seams stay unbroken where the bands above meet in a corner.
    for studs, axis in ((top, 0), (left, 1)):
        shade[_band(studs, scale, 0, seam, axis)] = SEAM_SHADE
        glow[_band(studs, scale, 0, seam, axis)] = 0
    for studs, axis in ((bottom, 0), (right, 1)):
        shade[_band(studs, scale, scale - seam, scale, axis)] = SEAM_SHADE
        glow[_band(studs, scale, scale - seam, scale, axis)] = 0

    pixels = np.repeat(np.repeat(colors, scale, axis=0), scale, axis=1).astype(np.float32)
    return np.clip(pixels * shade[..., None] + glow[..., None], 0, 255).astype(np.uint8)


def _round_stud(scale: int) -> tuple[np.ndarray, ...]:
    """Return one stud's (tile coverage, shade, glow, cast shadow), ``scale`` pixels wide."""
    size = scale * SUPERSAMPLING
    y, x = np.mgrid[0:size, 0:size].astype(np.float32) - (size - 1) / 2
    radius = size * ROUND_RADIUS
    distance = np.hypot(x, y)
    diagonal = x + y  # Negative towards the light.
    rim = distance > radius * ROUND_RIM
    offset = size * ROUND_CAST_OFFSET
    layers = (
        distance <= radius,
        (1 - ROUND_LIGHT_SLOPE * diagonal / (2 * radius)) * np.where(rim & (diagonal >= 0), ROUND_RIM_SHADE, 1),
        np.where(rim & (diagonal < 0), ROUND_RIM_GLOW, 0),
        np.hypot(x - offset, y - offset) <= radius,
    )
    return tuple(cv2.resize(np.float32(layer), (scale, scale), interpolation=cv2.INTER_AREA) for layer in layers)


def render_round(colors: np.ndarray, scale: int | None = None, regions: np.ndarray | None = None) -> np.ndarray:
    """
    Draw a mosaic as one round tile per stud, on a dark baseplate.

    :param colors: (rows, cols, 3) uint8 RGB array, one pixel per stud.
    :param scale: Pixels per stud. Defaults to ``tile_scale``.
    :param regions: (rows, cols) array numbering each stud by the plate covering it,
        drawn as in ``render_tiles``, or negative for a round tile (see
        ``plates.plate_regions``). Defaults to round tiles everywhere.
    :return: (rows x scale, cols x scale, 3) uint8 RGB array.
    """
    rows, cols = colors.shape[:2]
    scale = scale if scale is not None else tile_scale(rows, cols)
    coverage, shade, glow, shadow = (np.tile(layer, (rows, cols))[..., None] for layer in _round_stud(scale))
    pixels = np.repeat(np.repeat(colors, scale, axis=0), scale, axis=1).astype(np.float32)
    base = np.array(ROUND_BASE_COLOR, dtype=np.float32) * (1 - ROUND_CAST_SHADOW * shadow)
    drawn = (pixels * shade + glow) * coverage + base * (1 - coverage)
    round_tiles = np.clip(drawn, 0, 255).astype(np.uint8)
    if regions is None or not (regions >= 0).any():
        return round_tiles
    plates = np.kron(regions >= 0, np.ones((scale, scale), dtype=bool))
    return np.where(plates[..., None], render_tiles(colors, regions, scale), round_tiles)
