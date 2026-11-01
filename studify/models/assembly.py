"""What holds a mosaic together: baseplates, or the panels of the LEGO Art sets.

Baseplates don't connect to each other. The LEGO Art sets use 16x16 Technic panels
instead, pinned together along their sides, then held in a brick-built frame and hung
from two hangers. ``assembly_parts`` scales that design to any number of panels. It
reproduces the parts of the 3 x 3 panel sets (checked November 2026 against the
instructions of 31197 and the inventory of 31200); other sizes are worked out from
them, and nothing larger than a 8 x 5 panel set (31203) is known to hold.
"""

from __future__ import annotations

from studify.models.static import COLORS

ASSEMBLIES = ("baseplates", "panels")
DEFAULT_ASSEMBLY = "baseplates"
PANEL_STUDS = 16
# The longest side of an official set built this way (31203), in studs.
LARGEST_OFFICIAL_STUDS = 128
# The structure is hidden or frames the picture: all of it in one colour.
STRUCTURE_BRICK = list(COLORS).index("Black")

PANEL = "65803"  # Technic, Brick 16 x 16 x 1 1/3 with Holes
PIN = "2780"  # Technic, Pin with Short Friction Ridges
PIN_BRICK = "2458"  # Brick, Modified 1 x 2 with Pin
HANGER = "67139"  # Technic, Pin Connector Block, Liftarm 1 x 3 x 5 with Cutout
ASSEMBLY_PART_NUMBERS = (
    PANEL,
    PIN,
    PIN_BRICK,
    HANGER,
    "2445",  # Plate 2 x 12
    "4282",  # Plate 2 x 16
    "2465",  # Brick 1 x 16
    "3008",  # Brick 1 x 8
    "3010",  # Brick 1 x 4
    "3005",  # Brick 1 x 1
    "2357",  # Brick 2 x 2 Corner
    "2420",  # Plate 2 x 2 Corner
    "11212",  # Plate 3 x 3
    "3022",  # Plate 2 x 2
    "4162",  # Tile 1 x 8
    "63864",  # Tile 1 x 3
)


def panel_grid(rows: int, cols: int) -> tuple[int, int]:
    """Return how many panels a mosaic takes, down and across."""
    return rows // PANEL_STUDS, cols // PANEL_STUDS


def pin_joins(rows: int, cols: int) -> int:
    """Return how many panel sides are pinned to another panel."""
    down, across = panel_grid(rows, cols)
    return (down - 1) * across + down * (across - 1)


def assembly_parts(rows: int, cols: int) -> dict[tuple[int, str], int]:
    """Return the parts of panels, pins, frame and hangers for a mosaic, as
    {(brick id, BrickLink part number): how many}.

    :param rows: The mosaic's height in studs, a multiple of ``PANEL_STUDS``.
    :param cols: Its width in studs, likewise.
    """
    down, across = panel_grid(rows, cols)
    # Along each side of n panels, once on each of its two opposite sides.
    sides = 2 * (down + across)
    side_count = 4
    counts = {
        PANEL: down * across,
        # 3 pins a join, and 2 for each hanger.
        PIN: 3 * pin_joins(rows, cols) + 2 * 2,
        HANGER: 2,
        # The frame, side by side.
        PIN_BRICK: 2 * sides,
        "2465": sides,
        "3008": sides,
        "3010": sides - side_count,
        "4282": sides - side_count,
        "2445": side_count,
        "4162": 2 * sides - side_count,
        "63864": 2 * side_count,
        # Its corners, then the back: a corner plate where four panels meet, and a
        # plate on each panel to hold the mosaic off the wall.
        "2357": side_count,
        "11212": side_count,
        "3005": 3 * side_count,
        "2420": side_count + (down - 1) * (across - 1),
        "3022": down * across,
    }
    return {(STRUCTURE_BRICK, part): count for part, count in counts.items() if count}
