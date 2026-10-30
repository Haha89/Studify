from __future__ import annotations

from pathlib import Path

from legome.models.brickstore import BrickStore
from legome.models.canvas import Canvas
from legome.models.pdf_creator import PdfCreator
from legome.models.pricing import DEFAULT_PALETTE
from legome.models.static import DIM_BASE
from legome.models.style import STYLES


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
) -> Canvas:
    """
    Turn an image into a fitted LEGO mosaic, without writing anything.

    :param size: Number of 32x32 baseplates along the longest side.
    :param path: Path to the source image.
    :param full_palette: Also match trans/chrome/speckle/pearl/metallic finishes,
        instead of just plain buildable colours.
    :param frame: Width, in studs, of a black frame around the mosaic. 0 for none.
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
    :return: The fitted canvas.
    """
    canvas = Canvas(size, frame=frame)
    preset = STYLES[style]
    if colors is None and not full_palette and preset.colors:
        colors = list(preset.colors)
    if colors:
        bricks = BrickStore.bricks_named(colors)
    elif full_palette:
        bricks = BrickStore.all_bricks
    else:
        bricks = BrickStore.palette(palette)
    canvas.fit_from_image(path, palette=bricks, max_colors=max_colors, dither=dither, style=preset)
    return canvas


def process(
    size: int,
    path: str | Path,
    output_dir: Path | str | None = None,
    plot: bool = True,
    open_in_browser: bool = True,
    baseplates: str = "official",
    currency: str = "EUR",
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
    :param options: Mosaic options passed on to ``build_canvas`` (full_palette,
        frame, colors, max_colors, dither, style).
    :return: The fitted canvas.
    """
    canvas = build_canvas(size, path, **options)

    rows, cols = canvas.shape
    distribution = canvas.get_distribution()
    print(f"Canvas: {cols} x {rows} studs ({cols // DIM_BASE} x {rows // DIM_BASE} baseplates)")
    print(f"Colors used: {len(distribution)}")
    print(f"Total elements: {sum(distribution.values())}")
    price = canvas.get_price(baseplates, currency)
    print(
        f"Total estimated cost: {price.format()} "
        f"(bricks {price.format(price.bricks_low)}-{price.format(price.bricks_high)} incl. spares, "
        f"{price.baseplate_count} {baseplates} baseplates {price.format(price.baseplates)}; shipping not included)"
    )
    if price.rare_colors:
        print(f"Rare colours, hard to buy in bulk: {', '.join(f'{n} x{c}' for n, c in price.rare_colors.items())}")

    guide = PdfCreator(canvas, baseplates=baseplates, currency=currency).generate_html(
        output_dir=output_dir, open_in_browser=open_in_browser
    )
    print(f"Build guide: {guide}")

    if plot:
        canvas.plot_canvas()
    return canvas
