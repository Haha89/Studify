"""Give a mosaic depth: find the picture's subject and raise it above the background.

Two choices, in ``RELIEFS``:

- "flat": every piece at the same height.
- "stepped": the subject stands on stacks of hidden plates, its core higher still.

The subject comes from GrabCut, part of OpenCV, so nothing has to be downloaded. It can
be wrong: an implausible subject falls back to an ellipse in the middle of the picture.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import cv2
import numpy as np

from studify.models.bricklink import ROUND_TILE
from studify.models.plates import PLATE_SIZES, Plate, count_parts, pack_baseplates, plate_regions
from studify.models.static import COLORS, DIM_BASE

RELIEFS = ("flat", "stepped")
DEFAULT_RELIEF = "flat"

BACKGROUND, SUBJECT, CORE = 0, 1, 2
# Plates stacked under the visible piece, by level ("stepped" only).
STEPPED_PLATES = np.array([0, 2, 3])
# Hidden stacks can be any colour; black is cheap, and reads as shadow where a side shows.
SUPPORT_BRICK = list(COLORS).index("Black")

_RECT_INSET = 0.08  # GrabCut starts from a box this far inside the picture.
_MIN_SUBJECT, _MAX_SUBJECT = 0.05, 0.9  # Share of the picture a plausible subject covers.
_GRABCUT_ITERATIONS = 5
_CORE_SHARE = 0.5  # Of the way from the subject's outline to its deepest point.
_MIN_CORE_DEPTH = 3  # In studs: anything closer to the outline makes too thin a stack.


def _ellipse(shape: tuple[int, int], center: tuple[int, int], axes: tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    cv2.ellipse(mask, center, axes, 0, 0, 360, 1, -1)
    return mask.astype(bool)


def _largest_component(mask: np.ndarray) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8))
    if count < 2:
        return np.zeros(mask.shape, dtype=bool)
    return labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))


def subject_mask(rgb: np.ndarray) -> np.ndarray:
    """Return a boolean grid of the picture's subject, free of thin slivers."""
    height, width = rgb.shape[:2]
    fallback = _ellipse((height, width), (width // 2, height // 2), (width * 3 // 8, height * 3 // 8))
    inset_x, inset_y = max(1, round(width * _RECT_INSET)), max(1, round(height * _RECT_INSET))
    rect = (inset_x, inset_y, width - 2 * inset_x, height - 2 * inset_y)
    if rect[2] < 1 or rect[3] < 1:
        return fallback
    # A fixed seed: the guide must get the same subject as the preview it follows.
    cv2.setRNGSeed(0)
    labels = np.zeros((height, width), dtype=np.uint8)
    models = np.zeros((1, 65)), np.zeros((1, 65))
    try:
        cv2.grabCut(rgb[..., ::-1].copy(), labels, rect, *models, _GRABCUT_ITERATIONS, cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return fallback
    mask = np.isin(labels, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8)
    kernel = np.ones((3, 3), dtype=np.uint8)
    # Opening removes what a stack couldn't stand up as: anything under 3 studs wide.
    mask = cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel), cv2.MORPH_CLOSE, kernel)
    subject = _largest_component(mask)
    return subject if _MIN_SUBJECT <= subject.mean() <= _MAX_SUBJECT else fallback


def core_mask(subject: np.ndarray) -> np.ndarray:
    """Return the part of a subject furthest from its outline: what stands highest."""
    if not subject.any():
        return subject
    distance = cv2.distanceTransform(subject.astype(np.uint8), cv2.DIST_L2, 3)
    return distance >= max(_MIN_CORE_DEPTH, distance.max() * _CORE_SHARE)


def subject_levels(rgb: np.ndarray) -> np.ndarray:
    """Return a grid of ``BACKGROUND``, ``SUBJECT`` or ``CORE`` for a picture."""
    levels = np.full(rgb.shape[:2], BACKGROUND, dtype=int)
    subject = subject_mask(rgb)
    levels[subject] = SUBJECT
    levels[core_mask(subject)] = CORE
    return levels


@dataclass(frozen=True)
class Layout:
    """Which piece goes where in a mosaic."""

    heights: np.ndarray  # Plates stacked under each stud's visible piece.
    tiles: np.ndarray  # Studs topped by a round tile.
    plates: list[Plate]  # Visible plates, covering every other stud.
    supports: list[list[Plate]]  # Hidden plates by layer, the lowest first.
    # What holds it together, if not just baseplates (see ``assembly.assembly_parts``).
    assembly: dict[tuple[int, str], int] = field(default_factory=dict)

    @property
    def regions(self) -> np.ndarray:
        """A grid numbering each stud by its visible plate, negative for a round tile."""
        return plate_regions(self.tiles.shape, self.plates)

    @property
    def support_count(self) -> int:
        return sum(len(layer) for layer in self.supports)

    @property
    def assembly_count(self) -> int:
        return sum(self.assembly.values())

    def parts(self, brick_ids: np.ndarray) -> dict[tuple[int, str], int]:
        """Return every part it takes, as {(brick id, BrickLink part number): how many}."""
        counts = Counter(count_parts(self.plates))
        for layer in self.supports:
            counts.update(count_parts(layer))
        for brick_id, count in zip(*np.unique(brick_ids[self.tiles], return_counts=True), strict=True):
            counts[int(brick_id), ROUND_TILE] += int(count)
        counts.update(self.assembly)
        return dict(counts)


def _support_layer(area: np.ndarray, crosswise: bool, cell: int) -> list[Plate]:
    """Cover an area with hidden plates. Layers alternate direction, so that each one's
    plates bridge the joints of the one below instead of lining up with them."""
    grid = np.full(area.shape, SUPPORT_BRICK)
    if not crosswise:
        return pack_baseplates(grid, PLATE_SIZES, area, cell)
    return [
        Plate(plate.col, plate.row, plate.width, plate.height, plate.brick_id)
        for plate in pack_baseplates(grid.T, PLATE_SIZES, area.T, cell)
    ]


def lay_out(
    brick_ids: np.ndarray,
    sizes,
    heights: np.ndarray,
    tiles: np.ndarray,
    cell: int = DIM_BASE,
    assembly: dict[tuple[int, str], int] | None = None,
) -> Layout:
    """
    Work out a mosaic's pieces. A plate can't span two heights, so each is packed alone.

    :param brick_ids: Grid of brick ids.
    :param sizes: Visible plate sizes to use, from ``PLATE_SIZES``.
    :param heights: Plates stacked under each stud's visible piece.
    :param tiles: Studs to top with a round tile instead of a plate.
    :param cell: Width in studs of what it is built on: no plate crosses from one to the next.
    :param assembly: What holds it together, if not just baseplates.
    """
    plates = [
        plate
        for height in np.unique(heights).tolist()
        for plate in pack_baseplates(brick_ids, sizes, (heights == height) & ~tiles, cell)
    ]
    supports = [_support_layer(heights >= layer, layer % 2 == 0, cell) for layer in range(1, int(heights.max()) + 1)]
    return Layout(heights, tiles, plates, supports, assembly or {})
