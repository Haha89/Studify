from __future__ import annotations

import sys
from types import ModuleType

import pytest

from legome.cli import main


def test_requires_an_image(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert "required: image" in capsys.readouterr().err


def test_rejects_a_missing_image(red_blue_image):
    with pytest.raises(FileNotFoundError):
        main([str(red_blue_image.parent / "missing.png"), "--no-plot", "--no-open"])


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
