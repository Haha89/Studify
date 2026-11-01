"""A mosaic's bricks as a BrickLink wanted list, ready to upload.

Colour ids come from BrickLink's colour guide (checked October 2026,
v2.bricklink.com/catalog/color-guide). BrickLink has renamed several colours
since the LDraw-era names in ``static.py``, noted below. Only the plain colours
are listed: not every speciality finish exists as a 1x1 plate.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from math import ceil

from studify.models.assembly import ASSEMBLY_PART_NUMBERS
from studify.models.pricing import SPARE_RATE
from studify.models.static import COLORS

# BrickLink part number of each plate, by (short side, long side).
PLATE_PARTS = {
    (1, 1): "3024",
    (1, 2): "3023",
    (1, 3): "3623",
    (1, 4): "3710",
    (2, 2): "3022",
}
# Plate 1 x 1: what a mosaic is made of, unless it uses larger plates.
PLATE_1X1 = PLATE_PARTS[1, 1]
# Tile, Round 1 x 1: the other way to build a mosaic, one per stud.
ROUND_TILE = "98138"

BRICKLINK_COLOR_IDS = {
    "Aqua": 41,
    "Black": 11,
    "Blue": 7,
    "Blue-Violet": 97,  # BrickLink: Royal Blue. Same RGB here as "Royal Blue".
    "Bright Green": 36,
    "Bright Light Blue": 105,
    "Bright Light Orange": 110,
    "Bright Light Yellow": 103,
    "Bright Pink": 104,
    "Brown": 8,
    "Copper": 84,
    "Coral": 220,
    "Dark Azure": 153,
    "Dark Blue": 63,
    "Dark Blue-Violet": 109,  # BrickLink: Dark Royal Blue.
    "Dark Brown": 120,
    "Dark Flesh": 91,  # BrickLink: Light Brown.
    "Dark Gray": 10,
    "Dark Green": 80,
    "Dark Nougat": 225,
    "Dark Orange": 68,
    "Dark Pink": 47,
    "Dark Purple": 89,
    "Dark Red": 59,
    "Dark Tan": 69,
    "Dark Turquoise": 39,
    "Dark Bluish Gray": 85,
    "Earth Orange": 29,
    "Fabuland Brown": 106,
    "Flat Dark Gold": 81,
    "Flat Silver": 95,
    "Flesh": 28,  # BrickLink: Nougat.
    "Green": 6,
    "Lavender": 154,
    "Light Aqua": 152,
    "Light Blue": 62,
    "Light Bluish Gray": 86,
    "Light Flesh": 90,  # BrickLink: Light Nougat.
    "Light Gray": 9,
    "Light Green": 38,
    "Light Lime": 35,
    "Light Orange": 32,
    "Light Pink": 56,  # BrickLink: Rose Pink.
    "Light Purple": 93,
    "Light Salmon": 26,
    "Light Turquoise": 40,
    "Light Violet": 44,
    "Light Yellow": 33,
    "Lime": 34,
    "Maersk Blue": 72,
    "Magenta": 71,
    "Medium Azure": 156,
    "Medium Blue": 42,
    "Medium Brown": 240,
    "Medium Dark Flesh": 150,  # BrickLink: Medium Nougat.
    "Medium Dark Pink": 94,
    "Medium Green": 37,
    "Medium Lavender": 157,
    "Medium Lime": 76,
    "Medium Orange": 31,
    "Medium Tan": 241,
    "Medium Violet": 73,
    "Olive Green": 155,
    "Orange": 4,
    "Pink": 23,
    "Purple": 24,
    "Red": 5,
    "Reddish Brown": 88,
    "Reddish Lilac": 227,  # BrickLink: Clikits Lavender.
    "Royal Blue": 97,
    "Rust": 27,
    "Salmon": 25,
    "Sand Blue": 55,
    "Sand Green": 48,
    "Sand Purple": 54,
    "Sand Red": 58,
    "Sky Blue": 87,
    "Tan": 2,
    "Very Light Bluish Gray": 99,
    "Very Light Gray": 49,
    "Very Light Orange": 96,
    "Violet": 43,
    "White": 1,
    "Yellow": 3,
    "Yellowish Green": 158,
}

# The plain BrickLink colours each plate is known in, from the "Known Colors" of its
# catalog page (checked October 2026, e.g. bricklink.com/v2/catalog/catalogitem.page?P=3024).
PART_COLORS = {
    "3024": frozenset(
        (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 23, 28, 31, 34, 36, 39, 42, 47, 48, 49, 55, 56, 59, 63, 68, 69, 71, 72)
        + (80, 85, 86, 88, 89, 90, 103, 104, 105, 110, 120, 150, 152, 153, 154, 155, 156, 157, 158, 220, 241)
    ),
    "3023": frozenset(
        (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 23, 28, 31, 34, 36, 39, 41, 42, 47, 48, 55, 56, 58, 59, 63, 68, 69, 71)
        + (72, 80, 85, 86, 87, 88, 89, 90, 103, 104, 105, 110, 120, 150, 152, 153, 154, 155, 156, 157, 158, 220, 241)
    ),
    "3623": frozenset(
        (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 23, 34, 36, 39, 42, 47, 48, 55, 56, 59, 63, 68, 69, 71, 72, 80, 85)
        + (86, 88, 89, 90, 103, 104, 105, 110, 120, 150, 152, 153, 154, 155, 156, 157, 158)
    ),
    "3710": frozenset(
        (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 24, 28, 31, 33, 34, 36, 39, 42, 47, 48, 55, 59, 63, 68, 69, 71, 72, 80)
        + (85, 86, 87, 88, 89, 90, 103, 104, 105, 109, 110, 120, 150, 152, 153, 155, 156, 157, 158, 220, 241)
    ),
    "3022": frozenset(
        (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 24, 29, 33, 34, 36, 39, 42, 47, 48, 55, 59, 63, 68, 69, 71, 72, 85, 86)
        + (88, 89, 93, 103, 104, 105, 110, 120, 150, 152, 153, 154, 155, 156, 157, 241)
    ),
    # Checked November 2026.
    ROUND_TILE: frozenset(
        (1, 2, 3, 4, 5, 6, 7, 11, 28, 34, 36, 39, 42, 47, 55, 59, 63, 68, 69, 71, 80, 85, 86, 88, 89, 90, 95)
        + (103, 104, 105, 110, 120, 150, 152, 153, 154, 155, 156, 157, 158, 220)
    ),
}

# Black, the only colour the structure is listed in, is in all of these (checked
# November 2026 for the parts specific to it), and 3022 is already above.
PART_COLORS |= {
    part: frozenset({BRICKLINK_COLOR_IDS["Black"]}) for part in ASSEMBLY_PART_NUMBERS if part not in PART_COLORS
}

_NAMES = list(COLORS)


def comes_in(brick_id: int, part: str) -> bool:
    """Whether BrickLink knows this plate in this palette colour."""
    return BRICKLINK_COLOR_IDS.get(_NAMES[brick_id]) in PART_COLORS[part]


def round_tile_parts(distribution: Mapping[int, int]) -> dict[tuple[int, str], int]:
    """Return the parts of a build made of round tiles, as {(brick id, part): count}."""
    return {(brick_id, ROUND_TILE): count for brick_id, count in distribution.items()}


def unavailable_parts(parts: Mapping[tuple[int, str], int]) -> list[str]:
    """Return the colours of parts BrickLink doesn't know in that colour. Only a colour
    chosen by hand can have any, as the palettes leave out what can't be built."""
    return sorted({_NAMES[brick_id] for brick_id, part in parts if not comes_in(brick_id, part)})


