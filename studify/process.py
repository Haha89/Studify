from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from studify.models.assembly import DEFAULT_ASSEMBLY
from studify.models.bricklink import (
    ROUND_TILE,
    TILE_1X1,
    comes_in,
    unavailable_parts,
    unlisted_colors,
    wanted_list_xml,
)
from studify.models.brickstore import BrickStore
from studify.models.canvas import DEFAULT_FRAME_COLOR, Canvas
from studify.models.pdf_creator import PdfCreator
from studify.models.plates import PLATE_SIZES
from studify.models.pricing import DEFAULT_PALETTE
from studify.models.relief import DEFAULT_RELIEF
from studify.models.static import DIM_BASE
from studify.models.style import STYLES


def build_canvas(
    size: int,
    path: str | Path,
    full_palette: bool = False,
    frame: int = 0,
    colors: list[str] | None = None,
    max_colors: int | None = None,
    dither: bool = False,
    style: str = "natural",
    palette: str = DEFAULT_PALETTE,
    clean_strays: bool = True,
    round_tiles: bool = False,
    frame_color: str = DEFAULT_FRAME_COLOR,
    relief: str = DEFAULT_RELIEF,
    assembly: str = DEFAULT_ASSEMBLY,
    sharp: bool = False,
) -> Canvas:
    """
    Turn an image into a fitted LEGO mosaic, without writing anything.

    :param size: Number of 32x32 baseplates along the longest side.
    :param path: Path to the source image.
    :param full_palette: Also match trans/chrome/speckle/pearl/metallic finishes,
        instead of just plain buildable colours.
    :param frame: Width, in studs, of a frame around the mosaic. 0 for none.
    :param frame_color: Colour of that frame: "black" or "white".
    :param colors: Only use these LEGO colour names (e.g. the ones you already
        own). Overrides ``full_palette``.
    :param max_colors: Use at most this many colours, chosen to best fit the image.
    :param dither: Blend colours the palette lacks with Floyd-Steinberg dithering.
    :param style: Retouch preset from ``style.STYLES`` (natural, pop, comic). A
        style's own palette applies unless ``colors`` or ``full_palette`` is given.
    :param palette: Which colours to draw from, by how easy they are to buy:
        "buyable" (no rare colours, the default), "all" or "common" (see
        ``pricing.PALETTE_TIERS``). Ignored when ``colors``, ``full_palette`` or
        the style's own palette applies.
    :param clean_strays: Merge isolated bricks into their surroundings.
    :param round_tiles: Build from round 1x1 tiles, so only draw from the colours they
        come in. Colours given by hand in ``colors`` are still used as they are.
    :param relief: How to give the mosaic depth, from ``relief.RELIEFS``. A stepped
        relief is built from plates, so not with ``round_tiles``.
    :param assembly: What it is built on, from ``assembly.ASSEMBLIES``.
    :param sharp: Keep edges between colours sharp, for logos and flat graphics (see
        ``Canvas.fit_from_image``). Not with ``dither``.
    :raises ValueError: asking for both round tiles and a stepped relief.
    :return: The fitted canvas.
    """
    if round_tiles and relief != DEFAULT_RELIEF:
        raise ValueError("A relief is built from plates, so can't use round tiles")
    canvas = Canvas(size, frame=frame, frame_color=frame_color, assembly=assembly)
    preset = STYLES[style]
    part = ROUND_TILE if round_tiles else TILE_1X1
    if colors:
        bricks = BrickStore.bricks_named(colors)
    elif full_palette:
        bricks = BrickStore.all_bricks
    elif preset.colors:
        bricks = [brick for brick in BrickStore.bricks_named(list(preset.colors)) if comes_in(brick.id, part)]
    else:
        bricks = BrickStore.palette(palette, part)
    canvas.fit_from_image(
        path,
        palette=bricks,
        max_colors=max_colors,
        dither=dither,
        style=preset,
        clean_strays=clean_strays,
        relief=relief,
        sharp=sharp,
    )
    return canvas


def process(
    size: int,
    path: str | Path,
    output_dir: Path | str | None = None,
    plot: bool = True,
    open_in_browser: bool = True,
    baseplates: str = "official",
    currency: str = "EUR",
    bricklink: bool = False,
    tinted: bool = True,
    plates: Iterable[str] = PLATE_SIZES,
    lang: str = "en",
    round_tiles: bool = False,
    **options,
) -> Canvas:
    """
    Turn an image into a LEGO mosaic and write its build guide.

    :param size: Number of 32x32 baseplates along the longest side.
    :param path: Path to the source image.
    :param output_dir: Directory for the build guide. Defaults to ./output.
    :param plot: Show a preview of the mosaic in a matplotlib window.
    :param open_in_browser: Open the build guide once written.
    :param baseplates: Price "official" LEGO or "compatible" baseplates.
    :param currency: "EUR" or "USD", for the price estimate.
    :param bricklink: Also write a BrickLink wanted list beside the guide.
    :param tinted: Tint the guide's stud maps with each brick's colour.
    :param plates: Plate sizes for the guide and the wanted list, from ``PLATE_SIZES``.
    :param lang: Language of the guide: "en" or "fr".
    :param round_tiles: Build the picture from round 1x1 tiles, one per stud, and its
        border from plates up to 1x4, instead of ``plates``.
    :param options: Mosaic options passed on to ``build_canvas`` (full_palette,
        frame, frame_color, colors, max_colors, dither, style, palette, clean_strays, relief, assembly, sharp).
    :return: The fitted canvas.
    """
    canvas = build_canvas(size, path, round_tiles=round_tiles, **options)

    rows, cols = canvas.shape
    distribution = canvas.get_distribution()
    print(f"Canvas: {cols} x {rows} studs ({cols // DIM_BASE} x {rows // DIM_BASE} baseplates)")
    print(f"Colors used: {len(distribution)}")
    print(f"Total elements: {sum(distribution.values())}")
    parts = canvas.layout(plates, round_tiles).parts(canvas.get_brick_ids())
    price = canvas.get_price(baseplates, currency, parts)
    print(
        f"Total estimated cost: {price.format()} "
        f"(bricks {price.format(price.bricks_low)}-{price.format(price.bricks_high)} incl. spares, "
        f"{price.baseplate_count} {baseplates} baseplates {price.format(price.baseplates)}; shipping not included)"
    )
    if price.rare_colors:
        print(f"Rare colours, hard to buy in bulk: {', '.join(f'{n} x{c}' for n, c in price.rare_colors.items())}")

    guide = PdfCreator(
        canvas,
        baseplates=baseplates,
        currency=currency,
        tinted=tinted,
        plates=plates,
        lang=lang,
        round_tiles=round_tiles,
    ).generate_html(output_dir=output_dir, open_in_browser=open_in_browser)
    print(f"{'Pieces' if round_tiles else 'Tiles'} to place: {sum(parts.values())}")
    if missing := unavailable_parts(parts):
        print(f"Not sold in every size needed, per BrickLink: {', '.join(missing)}")
    print(f"Build guide: {guide}")

    if bricklink:
        wanted_list = guide.with_name(f"{guide.stem}-bricklink.xml")
        wanted_list.write_text(wanted_list_xml(distribution, parts), encoding="utf-8")
        print(f"BrickLink wanted list: {wanted_list}")
        if unlisted := unlisted_colors(distribution):
            print(f"Not in the wanted list, add them by hand: {', '.join(unlisted)}")

    if plot:
        canvas.plot_canvas()
    return canvas
