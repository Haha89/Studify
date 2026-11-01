from __future__ import annotations

import sys
from types import ModuleType

import pytest

from legome.cli import main
from legome.models.canvas import Canvas


def test_requires_an_image(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert "required: image" in capsys.readouterr().err


def test_rejects_a_missing_image(red_blue_image, capsys):
    with pytest.raises(SystemExit) as exc:
        main([str(red_blue_image.parent / "missing.png"), "--no-plot", "--no-open"])
    assert exc.value.code == 2
    assert "Could not read image" in capsys.readouterr().err


def test_rejects_a_file_that_is_not_an_image(tmp_path, capsys):
    not_an_image = tmp_path / "notes.txt"
    not_an_image.write_text("just some text", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main([str(not_an_image), "--no-plot", "--no-open"])
    assert exc.value.code == 2
    assert "notes.txt" in capsys.readouterr().err


def test_builds_a_guide_end_to_end(red_blue_image, tmp_path, capsys):
    out_dir = tmp_path / "guides"
    assert main([str(red_blue_image), "-s", "1", "-o", str(out_dir), "--no-plot", "--no-open"]) == 0

    guide = out_dir / "red_blue.html"
    assert guide.is_file()
    assert "<title>LegoMe: red_blue</title>" in guide.read_text(encoding="utf-8")

    stdout = capsys.readouterr().out
    assert "Canvas: 32 x 32 studs (1 x 1 baseplates)" in stdout
    assert "Total elements: 1024" in stdout
    assert "Total estimated cost:" in stdout


def test_gui_flag_launches_the_gui_without_an_image(monkeypatch):
    """--gui must not require an image, and must not need customtkinter installed."""
    launched = []
    fake_gui = ModuleType("legome.gui")
    fake_gui.run_gui = lambda: launched.append(True)
    monkeypatch.setitem(sys.modules, "legome.gui", fake_gui)

    assert main(["--gui"]) == 0
    assert launched == [True]


@pytest.mark.parametrize("size", ["0", "-1"])
def test_rejects_a_size_below_one(red_blue_image, capsys, size):
    with pytest.raises(SystemExit) as exc:
        main([str(red_blue_image), "--size", size, "--no-plot", "--no-open"])
    assert exc.value.code == 2
    assert "--size" in capsys.readouterr().err


def test_rejects_an_oversized_frame(red_blue_image, capsys):
    with pytest.raises(SystemExit) as exc:
        main([str(red_blue_image), "--frame", "16", "--no-plot", "--no-open"])
    assert exc.value.code == 2
    assert "--frame" in capsys.readouterr().err


def test_rejects_unknown_colour_names(red_blue_image, capsys):
    with pytest.raises(SystemExit) as exc:
        main([str(red_blue_image), "--colors", "Black,Purplish Unicorn", "--no-plot", "--no-open"])
    assert exc.value.code == 2
    assert "Purplish Unicorn" in capsys.readouterr().err


def test_colors_and_full_palette_are_exclusive(red_blue_image):
    with pytest.raises(SystemExit):
        main([str(red_blue_image), "--colors", "Black", "--full-palette", "--no-plot", "--no-open"])


def test_palette_options_end_to_end(red_blue_image, tmp_path, capsys):
    args = [str(red_blue_image), "-s", "1", "-o", str(tmp_path), "--no-plot", "--no-open"]
    assert main([*args, "--colors", "red, blue,Black", "--max-colors", "2", "--dither"]) == 0
    assert "Colors used: 2" in capsys.readouterr().out


def test_rejects_an_unknown_style(red_blue_image):
    with pytest.raises(SystemExit):
        main([str(red_blue_image), "--style", "baroque", "--no-plot", "--no-open"])


def test_style_end_to_end(red_blue_image, tmp_path):
    args = [str(red_blue_image), "-s", "1", "-o", str(tmp_path), "--no-plot", "--no-open"]
    assert main([*args, "--style", "comic", "--frame", "1"]) == 0


def test_price_options_end_to_end(red_blue_image, tmp_path, capsys):
    args = [str(red_blue_image), "-s", "1", "-o", str(tmp_path), "--no-plot", "--no-open"]
    assert main([*args, "--baseplates", "compatible", "--currency", "USD"]) == 0
    out = capsys.readouterr().out
    assert "Total estimated cost:" in out and "$" in out
    assert "compatible baseplates" in out and "shipping not included" in out


def test_bricklink_flag_writes_a_wanted_list_beside_the_guide(red_blue_image, tmp_path, capsys):
    args = [str(red_blue_image), "-s", "1", "-o", str(tmp_path), "--no-plot", "--no-open"]
    assert main(args) == 0
    assert not (tmp_path / "red_blue-bricklink.xml").exists()

    assert main([*args, "--bricklink"]) == 0
    assert "<ITEMID>3024</ITEMID>" in (tmp_path / "red_blue-bricklink.xml").read_text(encoding="utf-8")
    assert "BrickLink wanted list:" in capsys.readouterr().out


def test_bricklink_flag_names_colours_it_cannot_list(red_blue_image, tmp_path, capsys):
    args = [str(red_blue_image), "-s", "1", "-o", str(tmp_path), "--no-plot", "--no-open"]
    assert main([*args, "--bricklink", "--colors", "Chrome Gold,Black"]) == 0
    assert "add them by hand: Chrome Gold" in capsys.readouterr().out


def test_keep_strays_flag_reaches_the_canvas(red_blue_image, tmp_path, monkeypatch):
    seen = []
    real = Canvas.fit_from_image

    def spy(self, *args, **kwargs):
        seen.append(kwargs["clean_strays"])
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Canvas, "fit_from_image", spy)
    args = [str(red_blue_image), "-s", "1", "-o", str(tmp_path), "--no-plot", "--no-open"]
    main(args)
    main([*args, "--keep-strays"])
    assert seen == [True, False]


def test_palette_flag(red_blue_image, tmp_path):
    args = [str(red_blue_image), "-s", "1", "-o", str(tmp_path), "--no-plot", "--no-open"]
    assert main([*args, "--palette", "common"]) == 0
    with pytest.raises(SystemExit):
        main([*args, "--palette", "rainbow"])
    with pytest.raises(SystemExit):  # one way of choosing colours at a time
        main([*args, "--palette", "all", "--colors", "Black"])
