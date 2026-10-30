from legome.models.brick import Brick
from legome.models.static import COLORS

# Price of one 1x1 LEGO plate in USD, from the LEGO Pick-a-Brick store.
PRICE_TILE_USD = 0.03
# Price of one 32x32 LEGO baseplate in USD, as found on Amazon.
PRICE_BASE_USD = 2.0

# Name prefixes/entries that aren't ordinary, buildable LEGO colors: placeholders
# ("Unknown", "[No Color]") and finishes nearest-colour matching shouldn't reach for
# unless asked (transparent, chrome, speckled, glitter, pearl, metallic, glow-in-dark).
# Several of these sit exactly on or next to a plain colour (e.g. "Speckle
# Black-Copper" == (0, 0, 0), same as "Black"; "Unknown" == (0, 51, 178), closer to
# pure blue than "Blue" itself) and would otherwise win ties against the plain colour.
_NON_BUILDABLE_PREFIXES = (
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
_NON_BUILDABLE_NAMES = {"Unknown", "[No Color]"}


def _is_buildable(name: str) -> bool:
    return name not in _NON_BUILDABLE_NAMES and not name.startswith(_NON_BUILDABLE_PREFIXES)


class BrickStore:
    """The palette of bricks that can be ordered, and what they cost."""

    COLORS = COLORS

    # Every colour, indexed exactly as COLORS is: Brick.id is stable across both lists.
    all_bricks: list[Brick] = [Brick(i, c) for i, c in enumerate(COLORS.values())]
    # The subset actually meant for colour matching: no placeholders or speciality
    # finishes. This is what Canvas.fit_from_image uses by default.
    available_bricks: list[Brick] = [
        brick for brick, name in zip(all_bricks, COLORS, strict=True) if _is_buildable(name)
    ]

    @classmethod
    def bricks_named(cls, names: list[str]) -> list[Brick]:
        """Return the bricks for the given colour names (case-insensitive), in order.

        :raises ValueError: listing every name that isn't a palette colour.
        """
        by_name = {name.lower(): brick for name, brick in zip(COLORS, cls.all_bricks, strict=True)}
        unknown = [name for name in names if name.strip().lower() not in by_name]
        if unknown:
            raise ValueError(f"Unknown colour(s): {', '.join(unknown)}")
        return list(dict.fromkeys(by_name[name.strip().lower()] for name in names))

    @staticmethod
    def get_price_tile() -> float:
        """Return the price of one LEGO element in USD."""
        return PRICE_TILE_USD

    @staticmethod
    def get_price_base() -> float:
        """Return the price of one 32x32 LEGO baseplate in USD."""
        return PRICE_BASE_USD
