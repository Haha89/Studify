from __future__ import annotations

import pytest

from legome.models.canvas import Canvas
from legome.models.pdf_creator import PdfCreator
from legome.models.static import COLORS, DIM_BASE


class TestGetBases:
    def test_splits_the_canvas_into_one_canvas_per_baseplate(self, two_base_canvas):
        bases = PdfCreator(two_base_canvas).get_bases()
        assert len(bases) == 2
        assert all(base.shape == (DIM_BASE, DIM_BASE) for base in bases)

    def test_bases_are_named_by_row_and_column(self, two_base_canvas):
        names = [base.name for base in PdfCreator(two_base_canvas).get_bases()]
        assert names == ["two-base - Base Row1:Col1", "two-base - Base Row1:Col2"]

    def test_bases_are_read_left_to_right(self, two_base_canvas):
        left, right = PdfCreator(two_base_canvas).get_bases()
        assert left.get_distribution() == {1: DIM_BASE * DIM_BASE}
        assert right.get_distribution() == {2: DIM_BASE * DIM_BASE}

    def test_a_canvas_smaller_than_one_baseplate_yields_no_bases(self, make_canvas):
        assert PdfCreator(make_canvas([[1, 2], [3, 4]])).get_bases() == []

    def test_an_empty_canvas_yields_no_bases(self):
        assert PdfCreator(Canvas(1)).get_bases() == []


class TestGenerateHtml:
    def test_writes_a_file_named_after_the_source(self, two_base_canvas, tmp_path):
        out = PdfCreator(two_base_canvas).generate_html(output_dir=tmp_path)
        assert out == tmp_path / "two-base.html"
        assert out.is_file()

    def test_copies_the_stylesheet_beside_the_guide(self, two_base_canvas, tmp_path):
        out = PdfCreator(two_base_canvas).generate_html(output_dir=tmp_path)
        stylesheet = out.parent / "style.css"
        assert stylesheet.is_file()
        assert 'href="./style.css"' in out.read_text(encoding="utf-8")
        assert stylesheet.read_text(encoding="utf-8").strip()

    def test_creates_a_missing_output_directory(self, two_base_canvas, tmp_path):
        target = tmp_path / "deep" / "nested"
        out = PdfCreator(two_base_canvas).generate_html(output_dir=target)
        assert out.parent == target
        assert target.is_dir()

    def test_falls_back_to_a_default_name_when_there_is_no_source(self, make_canvas, tmp_path):
        canvas = make_canvas([[1] * DIM_BASE for _ in range(DIM_BASE)])
        out = PdfCreator(canvas).generate_html(output_dir=tmp_path)
        assert out.name == "canvas.html"

    @pytest.fixture
    def html(self, two_base_canvas, tmp_path):
        return PdfCreator(two_base_canvas).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")

    def test_html_is_well_formed(self, html):
        assert html.lstrip().startswith("<!DOCTYPE html>")
        assert html.count("<tr") == html.count("</tr>")
        assert html.count("<table") == html.count("</table>")

    def test_html_titles_the_build(self, html):
        assert "<title>LegoMe: two-base</title>" in html

    def test_html_has_a_section_per_baseplate(self, html):
        assert html.count('<section class="card baseplate">') == 2
        assert "two-base - Base Row1:Col1" in html
        assert "two-base - Base Row1:Col2" in html

    def test_stud_maps_are_rendered_raw_not_escaped(self, html):
        assert "&lt;table" not in html

    def test_html_lists_the_bricks_needed(self, html):
        assert f'<span class="brick-id">#1</span> &times; {DIM_BASE * DIM_BASE}' in html

    def test_color_reference_lists_only_colors_actually_used(self, two_base_canvas, html):
        # two_base_canvas only ever uses brick ids 1 and 2.
        names = list(COLORS)
        used_name, unused_name = names[1], names[0]
        assert html.count(f">{used_name}<") == 1
        assert f">{names[2]}<" in html
        assert f">{unused_name}<" not in html

    def test_color_reference_shows_how_many_bricks_of_each_colour(self, html):
        assert f"<td>{DIM_BASE * DIM_BASE}</td>" in html

    def test_does_not_open_a_browser_by_default(self, two_base_canvas, tmp_path, monkeypatch):
        opened = []
        monkeypatch.setattr("legome.models.pdf_creator.webbrowser.open", lambda *a, **k: opened.append(a))
        PdfCreator(two_base_canvas).generate_html(output_dir=tmp_path)
        assert opened == []

    def test_opens_a_browser_when_asked(self, two_base_canvas, tmp_path, monkeypatch):
        opened = []
        monkeypatch.setattr("legome.models.pdf_creator.webbrowser.open", lambda *a, **k: opened.append(a))
        PdfCreator(two_base_canvas).generate_html(output_dir=tmp_path, open_in_browser=True)
        assert len(opened) == 1
        assert opened[0][0].startswith("file://")


