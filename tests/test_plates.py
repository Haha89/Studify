from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from studify.cli import main
from studify.models.bricklink import wanted_list_xml
from studify.models.canvas import Canvas
from studify.models.pdf_creator import PdfCreator
from studify.models.plates import (
    PLATE_PARTS,
    PLATE_SIZES,
    Plate,
    count_parts,
    pack_baseplates,
    pack_plates,
    plate_regions,
    plate_sizes,
)
from studify.models.static import COLORS, DIM_BASE
from studify.models.utils import make_map


def assert_exact_cover(grid, plates):
    """Every stud is covered once, by a plate of its own colour, of a real size."""
    covered = np.zeros(grid.shape, dtype=int)
    for plate in plates:
        area = grid[plate.row : plate.row + plate.height, plate.col : plate.col + plate.width]
        assert area.shape == (plate.height, plate.width)
        assert (area == plate.brick_id).all()
        assert plate.part in PLATE_PARTS.values()
        covered[plate.row : plate.row + plate.height, plate.col : plate.col + plate.width] += 1
    assert (covered == 1).all()


class TestPackPlates:
    def test_one_colour_uses_the_largest_plates(self):
        plates = pack_plates(np.ones((2, 2), dtype=int))
        assert plates == [Plate(0, 0, 2, 2, 1)]
        assert plates[0].size == "2x2" and plates[0].part == "3022"

    def test_tries_both_orientations(self):
        plates = pack_plates(np.ones((4, 1), dtype=int))
        assert plates == [Plate(0, 0, 4, 1, 1)]
        assert plates[0].size == "1x4"

    def test_a_lone_stud_is_a_1x1(self):
        grid = np.array([[1, 2], [2, 2]])
        assert Plate(0, 0, 1, 1, 1) in pack_plates(grid)

    def test_colours_never_share_a_plate(self):
        grid = np.array([[1, 1, 2, 2]])
        assert pack_plates(grid) == [Plate(0, 0, 1, 2, 1), Plate(0, 2, 1, 2, 2)]

    @pytest.mark.parametrize("seed", range(5))
    @pytest.mark.parametrize("colors", [1, 2, 5])
    @pytest.mark.parametrize("sizes", [PLATE_SIZES, ["1x1"], ["1x3"], ["2x2"], ["1x2", "2x2"]])
    def test_random_grids_are_covered_exactly_once(self, seed, colors, sizes):
        grid = np.random.default_rng(seed).integers(0, colors, (DIM_BASE, DIM_BASE))
        plates = pack_plates(grid, sizes)
        assert_exact_cover(grid, plates)
        assert {plate.size for plate in plates} <= {*sizes, "1x1"}

    def test_only_uses_the_chosen_sizes(self):
        grid = np.ones((4, 4), dtype=int)
        assert {plate.size for plate in pack_plates(grid, ["1x2"])} == {"1x2"}
        assert {plate.size for plate in pack_plates(grid, ["2x2"])} == {"2x2"}
        assert {plate.size for plate in pack_plates(grid, ["1x1"])} == {"1x1"}

    def test_1x1_fills_what_the_chosen_sizes_cannot(self):
        assert {plate.size for plate in pack_plates(np.ones((1, 3), dtype=int), ["1x2"])} == {"1x1", "1x2"}


class TestAvailability:
    def test_skips_sizes_that_do_not_exist_in_the_colour(self):
        coral = list(COLORS).index("Coral")  # Comes in 1x1, 1x2 and 1x4, not 1x3 or 2x2.
        sizes = {plate.size for plate in pack_plates(np.full((3, 3), coral), PLATE_SIZES)}
        assert "2x2" not in sizes and "1x3" not in sizes

    def test_a_colour_without_a_1x1_is_still_covered(self):
        purple = list(COLORS).index("Purple")  # Comes in 1x4 and 2x2 only.
        grid = np.full((1, 1), purple)
        assert pack_plates(grid) == [Plate(0, 0, 1, 1, purple)]


class TestPlateSizes:
    def test_all_five_are_offered(self):
        assert PLATE_SIZES == ("1x1", "1x2", "1x3", "1x4", "2x2")

    def test_1x1_is_always_included_and_the_order_is_standard(self):
        assert plate_sizes(["2x2", "1x3"]) == ("1x1", "1x3", "2x2")
        assert plate_sizes([]) == ("1x1",)

    def test_unknown_sizes_are_rejected(self):
        with pytest.raises(ValueError, match="2x4"):
            plate_sizes(["2x4"])


def test_an_empty_canvas_needs_no_parts():
    assert Canvas(1).get_part_counts() == {}


def test_plates_stay_within_their_baseplate():
    grid = np.ones((DIM_BASE, DIM_BASE * 2), dtype=int)
    plates = pack_baseplates(grid)
    assert_exact_cover(grid, plates)
    for plate in plates:
        assert plate.col // DIM_BASE == (plate.col + plate.width - 1) // DIM_BASE


def test_plate_regions_number_each_plate():
    plates = [Plate(0, 0, 1, 2, 1), Plate(0, 2, 1, 1, 2)]
    assert plate_regions((1, 3), plates).tolist() == [[0, 0, 1]]


def test_count_parts():
    plates = [Plate(0, 0, 1, 4, 1), Plate(1, 0, 4, 1, 1), Plate(0, 4, 1, 1, 2)]
    assert count_parts(plates) == {(1, "3710"): 2, (2, "3024"): 1}


