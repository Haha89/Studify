from __future__ import annotations

import argparse
from pathlib import Path

from legome.models.brickstore import BrickStore
from legome.models.static import DIM_BASE
from legome.process import process


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="legome",
        description="Turn an image into a LEGO mosaic build guide.",
    )
    parser.add_argument("image", type=Path, nargs="?", help="Path to the source image.")
    parser.add_argument(
        "-s",
        "--size",
        type=int,
        default=4,
        help="Number of 32x32 baseplates along the longest side (default: %(default)s).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write the build guide (default: ./output).",
    )
    parser.add_argument("--no-plot", action="store_true", help="Skip the matplotlib preview.")
    parser.add_argument("--no-open", action="store_true", help="Do not open the guide in a browser.")
    palette = parser.add_mutually_exclusive_group()
    palette.add_argument(
        "--full-palette",
        action="store_true",
        help="Also match trans/chrome/speckle/pearl/metallic finishes, not just plain buildable colors.",
    )
    palette.add_argument(
        "--colors",
        type=lambda value: [name.strip() for name in value.split(",") if name.strip()],
        metavar="NAMES",
        help='Only use these LEGO colors, comma-separated (e.g. "Black,White,Red,Yellow").',
    )
    parser.add_argument(
        "--max-colors",
        type=int,
        default=None,
        metavar="N",
        help="Use at most N colors, chosen to best fit the image (default: no limit).",
    )
    parser.add_argument(
        "--dither",
        action="store_true",
        help="Blend colors the palette lacks with Floyd-Steinberg dithering.",
    )
    parser.add_argument(
        "--frame",
        type=int,
        default=0,
        metavar="STUDS",
        help="Add a black frame this many studs wide around the mosaic (default: no frame).",
    )
    parser.add_argument("--gui", action="store_true", help="Launch the graphical interface instead.")
    args = parser.parse_args(argv)

    if args.gui:
        from legome.gui import run_gui

        run_gui()
        return 0

    if args.image is None:
        parser.error("the following arguments are required: image (or pass --gui)")
    if not 0 <= args.frame < DIM_BASE // 2:
        parser.error(f"--frame must be between 0 and {DIM_BASE // 2 - 1} studs")
    if args.max_colors is not None and args.max_colors < 1:
        parser.error("--max-colors must be at least 1")
    if args.colors:
        try:
            BrickStore.bricks_named(args.colors)
        except ValueError as exc:
            parser.error(str(exc))

    process(
        size=args.size,
        path=args.image,
        output_dir=args.output_dir,
        plot=not args.no_plot,
        open_in_browser=not args.no_open,
        full_palette=args.full_palette,
        frame=args.frame,
        colors=args.colors,
        max_colors=args.max_colors,
        dither=args.dither,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
