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

from legome.models.pricing import SPARE_RATE
from legome.models.static import COLORS

# Plate 1 x 1, the only part a mosaic uses.
PLATE_1X1 = "3024"

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
    "Dark Azure": 153,
    "Dark Blue": 63,
    "Dark Blue-Violet": 109,  # BrickLink: Dark Royal Blue.
    "Dark Brown": 120,
    "Dark Flesh": 91,  # BrickLink: Light Brown.
    "Dark Gray": 10,
    "Dark Green": 80,
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
    "Medium Dark Flesh": 150,  # BrickLink: Medium Nougat.
    "Medium Dark Pink": 94,
    "Medium Green": 37,
    "Medium Lavender": 157,
    "Medium Lime": 76,
    "Medium Orange": 31,
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

_NAMES = list(COLORS)


def unlisted_colors(distribution: Mapping[int, int]) -> list[str]:
    """Return the names of a build's colours that a wanted list can't include."""
    return [_NAMES[brick_id] for brick_id in distribution if _NAMES[brick_id] not in BRICKLINK_COLOR_IDS]


def wanted_list_xml(distribution: Mapping[int, int]) -> str:
    """
    Write a build's bricks as BrickLink wanted-list XML, spares included. Baseplates
    are left out, and so are colours in ``unlisted_colors``.

    :param distribution: Bricks needed, as {brick id: count}.
    """
    # Two names can share a BrickLink colour.
    counts = Counter()
    for brick_id, count in distribution.items():
        color = BRICKLINK_COLOR_IDS.get(_NAMES[brick_id])
        if color is not None:
            counts[color] += count
    items = "".join(
        "<ITEM><ITEMTYPE>P</ITEMTYPE>"
        f"<ITEMID>{PLATE_1X1}</ITEMID>"
        f"<COLOR>{color}</COLOR>"
        f"<MINQTY>{count + ceil(round(count * SPARE_RATE, 6))}</MINQTY>"
        "</ITEM>\n"
        for color, count in sorted(counts.items())
    )
    return f"<INVENTORY>\n{items}</INVENTORY>\n"
