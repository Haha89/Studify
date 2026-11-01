from __future__ import annotations

import cv2
import numpy as np
import pytest

from studify.models.brickstore import BrickStore
from studify.models.canvas import Canvas
from studify.models.style import STYLES, WORKING_MAX_DIMENSION, Style, outline_mask, retouch
from studify.process import process


def _saturation(img: np.ndarray) -> float:
    return float(cv2.cvtColor(img, cv2.COLOR_RGB2HSV)[:, :, 1].mean())


def test_every_style_palette_names_real_colours():
    for style in STYLES.values():
        if style.colors:
            assert len(BrickStore.bricks_named(list(style.colors))) == len(style.colors)


def test_natural_style_leaves_the_image_unchanged():
    img = np.random.default_rng(0).integers(0, 256, size=(40, 60, 3), dtype=np.uint8)
    assert np.array_equal(retouch(img, STYLES["natural"]), img)


def test_retouch_shrinks_large_images_but_never_enlarges():
    big = np.zeros((2000, 1000, 3), dtype=np.uint8)
    assert retouch(big, Style()).shape[:2] == (WORKING_MAX_DIMENSION, WORKING_MAX_DIMENSION // 2)
    small = np.zeros((50, 30, 3), dtype=np.uint8)
    assert retouch(small, Style()).shape[:2] == (50, 30)


def test_saturation_boost_makes_colours_more_vivid():
    dull = np.full((20, 20, 3), (150, 120, 110), dtype=np.uint8)
    assert _saturation(retouch(dull, Style(saturation=1.6))) > _saturation(dull)


def test_flatten_smooths_texture():
    noisy = np.clip(128 + np.random.default_rng(1).normal(0, 20, size=(60, 60, 3)), 0, 255).astype(np.uint8)
    assert retouch(noisy, Style(flatten=3)).std() < noisy.std()


class TestOutlineMask:
    def test_finds_a_long_edge(self):
        img = np.zeros((32, 32, 3), dtype=np.uint8)
        img[:, 16:] = 255
        mask = outline_mask(img)
        assert mask[:, 14:18].any(axis=1).sum() >= 28  # a line down (nearly) the full height
        assert not mask[:, :10].any() and not mask[:, 22:].any()

    def test_drops_short_fragments(self):
        img = np.zeros((32, 32, 3), dtype=np.uint8)
        img[15:17, 15:17] = 255  # a speck: its edge is only a few studs long
        assert not outline_mask(img, min_length=20).any()

    def test_flat_image_has_no_outline(self):
        assert not outline_mask(np.full((16, 16, 3), 90, dtype=np.uint8)).any()


@pytest.fixture
def square_image(tmp_path):
    """A white square on a mid-grey background: one strong, closed edge."""
    img = np.full((64, 64, 3), 128, dtype=np.uint8)
    img[16:48, 16:48] = 255
    path = tmp_path / "square.png"
    cv2.imwrite(str(path), img)
    return path


def test_comic_style_outlines_in_black(square_image):
    black = BrickStore.bricks_named(["Black"])[0]
    plain, comic = Canvas(1), Canvas(1)
    plain.fit_from_image(square_image)
    comic.fit_from_image(square_image, style=STYLES["comic"])
    assert black.id not in plain.get_distribution()
    assert comic.get_distribution()[black.id] >= 16


def test_comic_outline_survives_a_colour_limit(square_image):
    canvas = Canvas(1)
    canvas.fit_from_image(square_image, style=STYLES["comic"], max_colors=2)
    assert BrickStore.bricks_named(["Black"])[0].id in canvas.get_distribution()


def test_pop_style_uses_its_own_palette(square_image, tmp_path):
    pop = {brick.id for brick in BrickStore.bricks_named(list(STYLES["pop"].colors))}
    canvas = process(1, square_image, output_dir=tmp_path, plot=False, open_in_browser=False, style="pop")
    assert set(canvas.get_distribution()) <= pop


def test_explicit_colours_override_the_style_palette(square_image, tmp_path):
    canvas = process(
        1, square_image, output_dir=tmp_path, plot=False, open_in_browser=False, style="pop", colors=["Tan", "Black"]
    )
    assert set(canvas.get_distribution()) <= {brick.id for brick in BrickStore.bricks_named(["Tan", "Black"])}


def test_build_canvas_skips_rare_colours_by_default(tmp_path):
    from studify.models.pricing import color_tier
    from studify.process import build_canvas

    # Random noise spans the whole colour cube, so every palette colour has a chance.
    rng = np.random.default_rng(7)
    path = tmp_path / "noise.png"
    cv2.imwrite(str(path), rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8))

    default = build_canvas(1, path)
    everything = build_canvas(1, path, palette="all")
    assert all(color_tier(i) != "rare" for i in default.get_distribution())
    assert any(color_tier(i) == "rare" for i in everything.get_distribution())
