from __future__ import annotations

import argparse
from pathlib import Path

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
    parser.add_argument(
        "--full-palette",
        action="store_true",
        help="Also match trans/chrome/speckle/pearl/metallic finishes, not just plain buildable colors.",
    )
    parser.add_argument("--gui", action="store_true", help="Launch the graphical interface instead.")
    args = parser.parse_args(argv)

    if args.gui:
        from legome.gui import run_gui

        run_gui()
        return 0

    if args.image is None:
        parser.error("the following arguments are required: image (or pass --gui)")

    process(
        size=args.size,
        path=args.image,
        output_dir=args.output_dir,
        plot=not args.no_plot,
        open_in_browser=not args.no_open,
        full_palette=args.full_palette,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
