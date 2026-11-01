from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader

from studify.export.labels import LABELS
from studify.models.assembly import LARGEST_OFFICIAL_STUDS, assembly_parts, panel_grid, pin_joins
from studify.models.canvas import Canvas
from studify.models.plates import ONLY_1X1, PLATE_SIZES, Plate, plate_regions, plate_sizes
from studify.models.pricing import SPARE_RATE, color_tier
from studify.models.relief import Layout
from studify.models.static import COLORS, DIM_BASE
from studify.models.utils import fit_within, make_map, read_image, rgb_to_lab

# Lightness (CIELAB, 0-100) above which a tinted stud shows its number in black.
DARK_TEXT_ABOVE_LIGHTNESS = 55

# The original picture in the guide's overview only fills half an A4 page.
ORIGINAL_MAX_DIMENSION = 1200
ORIGINAL_JPEG_QUALITY = 85

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "export"
OUTPUT_DIR_NAME = "output"
STYLESHEET = "style.css"

# Environment variable naming a Chromium-based browser to print PDFs with,
# overriding the search below.
BROWSER_ENV = "STUDIFY_BROWSER"
_BROWSER_COMMANDS = ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge", "microsoft-edge")
# Install locations not on PATH, relative to Program Files / LocalAppData (Windows).
_WINDOWS_BROWSERS = (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe")
_MAC_BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
PDF_TIMEOUT_SECONDS = 180
# How a hidden plate's studs are marked on a support map: not a colour's number.
SUPPORT_CELL = -1


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
    with tempfile.TemporaryDirectory(prefix="studify-browser-") as profile:
        try:
            result = subprocess.run(
                [
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-pdf-header-footer",
                    # Tags would add a structure object per stud-map cell, doubling the print time.
                    "--disable-pdf-tagging",
                    f"--user-data-dir={profile}",
                    f"--print-to-pdf={pdf_path}",
                    Path(html_path).resolve().as_uri(),
                ],
                capture_output=True,
                timeout=PDF_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"PDF printing timed out after {PDF_TIMEOUT_SECONDS} seconds.") from exc
        except OSError as exc:
            raise RuntimeError(f"Could not start the browser {browser} to print the PDF: {exc}") from exc
    if result.returncode != 0 or not pdf_path.is_file():
        raise RuntimeError(f"PDF printing failed: {result.stderr.decode(errors='replace').strip()[-500:]}")
    return pdf_path


def _original_data_uri(path: Path) -> str | None:
    """Encode the source image as a small JPEG ``data:`` URI, so the guide can embed
    it without a second file alongside the HTML, and without a phone photo's full
    resolution, which would outweigh the rest of the guide many times over. None if
    the file can no longer be read."""
    img = read_image(path)
    if img is None:
        return None
    ok, jpeg = cv2.imencode(
        ".jpg", fit_within(img, ORIGINAL_MAX_DIMENSION), [cv2.IMWRITE_JPEG_QUALITY, ORIGINAL_JPEG_QUALITY]
    )
    if not ok:
        raise RuntimeError("Failed to encode the original image as JPEG")
    return f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode('ascii')}"


def _text_color(rgb: tuple[int, int, int]) -> str:
    """The colour that stays readable on a stud of this colour: black or white."""
    return "#000" if rgb_to_lab(np.array([rgb]))[0, 0] > DARK_TEXT_ABOVE_LIGHTNESS else "#fff"


def data_uri_from_rgb_array(rgb: np.ndarray) -> str:
    """Encode an (rows, cols, 3) uint8 RGB array as a PNG ``data:`` URI."""
    ok, png = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError("Failed to encode the mosaic preview as PNG")
    return f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"


def _plates_on_base(plates: list[Plate], row: int, col: int) -> list[Plate]:
    """Return the plates on one baseplate, with rows and columns relative to it."""
    return [
        Plate(plate.row - row * DIM_BASE, plate.col - col * DIM_BASE, plate.height, plate.width, plate.brick_id)
        for plate in plates
        if plate.row // DIM_BASE == row and plate.col // DIM_BASE == col
    ]


