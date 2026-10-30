from __future__ import annotations

import base64
import mimetypes
import shutil
import webbrowser
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader

from legome.models.canvas import Canvas
from legome.models.static import COLORS, DIM_BASE
from legome.models.utils import make_map

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "export"
OUTPUT_DIR_NAME = "output"
STYLESHEET = "style.css"


def _data_uri_from_file(path: Path) -> str:
    """Encode a file on disk as a ``data:`` URI, so the guide can embed it without a
    second file alongside the HTML."""
    mime, _ = mimetypes.guess_type(path.name)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime or 'application/octet-stream'};base64,{encoded}"


def _data_uri_from_rgb_array(rgb: np.ndarray) -> str:
    """Encode an (rows, cols, 3) uint8 RGB array as a PNG ``data:`` URI."""
    ok, png = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError("Failed to encode the mosaic preview as PNG")
    return f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"


class PdfCreator:
    """Renders a canvas into a printable, per-baseplate build guide."""

    def __init__(self, canvas: Canvas):
        self.canvas = canvas

    def get_bases(self) -> list[Canvas]:
        """Split the canvas into one sub-canvas per 32x32 baseplate, left to right,
        top to bottom."""
        bricks = self.canvas.bricks
        rows, cols = self.canvas.shape
        bases = []
        for r in range(rows // DIM_BASE):
            for c in range(cols // DIM_BASE):
                tile = [row[DIM_BASE * c : DIM_BASE * (c + 1)] for row in bricks[DIM_BASE * r : DIM_BASE * (r + 1)]]
                bases.append(Canvas(1, tile, name=f"{self.canvas.source} - Base Row{r + 1}:Col{c + 1}"))
        return bases

    def get_local_ids(self) -> dict[int, int]:
        """Map every brick id used on the canvas to a compact 1..N id for this
        guide, in palette-id order. A build that only needs 40 colours is numbered
        1-40, rather than showing sparse, arbitrary-looking palette ids like
        ``#124``. Computed once across the whole canvas, so a given number means
        the same colour on every baseplate in the guide."""
        return {brick_id: local_id for local_id, brick_id in enumerate(sorted(self.canvas.get_distribution()), start=1)}

    def get_bases_maps_and_repartition(self) -> list[dict[str, object]]:
        """Return the per-baseplate name, stud map and brick counts, numbered with
        this guide's compact ids (see ``get_local_ids``)."""
        local_ids = self.get_local_ids()
        to_local = np.vectorize(local_ids.get)
        bases = []
        for c in self.get_bases():
            distribution = {local_ids[brick_id]: count for brick_id, count in c.get_distribution().items()}
            bases.append(
                {
                    "name": c.name,
                    "table": make_map(pd.DataFrame(to_local(c.get_brick_ids()))),
                    "distribution": dict(sorted(distribution.items())),
                }
            )
        return bases

    def get_used_colors(self) -> list[dict[str, object]]:
        """Return the colours actually present on the canvas, ordered by this
        guide's compact local id (see ``get_local_ids``), each with its name, RGB
        and brick count -- not the entire palette."""
        names = list(COLORS)
        local_ids = self.get_local_ids()
        return [
            {"id": local_ids[brick_id], "name": names[brick_id], "rgb": COLORS[names[brick_id]], "count": count}
            for brick_id, count in sorted(self.canvas.get_distribution().items())
        ]

    def generate_html(self, output_dir: Path | str | None = None, open_in_browser: bool = False) -> Path:
        """
        Write the build guide to ``<output_dir>/<source>.html``.

        :param output_dir: Directory for the guide. Defaults to ./output. Created if missing.
        :param open_in_browser: Open the finished guide in the default browser.
        :return: Path to the written file.
        """
        env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
        template = env.get_template("template.html")
        rows, cols = self.canvas.shape
        distribution = self.canvas.get_distribution()
        colors = self.get_used_colors()
        html_out = template.render(
            title=f"LegoMe: {self.canvas.source}",
            original_image=_data_uri_from_file(self.canvas.source_path) if self.canvas.source_path else None,
            mosaic_image=_data_uri_from_rgb_array(self.canvas.to_preview_image()) if self.canvas.bricks else None,
            mosaic_size=f"{cols} x {rows} studs",
            total_colors=len(distribution),
            total_bricks=sum(distribution.values()),
            total_cost=self.canvas.get_cost(),
            bases=self.get_bases_maps_and_repartition(),
            colors=colors,
            color_by_id={color["id"]: color for color in colors},
        )

        output_dir = Path(output_dir) if output_dir is not None else Path.cwd() / OUTPUT_DIR_NAME
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = output_dir / f"{self.canvas.source or 'canvas'}.html"
        filename.write_text(html_out, encoding="utf-8")
        # The template links the stylesheet relatively, so it has to sit beside the guide.
        shutil.copyfile(TEMPLATE_DIR / STYLESHEET, output_dir / STYLESHEET)

        if open_in_browser:
            webbrowser.open(filename.resolve().as_uri(), new=2)
        return filename
