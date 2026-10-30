from __future__ import annotations

import pytest

from legome.models import pricing
from legome.models.static import COLORS

NAMES = list(COLORS)


def brick(name: str) -> int:
    return NAMES.index(name)


def test_every_listed_colour_exists_in_the_palette():
    # A typo here would silently demote a colour to "uncommon".
    assert pricing.COMMON_COLORS.issubset(COLORS)
    assert pricing.RARE_COLORS.issubset(COLORS)
    assert not pricing.COMMON_COLORS & pricing.RARE_COLORS


@pytest.mark.parametrize(
    ("name", "tier"), [("Black", "common"), ("Dark Blue", "uncommon"), ("Light Gray", "rare"), ("Flat Silver", "rare")]
)
def test_color_tier(name, tier):
    assert pricing.color_tier(brick(name)) == tier


def test_tiers_get_pricier_with_rarity():
    common, uncommon, rare = (pricing.TIER_PRICES_EUR[t] for t in ("common", "uncommon", "rare"))
    assert common[0] < common[1] <= uncommon[1] <= rare[1]
    assert common[0] < uncommon[0] < rare[0]


def test_bricks_are_priced_by_tier_with_spares():
    estimate = pricing.estimate_price({brick("Black"): 1000}, baseplate_count=0)
    low, high = pricing.TIER_PRICES_EUR["common"]
    spares = 1 + pricing.SPARE_RATE
    assert estimate.bricks_low == pytest.approx(1000 * low * spares)
    assert estimate.bricks_high == pytest.approx(1000 * high * spares)
    assert estimate.rare_colors == {}


def test_rare_colours_raise_the_price_and_are_reported_biggest_first():
    common = pricing.estimate_price({brick("Black"): 1000}, baseplate_count=0)
    with_rare = pricing.estimate_price({brick("Black"): 900, brick("Light Gray"): 30, brick("Rust"): 70}, 0)
    assert with_rare.high > common.high
    assert list(with_rare.rare_colors.items()) == [("Rust", 70), ("Light Gray", 30)]


@pytest.mark.parametrize("kind", ["official", "compatible"])
def test_baseplates(kind):
    estimate = pricing.estimate_price({}, baseplate_count=9, baseplate_kind=kind)
    assert estimate.baseplates == pytest.approx(9 * pricing.BASEPLATE_PRICES_EUR[kind])
    assert estimate.low == estimate.high == estimate.baseplates


def test_official_baseplates_cost_more_than_compatible_ones():
    official = pricing.estimate_price({brick("Black"): 100}, 4, "official")
    compatible = pricing.estimate_price({brick("Black"): 100}, 4, "compatible")
    assert official.low > compatible.low


def test_dollars_are_euros_at_the_fixed_rate():
    distribution = {brick("Black"): 500, brick("Light Gray"): 50}
    eur = pricing.estimate_price(distribution, 2, currency="EUR")
    usd = pricing.estimate_price(distribution, 2, currency="USD")
    assert usd.low == pytest.approx(eur.low * pricing.USD_PER_EUR)
    assert usd.high == pytest.approx(eur.high * pricing.USD_PER_EUR)


def test_format():
    estimate = pricing.estimate_price({brick("Black"): 10_000}, 9, currency="EUR")
    assert estimate.format() == f"{estimate.low:,.0f}–{estimate.high:,.0f} €".replace(",", " ")
    assert estimate.format(1234.4) == "1 234 €"
    assert pricing.estimate_price({}, 1, currency="USD").format(5) == "5 $"


@pytest.mark.parametrize(
    ("kwargs", "match"), [({"baseplate_kind": "gold"}, "baseplate"), ({"currency": "GBP"}, "currency")]
)
def test_rejects_unknown_options(kwargs, match):
    with pytest.raises(ValueError, match=match):
        pricing.estimate_price({}, 1, **kwargs)
