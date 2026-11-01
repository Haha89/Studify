"""What a mosaic costs to build: bricks by colour rarity, baseplates, spares.

Every figure here is an estimate, kept in plain tables so it can be tuned
without touching the logic. Prices are in euros; dollars are derived with a
fixed rate. Shipping is never included: on BrickLink it often outweighs the
parts themselves on small orders.

Sources (checked October 2026):
- 1x1 plates: BrickLink price guides put common colours around EUR 0.02-0.04
  a piece, rare ones at EUR 0.50 and more (stonewars.com, "Is it really
  possible to rebrick LEGO Art mosaics at a reasonable price?").
- Larger plates, relative to a 1x1 (November 2026, new black parts only, so a rough
  guide): Brick Owl listings around USD 0.02-0.036 for a 1x1, 0.016-0.022 for a 1x2,
  0.027-0.034 for a 1x4 and 0.016-0.026 for a 2x2; LEGO Pick a Brick at EUR 0.06 for a
  1x1, 0.08 for a 1x2, 0.07 for a 1x3 and 0.12 for a 1x4 (via brickmissing.com).
  BrickLink's price guide wasn't readable, and neither source covers everything.
- Round 1x1 tiles: Brick Owl listings start around USD 0.003-0.04, which doesn't
  show them being dearer or cheaper than 1x1 plates.
- Official 32x32 baseplate (11023): USD 7.99 on LEGO.com, EUR 8.99 at EU retailers.
- Compatible (non-LEGO) 32x32 baseplates: about EUR 2 each on Amazon.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from studify.models.static import COLORS

# Dollars per euro, fixed. Last updated 2026-10 (EUR/USD traded ~1.12-1.15).
USD_PER_EUR = 1.13
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$"}

# Price of one 32x32 baseplate, in euros.
BASEPLATE_PRICES_EUR = {"official": 8.99, "compatible": 2.00}
BASEPLATE_LABELS = {"official": "official LEGO", "compatible": "compatible"}

# Price range of one 1x1 plate, in euros, by how easy its colour is to find.
TIER_PRICES_EUR = {
    "common": (0.02, 0.04),
    "uncommon": (0.05, 0.12),
    "rare": (0.15, 0.50),
}

# Price of each part relative to a 1x1 plate of the same colour, by BrickLink part
# number. Where the two sources differ, about halfway between them; a 2x2, with only
# listings to go on and those below a 1x1, is kept level with one to stay cautious.
PLATE_1X1_PART = "3024"
PART_PRICE_FACTORS = {
    PLATE_1X1_PART: 1.0,
    "3023": 1.0,  # 1x2
    "3623": 1.2,  # 1x3
    "3710": 1.5,  # 1x4
    "3022": 1.0,  # 2x2
    "98138": 1.0,  # Round tile 1x1
}

# Share of extra bricks to buy: some always go missing, and sellers miscount.
SPARE_RATE = 0.05

# Colours in current, high-volume production: cheap and stocked by most sellers.
COMMON_COLORS = frozenset(
    {
        "Black",
        "White",
        "Red",
        "Blue",
        "Yellow",
        "Tan",
        "Dark Tan",
        "Reddish Brown",
        "Light Bluish Gray",
        "Dark Bluish Gray",
        "Orange",
        "Lime",
        "Green",
        "Bright Green",
        "Medium Azure",
        "Medium Blue",
        "Bright Light Orange",
        "Bright Light Yellow",
    }
)
# Retired or short-lived colours (old greys and browns, pastels, flat metallics):
# few sellers, high prices, sometimes not obtainable in quantity at all.
RARE_COLORS = frozenset(
    {
        "Light Gray",
        "Dark Gray",
        "Brown",
        "Fabuland Brown",
        "Very Light Gray",
        "Light Violet",
        "Light Yellow",
        "Light Green",
        "Light Pink",
        "Light Salmon",
        "Salmon",
        "Light Purple",
        "Sand Purple",
        "Reddish Lilac",
        "Medium Violet",
        "Earth Orange",
        "Rust",
        "Copper",
        "Flat Dark Gold",
        "Flat Silver",
    }
)
# Every other colour is "uncommon".

# Which colour tiers each palette choice draws from. "buyable" is the default:
# on sample pictures, dropping rare colours left the mosaic almost unchanged
# but cut its estimated price by about 5-30 %; "common" alone visibly degrades skin tones.
PALETTE_TIERS = {
    "buyable": ("common", "uncommon"),
    "all": ("common", "uncommon", "rare"),
    "common": ("common",),
}
DEFAULT_PALETTE = "buyable"

_NAMES = list(COLORS)


def color_tier(brick_id: int) -> str:
    """Return "common", "uncommon" or "rare" for a palette colour."""
    name = _NAMES[brick_id]
    if name in COMMON_COLORS:
        return "common"
    if name in RARE_COLORS:
        return "rare"
    return "uncommon"


@dataclass(frozen=True)
class PriceEstimate:
    """A price range for one build, in ``currency``."""

    currency: str
    low: float
    high: float
    bricks_low: float
    bricks_high: float
    baseplates: float
    baseplate_count: int
    baseplate_kind: str
    # Rare colours the build uses, as {colour name: bricks needed}.
    rare_colors: dict[str, int] = field(default_factory=dict)

    @property
    def symbol(self) -> str:
        return CURRENCY_SYMBOLS[self.currency]

    def format(self, value: float | None = None) -> str:
        """Format an amount (or, by default, the whole range) for display."""
        if value is not None:
            return f"{value:,.0f} {self.symbol}".replace(",", " ")
        return f"{self.low:,.0f}–{self.high:,.0f} {self.symbol}".replace(",", " ")


def estimate_price(
    distribution: Mapping[int, int],
    baseplate_count: int,
    baseplate_kind: str = "official",
    currency: str = "EUR",
    parts: Mapping[tuple[int, str], int] | None = None,
) -> PriceEstimate:
    """
    Estimate what a build costs: every part priced by its colour's tier and its
    ``PART_PRICE_FACTORS``, plus ``SPARE_RATE`` extra parts, plus the baseplates.
    Shipping is not included.

    :param distribution: Bricks needed, as {brick id: count}.
    :param baseplate_count: Number of 32x32 baseplates.
    :param baseplate_kind: A ``BASEPLATE_PRICES_EUR`` key.
    :param currency: "EUR" or "USD".
    :param parts: The parts bought, as {(brick id, BrickLink part number): count}.
        Defaults to one 1x1 plate per brick.
    """
    if baseplate_kind not in BASEPLATE_PRICES_EUR:
        raise ValueError(f"Unknown baseplate kind {baseplate_kind!r}; expected one of {list(BASEPLATE_PRICES_EUR)}")
    if currency not in CURRENCY_SYMBOLS:
        raise ValueError(f"Unknown currency {currency!r}; expected one of {list(CURRENCY_SYMBOLS)}")
    rate = USD_PER_EUR if currency == "USD" else 1.0

    if parts is None:
        parts = {(brick_id, PLATE_1X1_PART): count for brick_id, count in distribution.items()}
    bricks_low = bricks_high = 0.0
    for (brick_id, part), count in parts.items():
        low, high = TIER_PRICES_EUR[color_tier(brick_id)]
        bricks_low += count * low * PART_PRICE_FACTORS[part]
        bricks_high += count * high * PART_PRICE_FACTORS[part]
    rare = {_NAMES[brick_id]: count for brick_id, count in distribution.items() if color_tier(brick_id) == "rare"}
    spares = 1 + SPARE_RATE
    bricks_low, bricks_high = bricks_low * spares * rate, bricks_high * spares * rate
    baseplates = baseplate_count * BASEPLATE_PRICES_EUR[baseplate_kind] * rate

    return PriceEstimate(
        currency=currency,
        low=bricks_low + baseplates,
        high=bricks_high + baseplates,
        bricks_low=bricks_low,
        bricks_high=bricks_high,
        baseplates=baseplates,
        baseplate_count=baseplate_count,
        baseplate_kind=baseplate_kind,
        rare_colors=dict(sorted(rare.items(), key=lambda item: -item[1])),
    )
