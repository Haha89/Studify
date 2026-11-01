from __future__ import annotations

import cv2
import numpy as np
import pytest

from studify.models.static import COLORS
from studify.process import build_canvas

NAMES = list(COLORS)


@pytest.fixture
def logo(tmp_path):
    """A red disc on blue, with the soft edge of any exported logo."""
    img = np.zeros((400, 400, 3), dtype=np.uint8)
    img[:] = (0xDD, 0x66, 0x33)
    cv2.circle(img, (200, 200), 110, (0x33, 0x33, 0xEE), -1, cv2.LINE_AA)
    ok, data = cv2.imencode(".png", img)
    assert ok
    path = tmp_path / "logo.png"
    path.write_bytes(data.tobytes())
    return path


def colors_used(canvas) -> set[str]:
    return {NAMES[brick_id] for brick_id in canvas.get_distribution()}


def test_by_default_edges_blend_into_other_colours(logo):
    assert len(colors_used(build_canvas(3, logo))) > 2


@pytest.mark.parametrize("frame", [0, 2])
def test_sharp_edges_keep_only_the_logos_colours_and_the_border(logo, frame):
    canvas = build_canvas(3, logo, frame=frame, sharp=True)
    top, bottom, left, right = canvas.content_box
    inside = canvas.get_brick_ids()[top:bottom, left:right]
    assert {NAMES[brick_id] for brick_id in np.unique(inside).tolist()} == {"Blue", "Red"}


def test_sharp_edges_keep_the_shape(logo):
    soft, sharp = build_canvas(3, logo), build_canvas(3, logo, sharp=True)
    blue, red = NAMES.index("Blue"), NAMES.index("Red")
    ids = sharp.get_brick_ids()
    assert ids[48, 48] == red and ids[5, 5] == blue
    assert (soft.get_brick_ids() != ids).mean() < 0.05


def test_sharp_edges_leave_the_frame_alone(logo):
    canvas = build_canvas(3, logo, frame=2, frame_color="white", sharp=True)
    assert set(np.unique(canvas.get_brick_ids()[:2]).tolist()) == {NAMES.index("White")}


def test_dither_wins_over_sharp_edges(logo):
    dithered = build_canvas(1, logo, dither=True)
    both = build_canvas(1, logo, dither=True, sharp=True)
    assert np.array_equal(dithered.get_brick_ids(), both.get_brick_ids())


@pytest.mark.parametrize("style", ["pop", "comic"])
def test_sharp_edges_work_with_every_style(logo, style):
    assert build_canvas(1, logo, style=style, sharp=True).shape == (32, 32)