def test_wanted_list_lists_each_part_and_colour_with_spares():
    black = 0
    xml = wanted_list_xml({}, {(black, "3022"): 20, (black, "3024"): 1})
    assert "<ITEMID>3022</ITEMID><COLOR>11</COLOR><MINQTY>21</MINQTY>" in xml
    assert "<ITEMID>3024</ITEMID><COLOR>11</COLOR><MINQTY>2</MINQTY>" in xml


class TestGuide:
    @pytest.fixture
    def canvas(self, make_canvas):
        row = [1] * DIM_BASE + [2] * DIM_BASE
        canvas = make_canvas([row[:] for _ in range(DIM_BASE)], bases=2, name="plates")
        canvas.source = "plates"
        return canvas

    def test_uses_far_fewer_parts_on_flat_colour(self, canvas: Canvas):
        assert sum(canvas.get_part_counts().values()) == 2 * (DIM_BASE * DIM_BASE // 4)
        assert sum(canvas.get_part_counts(["1x1"]).values()) == 2 * DIM_BASE * DIM_BASE

    def test_lists_plates_and_outlines_them(self, canvas, tmp_path):
        plain = PdfCreator(canvas, plates=["1x1"]).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")
        html = PdfCreator(canvas).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")

        assert "Bricks needed" in plain and "Tiles needed" not in plain
        assert "Tiles needed" in html and "1x4&nbsp;&times;&nbsp;256" in html
        assert "<dt>Tiles</dt><dd>512</dd>" in html
        assert html.count("border-end") > plain.count("border-end")

    def test_each_plate_is_one_merged_cell(self):
        grid = np.array([[1, 1, 1, 1], [2, 3, 3, 4], [2, 3, 3, 4]])
        plates = pack_plates(grid)
        table = make_map(pd.DataFrame(grid), plate_regions(grid.shape, plates))

        assert table.count("<td") == len(plates) == 4
        assert re.search(r'<td colspan="4" class="c1 [^"]*">1</td>', table)
        assert re.search(r'<td rowspan="2" class="c2 [^"]*">2</td>', table)
        assert re.search(r'<td rowspan="2" colspan="2" class="c3 [^"]*">3</td>', table)

    def test_merged_cells_are_outlined_on_all_sides(self):
        grid = np.array([[1, 1, 1, 1], [2, 3, 3, 4], [2, 3, 3, 4]])
        table = make_map(pd.DataFrame(grid), plate_regions(grid.shape, pack_plates(grid)))

        top = re.search(r'<td colspan="4" class="([^"]*)"', table).group(1).split()
        assert {"border-top", "border-start", "border-bottom", "border-end"} <= set(top)
        square = re.search(r'<td rowspan="2" colspan="2" class="([^"]*)"', table).group(1).split()
        assert {"border-bottom", "border-end"} <= set(square)  # The others come from its neighbours.

    def test_every_stud_stays_covered_on_a_real_baseplate(self):
        grid = np.random.default_rng(0).integers(0, 3, (DIM_BASE, DIM_BASE))
        plates = pack_plates(grid)
        table = make_map(pd.DataFrame(grid), plate_regions(grid.shape, plates))

        assert table.count("<td") == len(plates)
        rowspans = [int(v) for v in re.findall(r'rowspan="(\d+)"', table)]
        assert table.count("<tr>") == DIM_BASE + 1 and max(rowspans) <= 4

    def test_can_be_written_in_french(self, canvas, tmp_path):
        html = PdfCreator(canvas, lang="fr").generate_html(output_dir=tmp_path).read_text(encoding="utf-8")
        assert '<html lang="fr">' in html
        assert "Baseplate ligne 1, colonne 2" in html
        assert "Tuiles nécessaires" in html and "hors livraison" in html
        assert "baseplates LEGO officielles" in html
        assert "Tiles needed" not in html


def test_cli_plates_and_lang(red_blue_image, tmp_path, capsys):
    args = [str(red_blue_image), "-s", "1", "-o", str(tmp_path), "--no-plot", "--no-open"]
    assert main([*args, "--plates", "1x1,2x2", "--bricklink", "--lang", "fr"]) == 0
    assert "Tiles to place:" in capsys.readouterr().out
    wanted_list = (tmp_path / "red_blue-bricklink.xml").read_text(encoding="utf-8")
    assert "<ITEMID>3068</ITEMID>" in wanted_list and "<ITEMID>2431</ITEMID>" not in wanted_list
    assert "Tuiles nécessaires" in (tmp_path / "red_blue.html").read_text(encoding="utf-8")


def test_cli_uses_every_size_by_default_and_can_use_only_1x1(red_blue_image, tmp_path):
    args = [str(red_blue_image), "-s", "1", "-o", str(tmp_path), "--no-plot", "--no-open", "--bricklink"]
    assert main(args) == 0
    assert "Tiles needed" in (tmp_path / "red_blue.html").read_text(encoding="utf-8")

    assert main([*args, "--plates", "1x1"]) == 0
    assert "Bricks needed" in (tmp_path / "red_blue.html").read_text(encoding="utf-8")
    assert "<ITEMID>3070</ITEMID>" in (tmp_path / "red_blue-bricklink.xml").read_text(encoding="utf-8")


def test_cli_rejects_an_unknown_plate_size(red_blue_image, capsys):
    with pytest.raises(SystemExit) as exc:
        main([str(red_blue_image), "--plates", "2x4", "--no-plot", "--no-open"])
    assert exc.value.code == 2
    assert "Unknown plate size" in capsys.readouterr().err
