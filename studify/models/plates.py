"""Cover a mosaic with larger plates: fewer parts to place than one 1x1 per stud.

Which sizes to use is a choice, though 1x1 is always among them: it is the only
plate that can cover a stud with no same-coloured neighbour.

Plates stay within their baseplate, so each page of the guide can be built on its
own. Whether a colour actually comes in a given size varies: BrickLink reports any
that don't when the wanted list is uploaded.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from studify.models.static import DIM_BASE

# BrickLink part number of each plate, by (short side, long side).
PLATE_PARTS = {
    (1, 1): "3024",
    (1, 2): "3023",
    (1, 3): "3623",
    (1, 4): "3710",
    (2, 2): "3022",
}
# Every size, as written in the guide and on the command line. All are used by default.
PLATE_SIZES = tuple(f"{short}x{long}" for short, long in PLATE_PARTS)
ONLY_1X1 = ("1x1",)


def plate_sizes(names: Iterable[str]) -> tuple[str, ...]:
    """Return the chosen sizes in a standard order, with 1x1 always among them.

    :raises ValueError: naming any size that isn't in ``PLATE_SIZES``.
    """
    chosen = {name.strip() for name in names}
    if unknown := sorted(chosen - set(PLATE_SIZES)):
        raise ValueError(f"Unknown plate size(s): {', '.join(unknown)}; expected {', '.join(PLATE_SIZES)}")
    return tuple(size for size in PLATE_SIZES if size in chosen or size in ONLY_1X1)


def _shapes(sizes: Iterable[str]) -> tuple[tuple[int, int], ...]:
    """Each (height, width) to try at a position, largest first, in both orientations."""
    pairs = [tuple(int(side) for side in size.split("x")) for size in plate_sizes(sizes)]
    largest_first = sorted(pairs, key=lambda pair: pair[0] * pair[1], reverse=True)
    return tuple(dict.fromkeys(shape for short, long in largest_first for shape in ((short, long), (long, short))))


@dataclass(frozen=True, slots=True)
class Plate:
    """One plate on a baseplate, by the row and column of its top-left stud."""

    row: int
    col: int
    height: int
    width: int
    brick_id: int

    @property
    def size(self) -> str:
        """The plate's size as usually written, short side first, e.g. "1x4"."""
        return f"{min(self.height, self.width)}x{max(self.height, self.width)}"

    @property
    def part(self) -> str:
        return PLATE_PARTS[min(self.height, self.width), max(self.height, self.width)]


def pack_plates(grid: np.ndarray, sizes: Iterable[str] = PLATE_SIZES) -> list[Plate]:
    """
    Cover a grid of brick ids with plates, greedily: scanning from the top left,
    each uncovered stud starts the largest plate of one colour that fits there.

    :param grid: (rows, cols) array of brick ids, usually one baseplate.
    :param sizes: Which plates to use, from ``PLATE_SIZES``.
    :return: Plates covering every stud exactly once.
    """
    rows, cols = grid.shape
    shapes = _shapes(sizes)
    covered = np.zeros(grid.shape, dtype=bool)
    plates = []
    for row in range(rows):
        for col in range(cols):
            if covered[row, col]:
                continue
            brick_id = int(grid[row, col])
            for height, width in shapes:
                area = (row, row + height), (col, col + width)
                if area[0][1] > rows or area[1][1] > cols:
                    continue
                cells = slice(*area[0]), slice(*area[1])
                if (grid[cells] == brick_id).all() and not covered[cells].any():
                    covered[cells] = True
                    plates.append(Plate(row, col, height, width, brick_id))
                    break
    return plates


def pack_baseplates(grid: np.ndarray, sizes: Iterable[str] = PLATE_SIZES) -> list[Plate]:
    """Pack a whole mosaic baseplate by baseplate, with rows and columns given
    relative to the whole mosaic."""
    rows, cols = grid.shape
    return [
        Plate(row + plate.row, col + plate.col, plate.height, plate.width, plate.brick_id)
        for row in range(0, rows, DIM_BASE)
        for col in range(0, cols, DIM_BASE)
        for plate in pack_plates(grid[row : row + DIM_BASE, col : col + DIM_BASE], sizes)
    ]


def plate_regions(shape: tuple[int, int], plates: list[Plate]) -> np.ndarray:
    """Return a grid numbering each stud by its plate, for outlining the plates."""
    regions = np.zeros(shape, dtype=int)
    for number, plate in enumerate(plates):
        regions[plate.row : plate.row + plate.height, plate.col : plate.col + plate.width] = number
    return regions


def count_parts(plates: list[Plate]) -> dict[tuple[int, str], int]:
    """Count plates as {(brick id, BrickLink part number): how many}."""
    return dict(Counter((plate.brick_id, plate.part) for plate in plates))
