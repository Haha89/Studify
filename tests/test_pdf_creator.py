from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from studify.models import pdf_creator
from studify.models.canvas import Canvas
from studify.models.pdf_creator import ORIGINAL_MAX_DIMENSION, PdfCreator
from studify.models.static import COLORS, DIM_BASE


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
        return (
            PdfCreator(two_base_canvas, plates=["1x1"]).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")
        )

    def test_html_is_well_formed(self, html):
        assert html.lstrip().startswith("<!DOCTYPE html>")
        assert html.count("<tr") == html.count("</tr>")
        assert html.count("<table") == html.count("</table>")

    def test_html_titles_the_build(self, html):
        assert "<title>Studify: two-base</title>" in html

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
        color_table = html[html.index('class="color-table"') :]
        assert color_table.count(f"<td>{used_name}</td>") == 1
        assert f"<td>{names[2]}</td>" in color_table
        assert f">{unused_name}<" not in html

    def test_color_reference_shows_how_many_bricks_of_each_colour(self, html):
        assert f"<td>{DIM_BASE * DIM_BASE}</td>" in html

    def test_does_not_open_a_browser_by_default(self, two_base_canvas, tmp_path, monkeypatch):
        opened = []
        monkeypatch.setattr("studify.models.pdf_creator.webbrowser.open", lambda *a, **k: opened.append(a))
        PdfCreator(two_base_canvas).generate_html(output_dir=tmp_path)
        assert opened == []

    def test_opens_a_browser_when_asked(self, two_base_canvas, tmp_path, monkeypatch):
        opened = []
        monkeypatch.setattr("studify.models.pdf_creator.webbrowser.open", lambda *a, **k: opened.append(a))
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

    def test_embeds_a_large_original_shrunk_to_a_jpeg(self, tmp_path):
        import base64
        import re

        import cv2
        import numpy as np

        source = tmp_path / "large.png"
        source.write_bytes(cv2.imencode(".png", np.full((2000, 4000, 3), 128, dtype=np.uint8))[1].tobytes())
        canvas = Canvas(1)
        canvas.fit_from_image(source)
        html = PdfCreator(canvas).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")

        encoded = re.search(r'src="data:image/jpeg;base64,([^"]+)" alt="Original image"', html).group(1)
        embedded = cv2.imdecode(np.frombuffer(base64.b64decode(encoded), dtype=np.uint8), cv2.IMREAD_COLOR)
        assert embedded.shape[:2] == (ORIGINAL_MAX_DIMENSION // 2, ORIGINAL_MAX_DIMENSION)

    def test_leaves_out_an_original_that_has_since_disappeared(self, two_base_canvas, tmp_path):
        two_base_canvas.source_path = tmp_path / "gone.png"
        html = PdfCreator(two_base_canvas).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")

        assert "<figcaption>Original</figcaption>" not in html

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

    def test_bricks_needed_shows_each_colour_swatch_and_name(self, sparse_canvas, tmp_path):
        html = PdfCreator(sparse_canvas).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")
        names = list(COLORS)
        for palette_id in (5, 50):
            name, rgb = names[palette_id], COLORS[names[palette_id]]
            assert f'<li title="{name}">' in html
            assert f'<span class="swatch" style="background: rgb{rgb}"></span>' in html
            assert f'<span class="brick-name">{name}</span>' in html


class TestHtmlToPdf:
    @pytest.fixture
    def guide(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pdf_creator, "find_browser", lambda: "my-chrome")
        path = tmp_path / "guide.html"
        path.write_text("<html></html>", encoding="utf-8")
        return path

    @staticmethod
    def _fake_browser(monkeypatch, returncode=0, stderr=b"", writes_pdf=True):
        """Replace the browser process; returns the list its command line lands in."""
        commands = []

        def run(command, **kwargs):
            commands.append(command)
            if writes_pdf:
                target = next(arg for arg in command if arg.startswith("--print-to-pdf=")).split("=", 1)[1]
                Path(target).write_bytes(b"%PDF-fake")
            return SimpleNamespace(returncode=returncode, stderr=stderr)

        monkeypatch.setattr(pdf_creator.subprocess, "run", run)
        return commands

    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (FileNotFoundError("no such file"), "Could not start the browser my-chrome"),
            (subprocess.TimeoutExpired("my-chrome", 1), "timed out"),
        ],
    )
    def test_a_browser_that_cannot_run_or_finish_is_reported(self, guide, monkeypatch, error, message):
        def run(command, **kwargs):
            raise error

        monkeypatch.setattr(pdf_creator.subprocess, "run", run)
        with pytest.raises(RuntimeError, match=message):
            pdf_creator.html_to_pdf(guide, guide.with_suffix(".pdf"))

    def test_prints_the_guide_headless_and_untagged(self, guide, monkeypatch):
        commands = self._fake_browser(monkeypatch)
        pdf = pdf_creator.html_to_pdf(guide, guide.with_suffix(".pdf"))

        command = commands[0]
        assert pdf == guide.with_suffix(".pdf").resolve() and pdf.is_file()
        assert command[0] == "my-chrome"
        assert "--headless=new" in command and "--disable-pdf-tagging" in command
        assert command[-1] == guide.resolve().as_uri()

    def test_a_browser_error_is_reported(self, guide, monkeypatch):
        self._fake_browser(monkeypatch, returncode=1, stderr=b"it broke", writes_pdf=False)
        with pytest.raises(RuntimeError, match="it broke"):
            pdf_creator.html_to_pdf(guide, guide.with_suffix(".pdf"))

    def test_a_browser_that_writes_nothing_is_reported(self, guide, monkeypatch):
        self._fake_browser(monkeypatch, writes_pdf=False)
        with pytest.raises(RuntimeError, match="PDF printing failed"):
            pdf_creator.html_to_pdf(guide, guide.with_suffix(".pdf"))


class TestTintedMaps:
    def test_each_stud_is_tinted_with_its_colour(self, two_base_canvas, tmp_path):
        html = PdfCreator(two_base_canvas).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")
        rgb = COLORS[list(COLORS)[1]]  # Brick id 1 is the guide's colour 1.
        assert f".stud-map td.c1 {{ background: rgb{rgb};" in html
        assert 'class="c1 ' in html

    def test_can_be_turned_off(self, two_base_canvas, tmp_path):
        html = PdfCreator(two_base_canvas, tinted=False).generate_html(output_dir=tmp_path).read_text(encoding="utf-8")
        assert "background: rgb(" not in html.split("</head>")[0]

    @pytest.mark.parametrize(("rgb", "text"), [((0, 0, 0), "#fff"), ((255, 255, 255), "#000"), ((5, 19, 29), "#fff")])
    def test_numbers_stay_readable(self, rgb, text):
        assert pdf_creator._text_color(rgb) == text
