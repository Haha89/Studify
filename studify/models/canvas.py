from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from studify.models import depth
from studify.models.assembly import ASSEMBLIES, DEFAULT_ASSEMBLY, PANEL_STUDS, assembly_parts
from studify.models.brick import Brick
from studify.models.bricklink import round_tile_parts
from studify.models.brickstore import BrickStore
from studify.models.plates import BORDER_SIZES, PLATE_SIZES, Plate, count_parts, pack_baseplates, plate_regions
from studify.models.pricing import DEFAULT_PALETTE, PriceEstimate, estimate_price
from studify.models.realistic import render_mosaic, render_round
from studify.models.relief import BACKGROUND, DEFAULT_RELIEF, RELIEFS, STEPPED_PLATES, Layout, lay_out, subject_levels
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
# The metallic colours of round tiles, for a touch of LEGO Art's gold leaf and platinum.
BRIGHT_GOLD, DARK_GOLD, SILVER = COLORS["Metallic Gold"], COLORS["Pearl Gold"], COLORS["Flat Silver"]
ACCENT_COLORS = ("Metallic Gold", "Pearl Gold", "Flat Silver")
# What counts as golden or silvery, on OpenCV's HSV scales (hue 0-179, the rest 0-255).
GOLD_HUES = (15, 32)
GOLD_MIN_SATURATION = 90
GOLD_MIN_VALUE = 90
BRIGHT_GOLD_MIN_VALUE = 185
SILVER_MAX_SATURATION = 30
SILVER_VALUES = (90, 210)
# Gold and silver stay touches: an area this small can turn, and they cover this share at
# most, unless an area of any size is very likely metal, going by the three values after.
ACCENT_SPOT_STUDS = 12
ACCENT_MAX_SHARE = 0.02
METAL_MAX_DISTANCE = 14  # CIELAB, from the metal's colour.
METAL_MIN_SHARE = 0.9  # Of the area, within that distance.
METAL_MIN_CONTRAST = 15  # Standard deviation of its brightness, 0-255: flat areas aren't shiny.
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
        assembly: str = DEFAULT_ASSEMBLY,
    ):
        """
        :param bases: Number of baseplates along the longest side of the canvas.
        :param bricks: Pre-built brick grid, row-major. Defaults to an empty canvas.
        :param name: Human-readable label, used as a heading in the build guide.
        :param frame: Width, in studs, of a frame around the mosaic. 0 for none.
        :param frame_color: Colour of that frame, a ``FRAME_COLORS`` key.
        :param assembly: What it is built on, from ``assembly.ASSEMBLIES``.
        """
        if assembly not in ASSEMBLIES:
            raise ValueError(f"Unknown assembly {assembly!r}; expected one of {list(ASSEMBLIES)}")
        self.assembly = assembly
        if frame_color not in FRAME_COLORS:
            raise ValueError(f"Unknown frame colour {frame_color!r}; expected one of {list(FRAME_COLORS)}")
        self.frame_color = FRAME_COLORS[frame_color]
        if bases < 1:
            raise ValueError(f"bases must be at least 1, got {bases}")
        if not 0 <= frame < DIM_BASE // 2:
            raise ValueError(f"frame must be between 0 and {DIM_BASE // 2 - 1} studs, got {frame}")
        self.frame = frame
        self.relief = DEFAULT_RELIEF
        self.painted_heights: np.ndarray | None = None
        # Subject and core grid, or plates per stud for a depth relief. Set by fit_from_image.
        self.levels: np.ndarray | None = None
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
        return count_parts(pack_baseplates(self.get_brick_ids(), sizes, tile=True)) if self.bricks else {}

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
        return pack_baseplates(self.get_brick_ids(), BORDER_SIZES, self.border_mask(), tile=True)

    def get_round_part_counts(self) -> dict[tuple[int, str], int]:
        """Return the parts of a mosaic made of round tiles, with plates around the
        picture, as {(brick id, BrickLink part number): how many}."""
        tiles = np.unique(self.get_brick_ids()[~self.border_mask()], return_counts=True)
        distribution = dict(zip(tiles[0].tolist(), tiles[1].tolist(), strict=True))
        return round_tile_parts(distribution) | count_parts(self.border_plates())

    def layout(self, sizes: Iterable[str] = PLATE_SIZES, round_tiles: bool = False) -> Layout:
        """Return which piece goes where, given the canvas's relief.

        :param sizes: Plate sizes to use.
        :param round_tiles: Top the picture with round tiles, and its border with plates
            up to 1x4.
        """
        heights = np.zeros(self.shape, dtype=int)
        tiles = np.zeros(self.shape, dtype=bool)
        if self.painted_heights is not None:
            heights = self.painted_heights
        elif self.relief == "stepped":
            heights = STEPPED_PLATES[self.levels]
        elif self.relief == "depth":
            heights = self.levels
        if round_tiles:
            tiles = ~self.border_mask()
            sizes = BORDER_SIZES
        if self.assembly == "panels":
            # Each panel is built on its own, then pinned to the next.
            return lay_out(self.get_brick_ids(), sizes, heights, tiles, PANEL_STUDS, assembly_parts(*self.shape))
        return lay_out(self.get_brick_ids(), sizes, heights, tiles)

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
        # Panels replace baseplates, and are among the parts.
        baseplates = 0 if self.assembly == "panels" else self.baseplate_count
        return estimate_price(self.get_distribution(), baseplates, baseplate_kind, currency, parts)

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
        layout = self.layout(sizes, round_tiles)
        return render_mosaic(pixels, layout.regions, layout.heights, scale)

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

    def _majority_indices(self, src: np.ndarray, colors: np.ndarray) -> np.ndarray:
        """Return, for each stud of the resized image, the index of the colour that
        covers most of it, with every source pixel first snapped to its nearest colour."""
        packed = src[..., 0].astype(np.int32) << 16 | src[..., 1].astype(np.int32) << 8 | src[..., 2]
        # Only once per distinct colour: flat graphics have few, and photos aren't the point.
        distinct, inverse = np.unique(packed, return_inverse=True)
        rgb = np.stack([distinct >> 16, distinct >> 8 & 255, distinct & 255], axis=1)
        nearest = nearest_color_indices(rgb, colors)[inverse.ravel()].reshape(packed.shape)
        used = np.unique(nearest)
        shares = [self.resize_img(np.uint8(nearest == index) * 255) for index in used]
        return used[np.argmax(np.stack(shares), axis=0)]

    def _apply_accents(self, closest: np.ndarray, resized: np.ndarray, palette: list[Brick]) -> list[Brick]:
        """Turn a few golden and silvery spots to gold and silver, and return the palette
        with them. An area counts as a spot if it is small. A larger one only turns if it
        very likely is metal: nearly all of it the colour of the metal, and with the
        contrast of a shiny surface, which a yellow patch or a grey wall doesn't have."""
        top, bottom, left, right = self.content_box
        picture = resized[top:bottom, left:right]
        hsv = cv2.cvtColor(picture, cv2.COLOR_RGB2HSV)
        hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        lab = rgb_to_lab(picture.reshape(-1, 3)).reshape(picture.shape)
        golden = (GOLD_HUES[0] <= hue) & (hue <= GOLD_HUES[1])
        golden &= (saturation >= GOLD_MIN_SATURATION) & (value >= GOLD_MIN_VALUE)
        silvery = (saturation <= SILVER_MAX_SATURATION) & (SILVER_VALUES[0] <= value) & (value <= SILVER_VALUES[1])

        spots: list[tuple[float, np.ndarray, tuple[int, int, int] | None]] = []
        for mask, metals in ((golden, (BRIGHT_GOLD, DARK_GOLD)), (silvery, (SILVER,))):
            references = rgb_to_lab(np.array(metals))
            distances = np.linalg.norm(lab[..., None, :] - references, axis=-1).min(axis=-1)
            count, labels = cv2.connectedComponents(np.uint8(mask), connectivity=8)
            for number in range(1, count):
                area = labels == number
                if area.sum() <= ACCENT_SPOT_STUDS:
                    spots.append((float(distances[area].mean()), area, metals))
                elif (distances[area] <= METAL_MAX_DISTANCE).mean() >= METAL_MIN_SHARE and value[
                    area
                ].std() >= METAL_MIN_CONTRAST:
                    spots.append((-1.0, area, metals))  # Sure enough to come first, whatever its size.

        # However many spots there are, only the likeliest stay: gold and silver stay rare.
        budget = round(picture.shape[0] * picture.shape[1] * ACCENT_MAX_SHARE)
        chosen: list[tuple[np.ndarray, tuple]] = []
        for score, area, metals in sorted(spots, key=lambda spot: spot[0]):
            if score < 0 or area.sum() <= budget:
                chosen.append((area, metals))
                if score >= 0:
                    budget -= int(area.sum())
        if not chosen:
            return palette

        palette = list(dict.fromkeys([*palette, *BrickStore.bricks_named(list(ACCENT_COLORS))]))
        indices = {brick.color: index for index, brick in enumerate(palette)}
        region = closest[top:bottom, left:right]
        for area, metals in chosen:
            if SILVER in metals:
                region[area] = indices[SILVER]
            else:
                bright = value >= BRIGHT_GOLD_MIN_VALUE
                region[area & bright] = indices[BRIGHT_GOLD]
                region[area & ~bright] = indices[DARK_GOLD]
        return palette

    def fit_from_image(
        self,
        path: str | Path,
        palette: list[Brick] | None = None,
        max_colors: int | None = None,
        dither: bool = False,
        style: Style | None = None,
        clean_strays: bool = True,
        relief: str = DEFAULT_RELIEF,
        sharp: bool = False,
        accents: bool = False,
        heights: np.ndarray | None = None,
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
        :param relief: How to give the mosaic depth, from ``relief.RELIEFS``.
        :param sharp: Give each stud the colour covering most of it, rather than the
            one nearest the average of what it covers, which turns the edge between
            two colours into a line of a third: for logos and flat graphics. Not with
            ``dither``, which blends on purpose.
        :param heights: Plates under each stud, painted by hand: these replace whatever
            the relief would have worked out, if they fit the canvas.
        :param accents: Turn a few golden and silvery spots to gold and silver (see
            ``_apply_accents``). Not with ``max_colors``, which they would go over.
        """
        if relief not in RELIEFS:
            raise ValueError(f"Unknown relief {relief!r}; expected one of {list(RELIEFS)}")
        src = read_image(path)
        if src is None:
            raise FileNotFoundError(f"Could not read image: {path}")
        self.source_path = Path(path)
        self.source = self.source_path.stem
        src = cv2.cvtColor(src, cv2.COLOR_BGR2RGB)
        original = src
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
        if sharp and not dither:
            top, bottom, left, right = self.content_box
            closest[top:bottom, left:right] = self._majority_indices(src, colors)[top:bottom, left:right]
        if dither:
            top, bottom, left, right = self.content_box
            closest[top:bottom, left:right] = dither_indices(resized[top:bottom, left:right], colors)
        elif clean_strays:
            top, bottom, left, right = self.content_box
            closest[top:bottom, left:right] = remove_stray_bricks(closest[top:bottom, left:right], colors)
        if accents and max_colors is None:
            palette = self._apply_accents(closest, resized, palette)
        if outlined:
            # Outline in the darkest colour on hand: black, unless the palette lacks it.
            top, bottom, left, right = self.content_box
            darkest = int(np.argmin(rgb_to_lab(colors)[:, 0]))
            closest[top:bottom, left:right][outline_mask(resized[top:bottom, left:right])] = darkest

        # Bricks are immutable, so the same instance can be reused across the grid.
        self.bricks = [[palette[index] for index in row] for row in closest]

        self.relief = relief
        self.levels = np.full((rows, cols), BACKGROUND, dtype=int)
        self.painted_heights = heights if heights is not None and heights.shape == (rows, cols) else None
        if relief == "depth":
            # From the untouched picture: retouching flattens what the model reads depth from.
            near = cv2.resize(depth.estimate_depth(original), (src.shape[1], src.shape[0]))
            top, bottom, left, right = self.content_box
            self.levels[top:bottom, left:right] = depth.depth_heights(self.resize_img(near)[top:bottom, left:right])
        elif relief == "stepped":
            top, bottom, left, right = self.content_box
            self.levels[top:bottom, left:right] = subject_levels(resized[top:bottom, left:right])