class TestOverviewImages:
    def test_embeds_the_original_image_when_the_canvas_was_fitted_from_a_file(self, red_blue_image, tmp_path):
        canvas = Canvas(1)
        canvas.fit_from_image(red_blue_image)
        html = PdfCreator(canvas).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")

        assert "<figcaption>Original</figcaption>" in html
        assert 'src="data:image/png;base64,' in html

    def test_embeds_the_reconstructed_mosaic_as_a_pixelated_png(self, red_blue_image, tmp_path):
        canvas = Canvas(1)
        canvas.fit_from_image(red_blue_image)
        html = PdfCreator(canvas).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")

        assert "<figcaption>Reconstructed &middot; 32 x 32 studs</figcaption>" in html
        assert 'alt="Reconstructed LEGO mosaic" class="pixelated"' in html

    def test_omits_the_original_image_when_the_canvas_has_no_source_path(self, two_base_canvas, tmp_path):
        # two_base_canvas is built directly from brick ids, bypassing fit_from_image.
        html = PdfCreator(two_base_canvas).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")

        assert "<figcaption>Original</figcaption>" not in html
        assert "<figcaption>Reconstructed" in html  # the mosaic can still be shown

    def test_omits_the_overview_section_entirely_for_an_empty_canvas(self, tmp_path):
        html = PdfCreator(Canvas(1)).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")

        assert "Overview" not in html
        assert "<figcaption>Original</figcaption>" not in html
        assert "<figcaption>Reconstructed" not in html

    def test_reconstructed_mosaic_is_the_canvas_preview_image(self, two_base_canvas, tmp_path):
        import base64
        import re

        import cv2
        import numpy as np

        html = PdfCreator(two_base_canvas).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")
        match = re.search(r'src="data:image/png;base64,([^"]+)" alt="Reconstructed LEGO mosaic"', html)
        assert match, "could not find the reconstructed mosaic's data URI in the guide"

        png_bytes = base64.b64decode(match.group(1))
        decoded_bgr = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        decoded_rgb = cv2.cvtColor(decoded_bgr, cv2.COLOR_BGR2RGB)
        # The embedded preview is the scaled, gridded image (not a raw 1px-per-stud
        # copy) -- see TestToPreviewImage for the grid/scaling behaviour itself.
        assert np.array_equal(decoded_rgb, two_base_canvas.to_preview_image())


class TestLocalColorIds:
    """The guide numbers colours 1..N in the order they appear in the palette,
    not by their raw (sparse, arbitrary-looking) palette id."""

    @pytest.fixture
    def sparse_canvas(self, make_canvas):
        # Global ids 5 and 50, one per baseplate -- deliberately not already 1 and 2,
        # so a passing test can't be a coincidence of the fixture's chosen ids.
        row = [5] * DIM_BASE + [50] * DIM_BASE
        canvas = make_canvas([row[:] for _ in range(DIM_BASE)], bases=2, name="sparse")
        canvas.source = "sparse"
        return canvas

    def test_get_local_ids_numbers_from_one_in_palette_id_order(self, sparse_canvas):
        assert PdfCreator(sparse_canvas).get_local_ids() == {5: 1, 50: 2}

    def test_stud_maps_use_local_ids_not_palette_ids(self, sparse_canvas):
        bases = PdfCreator(sparse_canvas).get_bases_maps_and_repartition()
        left, right = bases
        assert ">1</td>" in left["table"]
        assert ">5</td>" not in left["table"]
        assert ">2</td>" in right["table"]
        assert ">50</td>" not in right["table"]

    def test_bricks_needed_lists_use_local_ids(self, sparse_canvas):
        bases = PdfCreator(sparse_canvas).get_bases_maps_and_repartition()
        assert bases[0]["distribution"] == {1: DIM_BASE * DIM_BASE}
        assert bases[1]["distribution"] == {2: DIM_BASE * DIM_BASE}

    def test_color_reference_uses_local_ids_but_keeps_the_real_name_and_rgb(self, sparse_canvas):
        names = list(COLORS)
        used = PdfCreator(sparse_canvas).get_used_colors()
        assert [c["id"] for c in used] == [1, 2]
        assert [c["name"] for c in used] == [names[5], names[50]]
        assert [c["rgb"] for c in used] == [COLORS[names[5]], COLORS[names[50]]]

    def test_guide_html_is_consistent_about_local_ids_end_to_end(self, sparse_canvas, tmp_path):
        html = PdfCreator(sparse_canvas).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")
        assert '<span class="brick-id">#1</span>' in html
        assert '<span class="brick-id">#2</span>' in html
        assert '<span class="brick-id">#5</span>' not in html
        assert '<span class="brick-id">#50</span>' not in html
