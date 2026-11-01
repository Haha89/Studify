from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter

from studify.models.bricklink import (
    BRICKLINK_COLOR_IDS,
    PART_COLORS,
    PLATE_1X1,
    PLATE_PARTS,
    comes_in,
    unavailable_parts,
    unlisted_colors,
    wanted_list_xml,
)
from studify.models.brickstore import BrickStore
from studify.models.static import COLORS
from studify.models.style import STYLES

NAMES = list(COLORS)


def items(xml: str) -> dict[int, int]:
    root = ET.fromstring(xml)
    assert root.tag == "INVENTORY"
    return {int(item.findtext("COLOR")): int(item.findtext("MINQTY")) for item in root}


def test_every_plain_colour_has_a_bricklink_id():
    assert unlisted_colors({brick.id: 1 for brick in BrickStore.available_bricks}) == []


def test_only_the_two_names_for_royal_blue_share_an_id():
    shared = [color for color, count in Counter(BRICKLINK_COLOR_IDS.values()).items() if count > 1]
    assert shared == [BRICKLINK_COLOR_IDS["Royal Blue"]]
    assert BRICKLINK_COLOR_IDS["Blue-Violet"] == BRICKLINK_COLOR_IDS["Royal Blue"]
    assert COLORS["Blue-Violet"] == COLORS["Royal Blue"]


def test_lists_1x1_plates_as_parts():
    item = ET.fromstring(wanted_list_xml({NAMES.index("Black"): 10}))[0]
    assert item.findtext("ITEMTYPE") == "P"
    assert item.findtext("ITEMID") == PLATE_1X1 == "3024"
    assert item.findtext("COLOR") == "11"


def test_quantities_include_spares_rounded_up():
    black, white, red = NAMES.index("Black"), NAMES.index("White"), NAMES.index("Red")
    assert items(wanted_list_xml({black: 100, white: 1, red: 60})) == {11: 105, 1: 2, 5: 63}


def test_names_sharing_a_colour_are_merged():
    distribution = {NAMES.index("Royal Blue"): 60, NAMES.index("Blue-Violet"): 40}
    assert items(wanted_list_xml(distribution)) == {97: 105}


def test_a_finish_without_an_id_is_left_out_and_reported():
    chrome, black = NAMES.index("Chrome Gold"), NAMES.index("Black")
    distribution = {chrome: 10, black: 20}
    assert unlisted_colors(distribution) == ["Chrome Gold"]
    assert items(wanted_list_xml(distribution)) == {11: 21}


def test_availability_comes_from_the_catalog():
    assert comes_in(NAMES.index("Black"), "3024")
    assert not comes_in(NAMES.index("Purple"), "3024")  # Only 1x4 and 2x2 of our five.
    assert comes_in(NAMES.index("Purple"), "3022")
    assert not comes_in(NAMES.index("Chrome Gold"), "3024")  # No BrickLink id here.


def test_unavailable_parts_names_the_colour():
    purple, black = NAMES.index("Purple"), NAMES.index("Black")
    assert unavailable_parts({(purple, "3024"): 1, (purple, "3022"): 1, (black, "3024"): 1}) == ["Purple"]


def test_every_palette_colour_has_a_1x1():
    for name in ("buyable", "all", "common"):
        assert all(comes_in(brick.id, PLATE_1X1) for brick in BrickStore.palette(name))


def test_every_style_palette_colour_has_a_1x1():
    for style in STYLES.values():
        assert all(comes_in(NAMES.index(name), PLATE_1X1) for name in style.colors or ())


def test_every_catalog_colour_is_a_real_bricklink_id():
    assert set(PART_COLORS) == set(PLATE_PARTS.values())


def test_an_empty_build_is_still_valid_xml():
    assert items(wanted_list_xml({})) == {}
