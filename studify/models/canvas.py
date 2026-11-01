from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from studify.models.brick import Brick
from studify.models.bricklink import round_tile_parts
from studify.models.brickstore import BrickStore
from studify.models.plates import BORDER_SIZES, PLATE_SIZES, Plate, count_parts, pack_baseplates, plate_regions
from studify.models.pricing import DEFAULT_PALETTE, PriceEstimate, estimate_price
from studify.models.realistic import render_round, render_tiles
from studify.models.static import COLORS, DIM_BASE
from studify.models.style import Style, outline_mask, retouch
from studify.models.utils import (
    dither_indices,
    get_distribution,
    make_map,
    nearest_color_indices,
    read_image,
    reduce_palette,
    remove_stray_bricks,
    rgb_to_lab,
)

# Colour used to pad a resized image out to whole baseplates (see resize_img).
LETTERBOX_COLOR = (0, 0, 0)
# Colours of the optional frame around the mosaic: exact LEGO colours, so they
# survive quantisation unchanged.
FRAME_COLORS = {"black": COLORS["Black"], "white": COLORS["White"]}
DEFAULT_FRAME_COLOR = "black"
OUTLINE_COLOR = COLORS["Black"]
# Cap on a preview image's longer side, so a very large mosaic doesn't embed an
# equally large PNG in the build guide.
PREVIEW_MAX_DIMENSION = 1200


