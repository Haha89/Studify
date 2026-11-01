from __future__ import annotations

import pytest

from studify.models.brick import Brick
from studify.models.brickstore import BrickStore
from studify.models.static import COLORS


def test_defaults():
    brick = Brick()
    assert brick.id == -1
    assert brick.color == (0, 0, 0)


def test_repr():
    assert repr(Brick(7, (1, 2, 3))) == "Brick: 7 (1, 2, 3)"


def test_equal_bricks_are_interchangeable_in_sets_and_dicts():
    a, b = Brick(3, (9, 9, 9)), Brick(3, (9, 9, 9))
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


def test_bricks_differing_in_colour_are_not_equal():
    assert Brick(3, (9, 9, 9)) != Brick(3, (8, 8, 8))


def test_bricks_are_immutable():
    with pytest.raises(AttributeError):
        Brick(1, (0, 0, 0)).id = 2


def test_all_bricks_cover_every_colour_with_matching_ids():
    palette = BrickStore.all_bricks
    assert len(palette) == len(COLORS)
    assert [brick.id for brick in palette] == list(range(len(COLORS)))
    assert [brick.color for brick in palette] == list(COLORS.values())


def test_available_bricks_are_a_subset_of_all_bricks_with_ids_preserved():
    available = BrickStore.available_bricks
    assert 0 < len(available) < len(BrickStore.all_bricks)
    for brick in available:
        assert BrickStore.all_bricks[brick.id] == brick


def test_available_bricks_exclude_placeholders_and_speciality_finishes():
    excluded_prefixes = (
        "Trans-",
        "Trans ",
        "Chrome",
        "Speckle",
        "Glitter",
        "Pearl",
        "Metallic",
        "Metal ",
        "Milky",
        "Glow",
    )
    names = list(COLORS)
    for brick in BrickStore.available_bricks:
        name = names[brick.id]
        assert name not in ("Unknown", "[No Color]")
        assert not name.startswith(excluded_prefixes)


def test_available_bricks_keep_plain_colours_like_black_and_blue():
    names_to_ids = {name: i for i, name in enumerate(COLORS)}
    available_ids = {brick.id for brick in BrickStore.available_bricks}
    assert names_to_ids["Black"] in available_ids
    assert names_to_ids["Blue"] in available_ids