class PdfCreator:
    """Renders a canvas into a printable, per-baseplate build guide."""

    def __init__(
        self,
        canvas: Canvas,
        baseplates: str = "official",
        currency: str = "EUR",
        tinted: bool = True,
        plates: Iterable[str] = PLATE_SIZES,
        lang: str = "en",
        round_tiles: bool = False,
    ):
        """
        :param canvas: The fitted mosaic.
        :param baseplates: Price "official" LEGO or "compatible" baseplates.
        :param currency: "EUR" or "USD", for the price estimate.
        :param tinted: Tint each stud in a stud map with its brick's colour, so a
            builder can find a colour at a glance. Off saves ink.
        :param plates: The plate sizes to lay the guide out with, from ``PLATE_SIZES``
            (see ``plates.pack_plates``). Just 1x1 gives one plate per stud.
        :param lang: Language of the guide, a ``LABELS`` key.
        :param round_tiles: The picture is built from round 1x1 tiles, one per stud, and
            its border from plates up to 1x4, so ``plates`` doesn't apply.
        """
        self.tinted = tinted
        self.round_tiles = round_tiles
        self.plates = ONLY_1X1 if self.round_tiles else plate_sizes(plates)
        self.labels = LABELS[lang]
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
        if not self.canvas.bricks:
            return []
        columns = self.canvas.shape[1] // DIM_BASE
        layout = self.canvas.layout(self.plates, self.round_tiles)
        one_per_stud = self.plates == ONLY_1X1 and not self.round_tiles and self.canvas.relief == "flat"
        bases = []
        for number, c in enumerate(self.get_bases()):
            distribution = {local_ids[brick_id]: count for brick_id, count in c.get_distribution().items()}
            ids = c.get_brick_ids()
            row, col = divmod(number, columns)
            name = self.labels["base"].format(source=self.canvas.source, row=row + 1, col=col + 1)
            sizes: dict[int, dict[str, int]] = {}
            tiles = layout.tiles[row * DIM_BASE : (row + 1) * DIM_BASE, col * DIM_BASE : (col + 1) * DIM_BASE]
            for brick_id, count in zip(*np.unique(ids[tiles], return_counts=True), strict=True):
                sizes.setdefault(local_ids[int(brick_id)], {})[self.labels["round_size"]] = int(count)
            plates = None if one_per_stud else _plates_on_base(layout.plates, row, col)
            for plate in plates or []:
                by_size = sizes.setdefault(local_ids[plate.brick_id], {})
                by_size[plate.size] = by_size.get(plate.size, 0) + 1
            bases.append(
                {
                    "name": name,
                    "supports": self._support_maps(layout, row, col, name),
                    "table": make_map(
                        pd.DataFrame(to_local(ids)), plate_regions(ids.shape, plates) if plates else None
                    ),
                    "distribution": dict(sorted(distribution.items())),
                    "plates": {brick: dict(sorted(by_size.items(), reverse=True)) for brick, by_size in sizes.items()},
                }
            )
        return bases

    def _support_maps(self, layout: Layout, row: int, col: int, name: str) -> list[dict[str, object]]:
        """Return a baseplate's hidden layers, the lowest first: where each plate goes,
        and how many of each size."""
        maps = []
        for number, layer in enumerate(layout.supports, start=1):
            plates = _plates_on_base(layer, row, col)
            if not plates:
                continue
            grid = np.zeros((DIM_BASE, DIM_BASE), dtype=int)
            for plate in plates:
                grid[plate.row : plate.row + plate.height, plate.col : plate.col + plate.width] = SUPPORT_CELL
            sizes = Counter(plate.size for plate in plates)
            maps.append(
                {
                    "name": self.labels["support_layer"].format(base=name, layer=number, total=len(layout.supports)),
                    "table": make_map(
                        pd.DataFrame(grid),
                        plate_regions(grid.shape, plates),
                        {0: "", SUPPORT_CELL: self.labels["support_mark"]},
                    ),
                    "sizes": dict(sorted(sizes.items(), reverse=True)),
                }
            )
        return maps

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
                "text": _text_color(COLORS[names[brick_id]]),
                "count": count,
                "tier": color_tier(brick_id),
            }
            for brick_id, count in sorted(self.canvas.get_distribution().items())
        ]

    def _assembly(self) -> dict[str, object]:
        """Return what the guide says about holding the mosaic together."""
        rows, cols = self.canvas.shape
        if self.canvas.assembly != "panels" or not self.canvas.bricks:
            return {"panels": False}
        names = self.labels["assembly_parts"]
        down, across = panel_grid(rows, cols)
        return {
            "panels": True,
            "down": down,
            "across": across,
            "joins": pin_joins(rows, cols),
            "too_large": max(rows, cols) > LARGEST_OFFICIAL_STUDS,
            "parts": [
                {"part": part, "name": names[part], "count": count}
                for (_, part), count in assembly_parts(rows, cols).items()
            ],
        }

    def _round_part_totals(self) -> dict[str, int]:
        """Return how many round tiles and border plates the mosaic takes."""
        layout = self.canvas.layout(self.plates, self.round_tiles)
        return {"tiles": int(layout.tiles.sum()), "border": len(layout.plates)}

    def _relief_part_totals(self) -> dict[str, int] | None:
        """Return how many round tiles and hidden plates a relief takes, if it has one."""
        if self.canvas.relief == "flat":
            return None
        layout = self.canvas.layout(self.plates, self.round_tiles)
        return {"tiles": int(layout.tiles.sum()), "supports": layout.support_count}

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
        layout = self.canvas.layout(self.plates, self.round_tiles) if self.canvas.bricks else None
        parts = layout.parts(self.canvas.get_brick_ids()) if layout else {}
        price = self.canvas.get_price(self.baseplates, self.currency, parts)
        t = self.labels
        html_out = template.render(
            title=f"Studify: {self.canvas.source}",
            original_image=_original_data_uri(self.canvas.source_path) if self.canvas.source_path else None,
            mosaic_image=(
                data_uri_from_rgb_array(self.canvas.to_realistic_image(self.plates, self.round_tiles))
                if self.canvas.bricks
                else None
            ),
            round_tiles=self.round_tiles,
            round_parts=self._round_part_totals() if self.round_tiles else None,
            relief=self.canvas.relief,
            assembly=self._assembly(),
            relief_parts=self._relief_part_totals(),
            mosaic_size=f"{cols} x {rows} {t['studs']}",
            total_colors=len(distribution),
            total_bricks=sum(distribution.values()),
            total_plates=len(layout.plates) if layout and self.plates != ONLY_1X1 else None,
            price=price,
            t=t,
            spares_note=t["spares"].format(percent=round(SPARE_RATE * 100)),
            baseplates_note=t["baseplates_note"].format(
                count=price.baseplate_count, kind=t[self.baseplates], price=price.format(price.baseplates)
            ),
            bases=self.get_bases_maps_and_repartition(),
            colors=colors,
            tinted=self.tinted,
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