class Canvas:
    """A grid of 1x1 bricks laid over one or more 32x32 LEGO baseplates."""

    def __init__(
        self,
        bases: int,
        bricks: list[list[Brick]] | None = None,
        name: str | None = None,
        frame: int = 0,
        frame_color: str = DEFAULT_FRAME_COLOR,
    ):
        """
        :param bases: Number of baseplates along the longest side of the canvas.
        :param bricks: Pre-built brick grid, row-major. Defaults to an empty canvas.
        :param name: Human-readable label, used as a heading in the build guide.
        :param frame: Width, in studs, of a frame around the mosaic. 0 for none.
        :param frame_color: Colour of that frame, a ``FRAME_COLORS`` key.
        """
        if frame_color not in FRAME_COLORS:
            raise ValueError(f"Unknown frame colour {frame_color!r}; expected one of {list(FRAME_COLORS)}")
        self.frame_color = FRAME_COLORS[frame_color]
        if bases < 1:
            raise ValueError(f"bases must be at least 1, got {bases}")
        if not 0 <= frame < DIM_BASE // 2:
            raise ValueError(f"frame must be between 0 and {DIM_BASE // 2 - 1} studs, got {frame}")
        self.frame = frame
        # (top, bottom, left, right) of the picture itself within the last resized
        # image, excluding letterbox bars and frame. Set by resize_img.
        self.content_box: tuple[int, int, int, int] | None = None
        self.size = bases * DIM_BASE  # Longest side, in studs.
        self.bricks: list[list[Brick]] = bricks if bricks is not None else []
        self.store = BrickStore()
        self.source = ""
        self.source_path: Path | None = None
        self.name = name

    def __repr__(self) -> str:
        return f"Canvas L{self.size} - Distrib: {self.get_distribution()}"

    @property
    def shape(self) -> tuple[int, int]:
        """Return the canvas size as (rows, columns) in studs."""
        if not self.bricks:
            return 0, 0
        return len(self.bricks), len(self.bricks[0])

    def get_distribution(self) -> dict[int, int]:
        """Return a mapping of brick id to how many of that brick the canvas needs."""
        return get_distribution(self.bricks)

    def get_brick_ids(self) -> np.ndarray:
        """Return the canvas as a 2D array of brick ids."""
        return np.array([[brick.id for brick in row] for row in self.bricks], dtype=int)

    def get_part_counts(self, sizes: Iterable[str] = PLATE_SIZES) -> dict[tuple[int, str], int]:
        """Return the parts needed with these plate sizes, as {(brick id, BrickLink
        part number): how many} (see ``plates.pack_plates``)."""
        return count_parts(pack_baseplates(self.get_brick_ids(), sizes)) if self.bricks else {}

    def border_mask(self) -> np.ndarray:
        """Return a boolean grid of the studs around the picture: its frame and any
        letterbox bars. A canvas not fitted from an image has none."""
        if self.content_box is None:
            return np.zeros(self.shape, dtype=bool)
        top, bottom, left, right = self.content_box
        mask = np.ones(self.shape, dtype=bool)
        mask[top:bottom, left:right] = False
        return mask

    def border_plates(self) -> list[Plate]:
        """Return the plates covering the border of a mosaic made of round tiles."""
        return pack_baseplates(self.get_brick_ids(), BORDER_SIZES, self.border_mask())

    def get_round_part_counts(self) -> dict[tuple[int, str], int]:
        """Return the parts of a mosaic made of round tiles, with plates around the
        picture, as {(brick id, BrickLink part number): how many}."""
        tiles = np.unique(self.get_brick_ids()[~self.border_mask()], return_counts=True)
        distribution = dict(zip(tiles[0].tolist(), tiles[1].tolist(), strict=True))
        return round_tile_parts(distribution) | count_parts(self.border_plates())

    @property
    def baseplate_count(self) -> int:
        """Number of 32x32 baseplates the canvas covers (a partial one counts whole)."""
        rows, cols = self.shape
        return -(-rows // DIM_BASE) * -(-cols // DIM_BASE)  # Ceiling divisions.

    def get_price(
        self,
        baseplate_kind: str = "official",
        currency: str = "EUR",
        parts: dict[tuple[int, str], int] | None = None,
    ) -> PriceEstimate:
        """Return the estimated price range of the build: bricks by colour rarity,
        spares and baseplates (see ``pricing.estimate_price``)."""
        return estimate_price(self.get_distribution(), self.baseplate_count, baseplate_kind, currency, parts)

    def to_pixel_array(self) -> np.ndarray:
        """Return the canvas as an (rows, cols, 3) uint8 RGB array, one pixel per brick:
        the mosaic as it will actually look once built."""
        if not self.bricks:
            raise ValueError("Canvas is empty; call fit_from_image() first")
        return np.array([[brick.color for brick in row] for row in self.bricks], dtype=np.uint8)

    def to_preview_image(self, round_tiles: bool = False) -> np.ndarray:
        """Return the mosaic as an (rows x scale, cols x scale, 3) uint8 RGB array,
        scaled up and ruled with a line at every baseplate boundary, for a build
        guide's "reconstructed" preview.

        :param round_tiles: Draw a round tile per stud, rather than a flat square.
        """
        pixels = self.to_pixel_array()
        rows, cols = pixels.shape[:2]
        scale = max(1, PREVIEW_MAX_DIMENSION // max(rows, cols))
        if round_tiles:
            preview = render_round(pixels, scale, plate_regions(self.shape, self.border_plates()))
        else:
            preview = cv2.resize(pixels, (cols * scale, rows * scale), interpolation=cv2.INTER_NEAREST)

        line_px = max(1, scale // 4)
        for y in range(0, rows + 1, DIM_BASE):
            y_px = min(y * scale, preview.shape[0] - 1)
            preview[max(0, y_px - line_px // 2) : y_px + line_px - line_px // 2, :] = LETTERBOX_COLOR
        for x in range(0, cols + 1, DIM_BASE):
            x_px = min(x * scale, preview.shape[1] - 1)
            preview[:, max(0, x_px - line_px // 2) : x_px + line_px - line_px // 2] = LETTERBOX_COLOR
        return preview

    def to_realistic_image(self, sizes: Iterable[str] = PLATE_SIZES, round_tiles: bool = False) -> np.ndarray:
        """Return the mosaic drawn as it looks built, for a build guide's overview:
        with its shading and the outline of each piece, no larger than a preview.

        :param sizes: The plates it is built from. Drawn as flat tiles (see ``realistic``).
        :param round_tiles: It is built from round tiles and border plates instead.
        """
        pixels = self.to_pixel_array()
        scale = max(1, PREVIEW_MAX_DIMENSION // max(pixels.shape[:2]))
        if round_tiles:
            return render_round(pixels, scale, plate_regions(self.shape, self.border_plates()))
        return render_tiles(pixels, plate_regions(self.shape, pack_baseplates(self.get_brick_ids(), sizes)), scale)

    def plot_canvas(self) -> None:
        """Show the canvas as an image, one pixel per brick."""
        self.plot(self.to_pixel_array() / 255)

    def to_html(self) -> str:
        """Return the canvas as an HTML table of brick ids."""
        return make_map(pd.DataFrame(self.get_brick_ids()))

    @staticmethod
    def plot(pixels: np.ndarray) -> None:
        """
        Display the canvas using matplotlib.

        :param pixels: NumPy array representing the pixel values of the canvas.
        """
        plt.figure()
        plt.clf()
        plt.axis("off")
        plt.title("Canvas")
        plt.imshow(pixels)
        plt.show()

    def resize_img(self, src: np.ndarray) -> np.ndarray:
        """Scale an image so its longest side is exactly ``self.size`` studs, with
        the short side resized in true proportion (no distortion), then letterbox
        that short side with black up to the next whole baseplate.

        With a frame, the image is instead centre-cropped to fill the area inside
        the frame exactly, so the frame is the same width on all four sides."""
        if self.frame:
            return self._resize_framed(src)
        height, width = src.shape[:2]
        if width < height:
            height_resized = self.size
            width_resized = max(1, round(width / height * self.size))
        else:
            width_resized = self.size
            height_resized = max(1, round(height / width * self.size))
        resized = cv2.resize(src, (width_resized, height_resized), interpolation=cv2.INTER_AREA)
        padded = self._letterbox(resized)
        top = (padded.shape[0] - height_resized) // 2
        left = (padded.shape[1] - width_resized) // 2
        self.content_box = (top, top + height_resized, left, left + width_resized)
        return padded

    def _resize_framed(self, src: np.ndarray) -> np.ndarray:
        """Centre-crop `src` to the aspect ratio of the area inside the frame,
        resize it into that area, then surround it with the frame."""
        height, width = src.shape[:2]
        bases_long = self.size // DIM_BASE
        bases_short = max(1, round(min(height, width) / max(height, width) * bases_long))
        if width < height:
            canvas_height, canvas_width = bases_long * DIM_BASE, bases_short * DIM_BASE
        else:
            canvas_height, canvas_width = bases_short * DIM_BASE, bases_long * DIM_BASE
        inner_height = canvas_height - 2 * self.frame
        inner_width = canvas_width - 2 * self.frame

        # Crop the excess off whichever side is too long for the inner aspect ratio.
        if width * inner_height > height * inner_width:
            crop_width = max(1, round(height * inner_width / inner_height))
            left = (width - crop_width) // 2
            src = src[:, left : left + crop_width]
        else:
            crop_height = max(1, round(width * inner_height / inner_width))
            top = (height - crop_height) // 2
            src = src[top : top + crop_height]

        inner = cv2.resize(src, (inner_width, inner_height), interpolation=cv2.INTER_AREA)
        self.content_box = (self.frame, self.frame + inner_height, self.frame, self.frame + inner_width)
        return cv2.copyMakeBorder(inner, *[self.frame] * 4, cv2.BORDER_CONSTANT, value=self.frame_color)

    @staticmethod
    def _letterbox(img: np.ndarray) -> np.ndarray:
        """Centre `img` on a black canvas padded out to whole baseplates on both
        sides, so the true aspect ratio survives instead of being squashed to fit."""
        height, width = img.shape[:2]
        padded_height = -(-height // DIM_BASE) * DIM_BASE  # Ceiling division.
        padded_width = -(-width // DIM_BASE) * DIM_BASE
        top = (padded_height - height) // 2
        left = (padded_width - width) // 2
        return cv2.copyMakeBorder(
            img,
            top,
            padded_height - height - top,
            left,
            padded_width - width - left,
            cv2.BORDER_CONSTANT,
            value=LETTERBOX_COLOR,
        )

    def fit_from_image(
        self,
        path: str | Path,
        palette: list[Brick] | None = None,
        max_colors: int | None = None,
        dither: bool = False,
        style: Style | None = None,
        clean_strays: bool = True,
    ) -> None:
        """
        Fill the canvas from an image, mapping every pixel to its nearest brick
        colour by CIELAB distance.

        :param path: Path to the image file.
        :param palette: Bricks to match against. Defaults to the buildable colours
            that are easy to buy (``BrickStore.palette(DEFAULT_PALETTE)``: no rare
            ones); pass ``BrickStore.available_bricks`` for every plain colour, or
            ``BrickStore.all_bricks`` to also allow trans/chrome/pearl/... finishes.
        :param max_colors: Limit the build to the this many colours that best fit
            the image (see ``utils.reduce_palette``). None for no limit.
        :param dither: Blend colours the palette lacks with Floyd-Steinberg
            dithering instead of flat nearest-colour regions. The frame and the
            letterbox bars are never dithered.
        :param style: Retouch the picture first (see ``style.STYLES``). Its
            ``colors`` are ignored here; pick the palette with ``palette``.
        :param clean_strays: Merge isolated bricks into their surroundings (see
            ``utils.remove_stray_bricks``). Not with ``dither``, whose scattered
            bricks are the point.
        """
        src = read_image(path)
        if src is None:
            raise FileNotFoundError(f"Could not read image: {path}")
        self.source_path = Path(path)
        self.source = self.source_path.stem
        src = cv2.cvtColor(src, cv2.COLOR_BGR2RGB)
        if style is not None:
            src = retouch(src, style)
        resized = self.resize_img(src)
        outlined = style is not None and style.outline

        palette = palette if palette is not None else self.store.palette(DEFAULT_PALETTE)
        if max_colors is not None:
            # A frame or outline must keep its colour, however few colours are allowed.
            needed = ([self.frame_color] if self.frame else []) + ([OUTLINE_COLOR] if outlined else [])
            keep = tuple(i for i, brick in enumerate(palette) if brick.color in needed)
            kept = reduce_palette(resized, np.array([brick.color for brick in palette]), max_colors, keep)
            palette = [palette[i] for i in kept]
        colors = np.array([brick.color for brick in palette])

        rows, cols = resized.shape[:2]
        closest = nearest_color_indices(resized.reshape(-1, 3), colors).reshape(rows, cols)
        if dither:
            top, bottom, left, right = self.content_box
            closest[top:bottom, left:right] = dither_indices(resized[top:bottom, left:right], colors)
        elif clean_strays:
            top, bottom, left, right = self.content_box
            closest[top:bottom, left:right] = remove_stray_bricks(closest[top:bottom, left:right], colors)
        if outlined:
            # Outline in the darkest colour on hand: black, unless the palette lacks it.
            top, bottom, left, right = self.content_box
            darkest = int(np.argmin(rgb_to_lab(colors)[:, 0]))
            closest[top:bottom, left:right][outline_mask(resized[top:bottom, left:right])] = darkest

        # Bricks are immutable, so the same instance can be reused across the grid.
        self.bricks = [[palette[index] for index in row] for row in closest]
