from __future__ import annotations

from pathlib import Path

from legome.models.brickstore import BrickStore
from legome.models.canvas import Canvas
from legome.models.pdf_creator import PdfCreator
from legome.models.static import DIM_BASE


def process(
    size: int,
    path: str | Path,
    output_dir: Path | str | None = None,
    plot: bool = True,
    open_in_browser: bool = True,
    full_palette: bool = False,
) -> Canvas:
    """
    Turn an image into a LEGO mosaic and write its build guide.

    :param size: Number of 32x32 baseplates along the longest side.
    :param path: Path to the source image.
    :param output_dir: Directory for the build guide. Defaults to ./output.
    :param plot: Show a preview of the mosaic in a matplotlib window.
    :param open_in_browser: Open the build guide once written.
    :param full_palette: Also match trans/chrome/speckle/pearl/metallic finishes,
        instead of just plain buildable colours.
    :return: The fitted canvas.
    """
    canvas = Canvas(size)
    palette = BrickStore.all_bricks if full_palette else None
    canvas.fit_from_image(path, palette=palette)

    rows, cols = canvas.shape
    distribution = canvas.get_distribution()
    print(f"Canvas: {cols} x {rows} studs ({cols // DIM_BASE} x {rows // DIM_BASE} baseplates)")
    print(f"Colors used: {len(distribution)}")
    print(f"Total elements: {sum(distribution.values())}")
    print(f"Total estimated cost: {canvas.get_cost():.2f} USD")

    guide = PdfCreator(canvas).generate_html(output_dir=output_dir, open_in_browser=open_in_browser)
    print(f"Build guide: {guide}")

    if plot:
        canvas.plot_canvas()
    return canvas
