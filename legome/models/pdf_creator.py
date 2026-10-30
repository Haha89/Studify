from __future__ import annotations

import base64
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import pikepdf
from jinja2 import Environment, FileSystemLoader

from legome.models.canvas import Canvas
from legome.models.pricing import BASEPLATE_LABELS, SPARE_RATE, color_tier
from legome.models.static import COLORS, DIM_BASE
from legome.models.utils import make_map

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "export"
OUTPUT_DIR_NAME = "output"
STYLESHEET = "style.css"

# Environment variable naming a Chromium-based browser to print PDFs with,
# overriding the search below.
BROWSER_ENV = "LEGOME_BROWSER"
_BROWSER_COMMANDS = ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge", "microsoft-edge")
# Install locations not on PATH, relative to Program Files / LocalAppData (Windows).
_WINDOWS_BROWSERS = (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe")
_MAC_BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
PDF_TIMEOUT_SECONDS = 180


def find_browser() -> str | None:
    """Return the path of a Chromium-based browser (Chrome, Chromium or Edge)
    able to print a guide to PDF, or None if there isn't one."""
    if override := os.environ.get(BROWSER_ENV):
        return override
    for command in _BROWSER_COMMANDS:
        if found := shutil.which(command):
            return found
    if sys.platform == "win32":
        roots = [os.environ.get(var) for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        candidates = [Path(root) / exe for root in roots if root for exe in _WINDOWS_BROWSERS]
    else:
        candidates = [Path(path) for path in _MAC_BROWSERS]
    return next((str(path) for path in candidates if path.is_file()), None)


def html_to_pdf(html_path: Path, pdf_path: Path) -> Path:
    """Print an HTML file to PDF with a headless Chromium-based browser, which
    honours the guide's print stylesheet (A4, one baseplate per page).

    :raises RuntimeError: if no suitable browser is installed, or printing fails.
    """
    browser = find_browser()
    if browser is None:
        raise RuntimeError(f"Printing a PDF needs Chrome, Chromium or Edge; none was found (or set {BROWSER_ENV}).")
    pdf_path = Path(pdf_path).resolve()
    # A throwaway profile, so a browser the user already has open isn't reused or locked.
    with tempfile.TemporaryDirectory(prefix="legome-browser-") as profile:
        result = subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-pdf-header-footer",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={pdf_path}",
                Path(html_path).resolve().as_uri(),
            ],
            capture_output=True,
            timeout=PDF_TIMEOUT_SECONDS,
        )
    if result.returncode != 0 or not pdf_path.is_file():
        raise RuntimeError(f"PDF printing failed: {result.stderr.decode(errors='replace').strip()[-500:]}")
    compact_pdf(pdf_path)
    return pdf_path


def compact_pdf(pdf_path: Path) -> None:
    """Rewrite a PDF in place with its objects packed into compressed object
    streams. Chrome's tagged PDFs hold one structure object per stud-map cell,
    stored loose: packing them shrinks a guide about fourfold, content unchanged."""
    pdf_path = Path(pdf_path)
    packed = pdf_path.with_suffix(".packed.pdf")
    with pikepdf.open(pdf_path) as pdf:
        pdf.save(packed, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    packed.replace(pdf_path)


def _data_uri_from_file(path: Path) -> str:
    """Encode a file on disk as a ``data:`` URI, so the guide can embed it without a
    second file alongside the HTML."""
    mime, _ = mimetypes.guess_type(path.name)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime or 'application/octet-stream'};base64,{encoded}"


def data_uri_from_rgb_array(rgb: np.ndarray) -> str:
    """Encode an (rows, cols, 3) uint8 RGB array as a PNG ``data:`` URI."""
    ok, png = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError("Failed to encode the mosaic preview as PNG")
    return f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"


class PdfCreator:
    """Renders a canvas into a printable, per-baseplate build guide."""

    def __init__(self, canvas: Canvas, baseplates: str = "official", currency: str = "EUR"):
        """
        :param canvas: The fitted mosaic.
        :param baseplates: Price "official" LEGO or "compatible" baseplates.
        :param currency: "EUR" or "USD", for the price estimate.
        """
        self.canvas = canvas
        self.baseplates = baseplates
        self.currency = currency

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
            {
                "id": local_ids[brick_id],
                "name": names[brick_id],
                "rgb": COLORS[names[brick_id]],
                "count": count,
                "tier": color_tier(brick_id),
            }
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
            mosaic_image=data_uri_from_rgb_array(self.canvas.to_preview_image()) if self.canvas.bricks else None,
            mosaic_size=f"{cols} x {rows} studs",
            total_colors=len(distribution),
            total_bricks=sum(distribution.values()),
            price=self.canvas.get_price(self.baseplates, self.currency),
            baseplate_label=BASEPLATE_LABELS[self.baseplates],
            spare_percent=round(SPARE_RATE * 100),
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

    def generate_pdf(self, output_dir: Path | str | None = None) -> Path:
        """
        Write the build guide as ``<output_dir>/<source>.pdf`` (the HTML guide is
        written alongside it, see ``generate_html``).

        :param output_dir: Directory for the guide. Defaults to ./output. Created if missing.
        :return: Path to the written PDF.
        :raises RuntimeError: if no Chromium-based browser is available to print it.
        """
        html = self.generate_html(output_dir=output_dir)
        return html_to_pdf(html, html.with_suffix(".pdf"))
