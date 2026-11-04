"""Regenerate the README's before/after images and the example guide in docs/.

Run from the repository root:

    uv run python scripts/make_docs.py

The PDF step needs Chrome, Chromium or Edge (see ``pdf_creator.find_browser``).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np

from studify.models.pdf_creator import PdfCreator
from studify.models.utils import read_image
from studify.process import build_canvas

SOURCE = Path("pics/mountain-lake.jpg")
DOCS = Path("docs")
IMAGES = DOCS / "images"

# Showcase renders: 3 baseplates wide with a 1-stud frame, one per style.
SHOWCASE = {"size": 3, "frame": 1}
STYLES = ("natural", "pop", "comic")
# The example guide is kept small (2x2 baseplates) so the PDF stays light.
GUIDE = {"size": 2, "frame": 1, "style": "pop"}

# Pixels per stud in the showcase images: big enough to see each brick.
STUD_PX = 4
ORIGINAL_MAX_PX = 96 * STUD_PX


def _write_png(path: Path, rgb: np.ndarray) -> None:
    ok, png = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError(f"Could not encode {path}")
    path.write_bytes(png.tobytes())


def make_images() -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)

    original = cv2.cvtColor(read_image(SOURCE), cv2.COLOR_BGR2RGB)
    scale = ORIGINAL_MAX_PX / max(original.shape[:2])
    original = cv2.resize(original, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    _write_png(IMAGES / f"{SOURCE.stem}-original.png", original)

    for style in STYLES:
        canvas = build_canvas(path=SOURCE, style=style, **SHOWCASE)
        pixels = canvas.to_pixel_array()
        rows, cols = pixels.shape[:2]
        big = cv2.resize(pixels, (cols * STUD_PX, rows * STUD_PX), interpolation=cv2.INTER_NEAREST)
        _write_png(IMAGES / f"{SOURCE.stem}-{style}.png", big)
        print(f"{style:8} {canvas.get_price().format():>12}  {len(canvas.get_distribution())} colours")


def make_guide() -> None:
    canvas = build_canvas(path=SOURCE, **GUIDE)
    with tempfile.TemporaryDirectory() as tmp:
        pdf = PdfCreator(canvas).generate_pdf(output_dir=tmp)
        target = DOCS / f"example-guide-{SOURCE.stem}.pdf"
        shutil.copyfile(pdf, target)
    print(f"guide    {target} ({target.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    make_images()
    make_guide()
