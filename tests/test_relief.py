from __future__ import annotations

import cv2
import numpy as np
import pytest

from studify.models.bricklink import ROUND_TILE
from studify.models.pdf_creator import PdfCreator
from studify.models.plates import PLATE_SIZES
from studify.models.realistic import render_mosaic
from studify.models.relief import (
    BACKGROUND,
    CORE,
    SUBJECT,
    SUPPORT_BRICK,
    core_mask,
    lay_out,
    subject_levels,
    subject_mask,
)
from studify.process import build_canvas


@pytest.fixture
def disc_image(tmp_path):
    """A red disc on a blue background: a subject GrabCut can't miss."""
    img = np.full((320, 320, 3), (200, 80, 20), dtype=np.uint8)
    cv2.circle(img, (160, 160), 90, (30, 30, 220), -1)
    path = tmp_path / "disc.png"
    ok, data = cv2.imencode(".png", img)
    assert ok
    path.write_bytes(data.tobytes())
    return path


def disc(size=64):
    img = np.full((size, size, 3), (20, 80, 200), dtype=np.uint8)
    cv2.circle(img, (size // 2, size // 2), size // 3, (220, 30, 30), -1)
    return img


def test_grabcut_finds_a_clear_subject():
    mask = subject_mask(disc())
    assert mask[32, 32] and not mask[2, 2]
    assert 0.2 < mask.mean() < 0.5


def test_the_subject_is_the_same_every_time():
    assert np.array_equal(subject_levels(disc()), subject_levels(disc()))


def test_a_uniform_picture_falls_back_to_a_central_subject():
    mask = subject_mask(np.full((64, 64, 3), 128, dtype=np.uint8))
    assert mask[32, 32] and not mask[0, 0]


def test_the_core_sits_inside_the_subject_away_from_its_outline():
    levels = subject_levels(disc())
    assert levels[32, 32] == CORE
    assert set(np.unique(levels)) == {BACKGROUND, SUBJECT, CORE}
    assert not (levels[subject_mask(disc()) == 0] == CORE).any()


def test_a_subject_too_thin_for_a_core_has_none():
    thin = np.zeros((32, 32), dtype=bool)
    thin[10:14, 2:30] = True
    assert not core_mask(thin).any()


def grids():
    ids = np.full((32, 32), 11)
    heights = np.zeros((32, 32), dtype=int)
    heights[8:24, 8:24] = 2
    return ids, heights


def test_plates_never_span_two_heights():
    ids, heights = grids()
    layout = lay_out(ids, PLATE_SIZES, heights, np.zeros((32, 32), dtype=bool))
    for plate in layout.plates:
        area = heights[plate.row : plate.row + plate.height, plate.col : plate.col + plate.width]
        assert (area == area[0, 0]).all()
    assert (layout.regions >= 0).all()


def test_supports_cover_the_raised_area_once_per_layer_and_cross():
    ids, heights = grids()
    layout = lay_out(ids, PLATE_SIZES, heights, np.zeros((32, 32), dtype=bool))
    assert len(layout.supports) == 2
    for layer in layout.supports:
        covered = np.zeros((32, 32), dtype=int)
        for plate in layer:
            assert plate.brick_id == SUPPORT_BRICK
            covered[plate.row : plate.row + plate.height, plate.col : plate.col + plate.width] += 1
        assert np.array_equal(covered, (heights >= 1).astype(int))
    first, second = ({(p.row, p.col, p.height, p.width) for p in layer} for layer in layout.supports)
    assert first != second


def test_parts_add_tiles_plates_and_supports():
    ids, heights = grids()
    tiles = np.zeros((32, 32), dtype=bool)
    tiles[:2, :2] = True
    layout = lay_out(ids, PLATE_SIZES, heights, tiles)
    parts = layout.parts(ids)
    assert parts[11, ROUND_TILE] == 4
    assert sum(parts.values()) == 4 + len(layout.plates) + layout.support_count


def test_shadows_fall_only_on_the_lower_side():
    colors = np.full((8, 8, 3), 200, dtype=np.uint8)
    regions = np.arange(64).reshape(8, 8)
    heights = np.zeros((8, 8), dtype=int)
    heights[:4, :4] = 3
    flat = render_mosaic(colors, regions, None, 20)
    raised = render_mosaic(colors, regions, heights, 20)
    assert (raised[90, 90] < flat[90, 90]).all()  # Just past the raised corner.
    assert np.array_equal(raised[:70, :70], flat[:70, :70])  # On top of it.
    assert np.array_equal(raised[150:, 150:], flat[150:, 150:])  # Far away.


class TestCanvas:
    def test_flat_is_unchanged(self, disc_image):
        canvas = build_canvas(1, disc_image)
        layout = canvas.layout()
        assert not layout.tiles.any() and not layout.supports
        assert layout.parts(canvas.get_brick_ids()) == canvas.get_part_counts()

    def test_stepped_raises_the_subject_but_not_the_frame(self, disc_image):
        canvas = build_canvas(1, disc_image, relief="stepped", frame=2)
        layout = canvas.layout()
        assert layout.heights[16, 16] >= 2
        assert not layout.heights[:2].any()
        assert layout.support_count > 0

    def test_stepped_uses_no_round_tiles(self, disc_image):
        canvas = build_canvas(1, disc_image, relief="stepped", frame=2)
        layout = canvas.layout()
        assert not layout.tiles.any()
        assert ROUND_TILE not in {part for _, part in layout.parts(canvas.get_brick_ids())}

    def test_stepped_and_round_tiles_are_refused_together(self, disc_image):
        with pytest.raises(ValueError):
            build_canvas(1, disc_image, relief="stepped", round_tiles=True)

    @pytest.mark.parametrize("relief", ["sculpted", "texture"])
    def test_an_unknown_relief_is_refused(self, disc_image, relief):
        with pytest.raises(ValueError):
            build_canvas(1, disc_image, relief=relief)


def test_a_stepped_guide_has_a_map_per_hidden_layer(disc_image, tmp_path):
    canvas = build_canvas(1, disc_image, relief="stepped", frame=2)
    html = PdfCreator(canvas, lang="fr").generate_html(output_dir=tmp_path).read_text(encoding="utf-8")
    assert "couche cachée 1 sur 3" in html and "couche cachée 3 sur 3" in html
    assert "Plaques cachées" in html and "Non testé" in html


def test_a_flat_guide_mentions_no_relief(disc_image, tmp_path):
    canvas = build_canvas(1, disc_image, frame=2)
    html = PdfCreator(canvas, lang="fr").generate_html(output_dir=tmp_path).read_text(encoding="utf-8")
    assert "couche cachée" not in html and "Relief" not in html
