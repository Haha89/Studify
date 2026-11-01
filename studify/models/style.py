"""Photo retouching applied before a picture is turned into bricks.

A style is a small recipe: punchier colours, flatter regions, comic-book
outlines and/or a restricted palette. Flat, saturated areas suit LEGO far better
than photographic texture, which otherwise turns into a scatter of odd bricks.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# Longest side the source is shrunk to before retouching. Filters are slow on
# multi-megapixel photos, and the mosaic is at most a few hundred studs anyway.
WORKING_MAX_DIMENSION = 800


@dataclass(frozen=True)
class Style:
    """How to retouch a picture before matching it to bricks."""

    # Multiplier on HSV saturation; 1.0 leaves colours unchanged.
    saturation: float = 1.0
    # CLAHE clip limit on lightness, for local contrast; 0 to skip.
    contrast: float = 0.0
    # Edge-preserving smoothing passes, turning texture into flat areas; 0 to skip.
    flatten: int = 0
    # Draw a dark one-stud outline along strong edges, comic-book style.
    outline: bool = False
    # Default LEGO colour names for this style; None keeps the usual palette.
    colors: tuple[str, ...] | None = None


STYLES: dict[str, Style] = {
    "natural": Style(),
    # Warhol-like: loud, flat colour from a short, vivid palette.
    "pop": Style(
        saturation=1.6,
        contrast=2.5,
        flatten=3,
        colors=(
            "Black",
            "White",
            "Red",
            "Yellow",
            "Blue",
            "Dark Pink",
            "Bright Pink",
            "Orange",
            "Bright Green",
            "Medium Azure",
            "Purple",
            "Lime",
        ),
    ),
    # Lichtenstein-like: vivid flat colour held together by black outlines.
    "comic": Style(saturation=1.4, contrast=2.0, flatten=3, outline=True),
}


def retouch(img: np.ndarray, style: Style) -> np.ndarray:
    """Return an (H, W, 3) RGB uint8 image with the style's colour and
    smoothing applied; the image may come back smaller (see
    ``WORKING_MAX_DIMENSION``), never larger."""
    height, width = img.shape[:2]
    scale = WORKING_MAX_DIMENSION / max(height, width)
    if scale < 1:
        img = cv2.resize(img, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)

    for _ in range(style.flatten):
        img = cv2.bilateralFilter(img, d=9, sigmaColor=60, sigmaSpace=9)

    if style.contrast:
        lab = cv2.cvtColor(img, cv2.COLOR_RGB2Lab)
        clahe = cv2.createCLAHE(clipLimit=style.contrast, tileGridSize=(8, 8))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        img = cv2.cvtColor(lab, cv2.COLOR_Lab2RGB)

    if style.saturation != 1.0:
        hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV).astype(np.float32)
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * style.saturation, 0, 255)
        img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)
    return img


def outline_mask(img: np.ndarray, low: int = 90, high: int = 180, min_length: int = 6) -> np.ndarray:
    """Return an (H, W) bool mask of the strong edges in a stud-resolution RGB
    image: one stud wide, where a comic outline should go. Edge fragments
    shorter than ``min_length`` studs are dropped: at stud scale they read as
    stray dark bricks (wrinkles, hair strands), not as lines."""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    edges = (cv2.Canny(gray, low, high) > 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(edges, connectivity=8)
    long_enough = stats[:, cv2.CC_STAT_AREA] >= min_length
    long_enough[0] = False  # Label 0 is the background.
    return long_enough[labels]
