from __future__ import annotations

import argparse
from pathlib import Path

from studify.export.labels import LABELS
from studify.models.brickstore import BrickStore
from studify.models.plates import PLATE_SIZES, plate_sizes
from studify.models.static import DIM_BASE
from studify.models.style import STYLES
from studify.process import process


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="studify",
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
        "--palette",
        choices=["buyable", "all", "common"],
        default="buyable",
        help="Colours to draw from: buyable (default: no rare, pricey colours), all, or common only.",
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
        "--style",
        choices=list(STYLES),
        default="natural",
        help="Retouch the photo first: pop (vivid, flat, short palette) or comic (black outlines). "
        "Default: %(default)s.",
    )
    parser.add_argument(
        "--dither",
        action="store_true",
        help="Blend colors the palette lacks with Floyd-Steinberg dithering.",
    )
    parser.add_argument(
        "--keep-strays",
        action="store_true",
        help="Keep isolated bricks instead of merging them into their surroundings.",
    )
    parser.add_argument(
        "--plain-maps",
        action="store_true",
        help="Leave stud maps untinted, to save ink when printing the guide.",
    )
    parser.add_argument(
        "--plates",
        type=lambda value: value.split(","),
        default=list(PLATE_SIZES),
        metavar="SIZES",
        help=f"Plate sizes to use, comma-separated, from {', '.join(PLATE_SIZES)} (default: all). "
        "1x1 is always included; --plates 1x1 gives one plate per stud.",
    )
    parser.add_argument(
        "--round-tiles",
        action="store_true",
        help="Build the picture from round 1x1 tiles, one per stud, in the colours they come in, "
        "and its border from plates up to 1x4. Overrides --plates.",
    )
    parser.add_argument(
        "--relief",
        choices=["flat", "stepped"],
        default="flat",
        help="Depth: flat (default) or stepped (subject raised on hidden plates, its core "
        "higher still). Stepped is built from plates, so not with --round-tiles.",
    )
    parser.add_argument(
        "--assembly",
        choices=["baseplates", "panels"],
        default="baseplates",
        help="What to build on: baseplates (default), which need fixing to a rigid board, or the "
        "pinned 16x16 panels and brick frame of the LEGO Art sets.",
    )
    parser.add_argument(
        "--lang",
        choices=list(LABELS),
        default="en",
        help="Language of the build guide (default: %(default)s).",
    )
    parser.add_argument(
        "--bricklink",
        action="store_true",
        help="Also write a BrickLink wanted list (XML) beside the guide.",
    )
    parser.add_argument(
        "--frame",
        type=int,
        default=0,
        metavar="STUDS",
        help="Add a frame this many studs wide around the mosaic (default: no frame).",
    )
    parser.add_argument(
        "--frame-color",
        choices=["black", "white"],
        default="black",
        help="Colour of the frame (default: %(default)s).",
    )
    parser.add_argument(
        "--baseplates",
        choices=["official", "compatible"],
        default="official",
        help="Price official LEGO baseplates or cheaper compatible ones (default: %(default)s).",
    )
    parser.add_argument(
        "--currency",
        choices=["EUR", "USD"],
        default="EUR",
        help="Currency of the price estimate (default: %(default)s).",
    )
    parser.add_argument("--gui", action="store_true", help="Launch the graphical interface instead.")
    args = parser.parse_args(argv)

    if args.gui:
        from studify.gui import run_gui

        run_gui()
        return 0

    if args.image is None:
        parser.error("the following arguments are required: image (or pass --gui)")
    if args.round_tiles and args.relief != "flat":
        parser.error("--relief stepped is built from plates, so can't be used with --round-tiles")
    if args.size < 1:
        parser.error("--size must be at least 1")
    if not 0 <= args.frame < DIM_BASE // 2:
        parser.error(f"--frame must be between 0 and {DIM_BASE // 2 - 1} studs")
    if args.max_colors is not None and args.max_colors < 1:
        parser.error("--max-colors must be at least 1")
    try:
        plate_sizes(args.plates)
    except ValueError as exc:
        parser.error(str(exc))
    if args.colors:
        try:
            BrickStore.bricks_named(args.colors)
        except ValueError as exc:
            parser.error(str(exc))

    try:
        process(
            size=args.size,
            path=args.image,
            output_dir=args.output_dir,
            plot=not args.no_plot,
            open_in_browser=not args.no_open,
            full_palette=args.full_palette,
            frame=args.frame,
            frame_color=args.frame_color,
            colors=args.colors,
            max_colors=args.max_colors,
            dither=args.dither,
            style=args.style,
            palette=args.palette,
            baseplates=args.baseplates,
            currency=args.currency,
            bricklink=args.bricklink,
            tinted=not args.plain_maps,
            plates=args.plates,
            round_tiles=args.round_tiles,
            relief=args.relief,
            assembly=args.assembly,
            lang=args.lang,
            clean_strays=not args.keep_strays,
        )
    except FileNotFoundError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
