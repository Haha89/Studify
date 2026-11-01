"""Guess how far each part of a picture is, and turn that into stacks of plates.

Uses Depth Anything V2 Small (Apache-2.0), downloaded on first use: 27 MB, with its
version and checksum fixed below. This is the quantized file, which gives about the
same depth as the full 99 MB one on sample pictures, which is plenty for a grid of studs.
It needs the ``onnxruntime`` package (``uv sync --extra depth``).
"""

from __future__ import annotations

import hashlib
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

MODEL_URL = (
    "https://huggingface.co/onnx-community/depth-anything-v2-small/"
    "resolve/4472b7362082ad9968fee890ca0f1e5aca36b93d/onnx/model_int8.onnx"
)
MODEL_SHA256 = "01aa7a23de3f4a0ee1a2bb9997e6918104c85a9f95dea46d27b9b3fb0c6b9001"
# Environment variable naming where to keep the model, overriding ~/.cache/studify.
CACHE_ENV = "STUDIFY_CACHE"
DOWNLOAD_TIMEOUT_SECONDS = 60

# What the model expects: its longest side this size, both sides multiples of its patch.
INPUT_SIZE = 518
PATCH_SIZE = 14
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# Depth, from nearest 1 to furthest 0, past which each further plate is stacked. Below
# the first, a stud stays on the baseplate: the background of most pictures.
PLATE_THRESHOLDS = (0.25, 0.45, 0.65, 0.85)
MAX_PLATES = len(PLATE_THRESHOLDS)
# Share of the picture's depths ignored at each end, so one odd spot doesn't set the scale.
OUTLIERS_PERCENT = 2
MIN_DEPTH_RANGE = 1e-3  # Under this, the picture has no depth to speak of.
_KERNEL = np.ones((3, 3), dtype=np.uint8)

# The depth of the last few pictures: every style and the guide share one.
_RECENT_DEPTHS = 4

_lock = threading.Lock()
_session = None
_depths: dict[str, np.ndarray] = {}


class DepthError(RuntimeError):
    """The model can't be run. The message says what to do about it."""


def model_path() -> Path:
    """Return where the model is kept."""
    return Path(os.environ.get(CACHE_ENV) or Path.home() / ".cache" / "studify") / "depth-anything-v2-small-int8.onnx"


def _download(target: Path) -> None:
    """Download the model, keeping it only if it is exactly the expected file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DepthError(f"Could not download the depth model: {exc}") from exc
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise DepthError("The depth model that was downloaded isn't the expected file.")
    partial.write_bytes(data)
    partial.replace(target)


def _load_session():
    """Return the model, ready to run, downloading it first if need be."""
    global _session
    with _lock:
        if _session is None:
            try:
                import onnxruntime
            except ImportError as exc:
                raise DepthError("A depth relief needs onnxruntime: uv sync --extra depth") from exc
            path = model_path()
            if not path.is_file():
                _download(path)
            _session = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        return _session


def _input_shape(height: int, width: int) -> tuple[int, int]:
    """Return the (height, width) to run a picture at, keeping its proportions."""
    scale = INPUT_SIZE / max(height, width)
    return tuple(max(PATCH_SIZE, round(side * scale / PATCH_SIZE) * PATCH_SIZE) for side in (height, width))


def estimate_depth(rgb: np.ndarray) -> np.ndarray:
    """
    Return how near each pixel of a picture looks, from 0 to 1, at the picture's size.

    :param rgb: (rows, cols, 3) uint8 RGB array.
    :raises DepthError: if the model can't be run.
    """
    key = hashlib.sha256(rgb.tobytes()).hexdigest()
    if key in _depths:
        return _depths[key]
    session = _load_session()
    height, width = rgb.shape[:2]
    input_height, input_width = _input_shape(height, width)
    resized = cv2.resize(rgb, (input_width, input_height), interpolation=cv2.INTER_CUBIC)
    pixels = ((resized.astype(np.float32) / 255 - MEAN) / STD).transpose(2, 0, 1)[None]
    predicted = session.run(None, {session.get_inputs()[0].name: pixels})[0].squeeze().astype(np.float32)
    low, high = np.percentile(predicted, (OUTLIERS_PERCENT, 100 - OUTLIERS_PERCENT))
    if high - low < MIN_DEPTH_RANGE:
        near = np.zeros(predicted.shape, dtype=np.float32)
    else:
        near = np.clip((predicted - low) / (high - low), 0, 1)
    depth = cv2.resize(near, (width, height), interpolation=cv2.INTER_LINEAR)
    while len(_depths) >= _RECENT_DEPTHS:
        _depths.pop(next(iter(_depths)))
    _depths[key] = depth
    return depth


def depth_heights(depth: np.ndarray) -> np.ndarray:
    """Return how many plates to stack under each stud, from how near it looks. Each
    level sits wholly on the one below, and has nothing under 3 studs wide, which would
    make too thin a stack."""
    heights = np.zeros(depth.shape, dtype=int)
    below = np.ones(depth.shape, dtype=np.uint8)
    for threshold in PLATE_THRESHOLDS:
        level = np.uint8(depth >= threshold) & below
        level = cv2.morphologyEx(level, cv2.MORPH_OPEN, _KERNEL) & below
        heights += level
        below = level
    return heights
