from __future__ import annotations

from legome.process import process


def test_returns_the_fitted_canvas(red_blue_image, tmp_path):
    canvas = process(1, red_blue_image, output_dir=tmp_path, plot=False, open_in_browser=False)
    assert canvas.source == "red_blue"
    assert canvas.shape == (32, 32)
    assert sum(canvas.get_distribution().values()) == 32 * 32


def test_plots_only_when_asked(red_blue_image, tmp_path, monkeypatch):
    plotted = []
    monkeypatch.setattr("legome.models.canvas.Canvas.plot", staticmethod(lambda pixels: plotted.append(pixels.shape)))

    process(1, red_blue_image, output_dir=tmp_path, plot=False, open_in_browser=False)
    assert plotted == []

    process(1, red_blue_image, output_dir=tmp_path, plot=True, open_in_browser=False)
    assert plotted == [(32, 32, 3)]


def test_writes_the_guide_to_the_given_directory(red_blue_image, tmp_path):
    process(1, red_blue_image, output_dir=tmp_path / "out", plot=False, open_in_browser=False)
    assert (tmp_path / "out" / "red_blue.html").is_file()