def unlisted_colors(distribution: Mapping[int, int]) -> list[str]:
    """Return the names of a build's colours that a wanted list can't include."""
    return [_NAMES[brick_id] for brick_id in distribution if _NAMES[brick_id] not in BRICKLINK_COLOR_IDS]


def wanted_list_xml(distribution: Mapping[int, int], parts: Mapping[tuple[int, str], int] | None = None) -> str:
    """
    Write a build's bricks as BrickLink wanted-list XML, spares included. Baseplates
    are left out, and so are colours in ``unlisted_colors``.

    :param distribution: Bricks needed, as {brick id: count}.
    :param parts: Larger plates to list instead of 1x1s, as {(brick id, BrickLink
        part number): count}; see ``Canvas.get_part_counts``.
    """
    if parts is None:
        parts = {(brick_id, PLATE_1X1): count for brick_id, count in distribution.items()}
    # Two names can share a BrickLink colour.
    counts = Counter()
    for (brick_id, part), count in parts.items():
        color = BRICKLINK_COLOR_IDS.get(_NAMES[brick_id])
        if color is not None:
            counts[part, color] += count
    items = "".join(
        "<ITEM><ITEMTYPE>P</ITEMTYPE>"
        f"<ITEMID>{part}</ITEMID>"
        f"<COLOR>{color}</COLOR>"
        f"<MINQTY>{count + ceil(round(count * SPARE_RATE, 6))}</MINQTY>"
        "</ITEM>\n"
        for (part, color), count in sorted(counts.items())
    )
    return f"<INVENTORY>\n{items}</INVENTORY>\n"
