from __future__ import annotations

import matplotlib
import numpy as np
import pytest

# Keep matplotlib headless so plot tests never try to open a window.
matplotlib.use("Agg")

import cv2  # noqa: E402  (imported after the backend is pinned)
import matplotlib.pyplot as plt  # noqa: E402

from legome.models.brick import Brick  # noqa: E402
from legome.models.canvas import Canvas  # noqa: E402
from legome.models.static import DIM_BASE  # noqa: E402


@pytest.fixture(autouse=True)
def close_figures():
    """Stop matplotlib figures leaking between tests."""
    yield
    plt.close("all")


@pytest.fixture
def red_blue_image(tmp_path):
    """A 128x64 PNG, red on the left half and blue on the right."""
    img = np.zeros((64, 128, 3), dtype=np.uint8)
    img[:, :64] = (0, 0, 255)  # BGR red
    img[:, 64:] = (255, 0, 0)  # BGR blue
    path = tmp_path / "red_blue.png"
    cv2.imwrite(str(path), img)
    return path


@pytest.fixture
def tall_image(tmp_path):
    """A 64x192 PNG: taller than it is wide."""
    img = np.full((192, 64, 3), 200, dtype=np.uint8)
    path = tmp_path / "tall.png"
    cv2.imwrite(str(path), img)
    return path


def _make_canvas(ids: list[list[int]], bases: int = 1, name: str | None = None) -> Canvas:
    """Build a canvas straight from a grid of brick ids, skipping image fitting."""
    bricks = [[Brick(i, (i, i, i)) for i in row] for row in ids]
    return Canvas(bases, bricks, name=name)


@pytest.fixture
def make_canvas():
    """Factory for canvases built from a grid of brick ids."""
    return _make_canvas


@pytest.fixture
def two_base_canvas():
    """A 32x64 canvas: brick 1 on the left baseplate, brick 2 on the right."""
    row = [1] * DIM_BASE + [2] * DIM_BASE
    canvas = _make_canvas([row[:] for _ in range(DIM_BASE)], bases=2, name="two-base")
    canvas.source = "two-base"
    return canvas
