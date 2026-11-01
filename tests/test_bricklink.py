from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter

from studify.models.bricklink import BRICKLINK_COLOR_IDS, PLATE_1X1, unlisted_colors, wanted_list_xml
from studify.models.brickstore import BrickStore
from studify.models.static import COLORS

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


def test_an_empty_build_is_still_valid_xml():
    assert items(wanted_list_xml({})) == {}
