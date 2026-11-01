from __future__ import annotations

import hashlib

import cv2
import numpy as np
import pytest

from studify.models import depth
from studify.models.depth import MAX_PLATES, depth_heights
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


@pytest.fixture
def dome(monkeypatch):
    """Stand in for the model: nearest in the middle of the picture."""

    def estimate(rgb):
        rows, cols = rgb.shape[:2]
        y, x = np.mgrid[0:rows, 0:cols].astype(np.float32)
        return 1 - np.clip(np.hypot(y - rows / 2, x - cols / 2) / (min(rows, cols) / 2), 0, 1)

    monkeypatch.setattr(depth, "estimate_depth", estimate)


class TestHeights:
    def test_far_stays_flat_and_near_is_highest(self):
        grid = np.zeros((40, 40), dtype=np.float32)
        grid[10:30, 10:30] = 1
        heights = depth_heights(grid)
        assert heights[0, 0] == 0 and heights[20, 20] == MAX_PLATES

    def test_each_level_sits_on_the_one_below(self):
        y, x = np.mgrid[0:64, 0:64]
        heights = depth_heights(1 - np.clip(np.hypot(y - 32, x - 32) / 32, 0, 1).astype(np.float32))
        assert set(np.unique(heights).tolist()) == set(range(MAX_PLATES + 1))
        for level in range(1, MAX_PLATES + 1):
            assert ((heights >= level) <= (heights >= level - 1)).all()

    def test_no_stack_is_thinner_than_3_studs(self):
        grid = np.zeros((20, 20), dtype=np.float32)
        grid[5, :] = 1  # 1 stud wide.
        grid[:, 15:17] = 1  # 2 studs wide.
        assert not depth_heights(grid).any()

    def test_a_flat_picture_has_no_relief(self):
        assert not depth_heights(np.zeros((20, 20), dtype=np.float32)).any()


class TestCanvas:
    def test_raises_the_picture_but_not_the_frame(self, picture, dome):
        canvas = build_canvas(1, picture, frame=2, relief="depth")
        layout = canvas.layout()
        assert layout.heights[16, 16] == MAX_PLATES
        assert not layout.heights[:2].any() and not layout.heights[:, -2:].any()
        assert len(layout.supports) == MAX_PLATES
        assert not layout.tiles.any()

    def test_plates_never_span_two_heights(self, picture, dome):
        layout = build_canvas(1, picture, relief="depth").layout()
        for plate in layout.plates:
            area = layout.heights[plate.row : plate.row + plate.height, plate.col : plate.col + plate.width]
            assert (area == area[0, 0]).all()

    def test_is_refused_with_round_tiles(self, picture, dome):
        with pytest.raises(ValueError):
            build_canvas(1, picture, relief="depth", round_tiles=True)

    def test_the_depth_comes_from_the_untouched_picture(self, picture, monkeypatch):
        seen = []

        def estimate(rgb):
            seen.append(rgb.tobytes())
            return np.zeros(rgb.shape[:2], dtype=np.float32)

        monkeypatch.setattr(depth, "estimate_depth", estimate)
        for style in ("natural", "pop", "comic"):
            build_canvas(1, picture, relief="depth", style=style)
        assert len(set(seen)) == 1

    def test_the_guide_has_a_map_per_hidden_layer(self, picture, dome, tmp_path):
        canvas = build_canvas(1, picture, frame=2, relief="depth")
        html = PdfCreator(canvas, lang="fr").generate_html(output_dir=tmp_path).read_text(encoding="utf-8")
        assert f"couche cachée 1 sur {MAX_PLATES}" in html and f"couche cachée {MAX_PLATES} sur {MAX_PLATES}" in html
        assert "distance estimée" in html


class TestModel:
    def test_input_keeps_proportions_in_multiples_of_the_patch(self):
        assert depth._input_shape(1000, 1000) == (518, 518)
        height, width = depth._input_shape(500, 1000)
        assert width == 518 and height % 14 == 0 and abs(height - 259) <= 7

    def test_a_download_that_isnt_the_expected_file_is_refused(self, tmp_path, monkeypatch):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"not a model"

        monkeypatch.setattr(depth.urllib.request, "urlopen", lambda *args, **kwargs: Response())
        target = tmp_path / "model.onnx"
        with pytest.raises(depth.DepthError):
            depth._download(target)
        assert not target.exists()

    def test_a_failed_download_says_so(self, tmp_path, monkeypatch):
        def fail(*args, **kwargs):
            raise depth.urllib.error.URLError("offline")

        monkeypatch.setattr(depth.urllib.request, "urlopen", fail)
        with pytest.raises(depth.DepthError, match="offline"):
            depth._download(tmp_path / "model.onnx")

    def test_the_cache_folder_can_be_chosen(self, tmp_path, monkeypatch):
        monkeypatch.setenv(depth.CACHE_ENV, str(tmp_path))
        assert depth.model_path().parent == tmp_path

    @pytest.mark.skipif(not depth.model_path().is_file(), reason="the model hasn't been downloaded")
    def test_the_real_model_finds_the_nearest_part_of_a_picture(self):
        pytest.importorskip("onnxruntime")
        assert hashlib.sha256(depth.model_path().read_bytes()).hexdigest() == depth.MODEL_SHA256
        rgb = np.full((200, 300, 3), 200, dtype=np.uint8)
        cv2.circle(rgb, (150, 100), 60, (200, 40, 40), -1)
        near = depth.estimate_depth(rgb)
        assert near.shape == (200, 300)
        assert near.min() >= 0 and near.max() <= 1
