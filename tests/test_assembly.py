from __future__ import annotations

import cv2
import numpy as np
import pytest

from studify.models.assembly import PANEL_STUDS, STRUCTURE_BRICK, assembly_parts, panel_grid, pin_joins
from studify.models.bricklink import comes_in
from studify.models.pdf_creator import PdfCreator
from studify.process import build_canvas


@pytest.fixture
def picture(tmp_path):
    img = np.full((320, 320, 3), (200, 80, 20), dtype=np.uint8)
    cv2.circle(img, (160, 160), 90, (30, 30, 220), -1)
    ok, data = cv2.imencode(".png", img)
    assert ok
    path = tmp_path / "disc.png"
    path.write_bytes(data.tobytes())
    return path


def test_a_3x3_panel_mosaic_takes_what_the_official_sets_do():
    """From the inventory of 31200 and the instructions of 31197. Corner plates are
    black here where the sets use red ones on the back, and the sets' 44 pins include
    4 beyond the 40 their instructions place."""
    parts = {part: count for (_, part), count in assembly_parts(48, 48).items()}
    assert parts == {
        "65803": 9,
        "2780": 40,
        "2458": 24,
        "67139": 2,
        "2445": 4,
        "4282": 8,
        "2465": 12,
        "3008": 12,
        "3010": 8,
        "3005": 12,
        "2357": 4,
        "11212": 4,
        "2420": 8,
        "3022": 9,
        "4162": 20,
        "63864": 8,
    }


def test_sizes_scale_with_the_panels():
    assert panel_grid(32, 64) == (2, 4)
    assert pin_joins(32, 64) == 4 + 6
    parts = {part: count for (_, part), count in assembly_parts(32, 64).items()}
    assert parts["65803"] == 8
    assert parts["2458"] == 2 * 2 * (2 + 4)


def test_a_single_panel_has_no_joins():
    parts = {part: count for (_, part), count in assembly_parts(PANEL_STUDS, PANEL_STUDS).items()}
    assert parts["2780"] == 4  # Just the hangers'.


def test_every_part_is_known_in_its_colour():
    assert all(comes_in(brick, part) for brick, part in assembly_parts(48, 48))
    assert all(brick == STRUCTURE_BRICK for brick, _ in assembly_parts(48, 48))


def test_plates_stay_within_a_panel(picture):
    canvas = build_canvas(1, picture, assembly="panels")
    for plate in canvas.layout().plates:
        assert plate.row // PANEL_STUDS == (plate.row + plate.height - 1) // PANEL_STUDS
        assert plate.col // PANEL_STUDS == (plate.col + plate.width - 1) // PANEL_STUDS


def test_panels_replace_the_price_of_baseplates(picture):
    canvas = build_canvas(1, picture, assembly="panels")
    parts = canvas.layout().parts(canvas.get_brick_ids())
    price = canvas.get_price("official", "EUR", parts)
    assert price.baseplates == 0
    assert price.baseplate_count == 0
    flat = build_canvas(1, picture)
    assert price.low > flat.get_price("official", "EUR", flat.layout().parts(flat.get_brick_ids())).bricks_low


def test_baseplates_add_no_structure(picture):
    canvas = build_canvas(1, picture)
    assert canvas.layout().assembly == {}
    assert canvas.layout().parts(canvas.get_brick_ids()) == canvas.get_part_counts()


def test_an_unknown_assembly_is_refused(picture):
    with pytest.raises(ValueError):
        build_canvas(1, picture, assembly="glue")


@pytest.mark.parametrize(
    ("assembly", "present", "absent"),
    [
        ("panels", ["65803", "3 goujons", "Panneaux"], ["panneau plat"]),
        ("baseplates", ["panneau plat"], ["65803", "3 goujons"]),
    ],
)
def test_the_guide_explains_how_it_holds_together(picture, tmp_path, assembly, present, absent):
    canvas = build_canvas(1, picture, assembly=assembly)
    html = PdfCreator(canvas, lang="fr").generate_html(output_dir=tmp_path).read_text(encoding="utf-8")
    assert "Assemblage" in html
    assert all(text in html for text in present)
    assert not any(text in html for text in absent)


def test_the_guide_warns_past_the_largest_official_size(picture, tmp_path):
    canvas = build_canvas(5, picture, assembly="panels")
    html = PdfCreator(canvas, lang="en").generate_html(output_dir=tmp_path).read_text(encoding="utf-8")
    assert "larger than any official set" in html
