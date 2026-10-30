# LegoMe

Turn any image into a LEGO mosaic and a printable, baseplate-by-baseplate build guide.

LegoMe downsamples your picture to whole 32x32 LEGO baseplates, snaps every pixel to the
nearest real LEGO colour, and writes an HTML guide with one stud map per baseplate, the
brick counts you need, and a cost estimate.

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync                  # core dependencies
uv sync --all-extras     # also install the optional GUI and web app
```

## Use

```bash
# A 4-baseplate-wide mosaic of the sample image
uv run legome pics/dog.png --size 4

# Write the guide elsewhere, and skip the preview window and the browser
uv run legome pics/vador.png -s 2 -o ./guides --no-plot --no-open

# A 2-stud black frame around the picture, for a finished, framed look
uv run legome pics/alex.jpg -s 3 --frame 2

# A punchy, 8-colour version: cheaper to buy, and more pop-art
uv run legome pics/dog.png -s 3 --max-colors 8

# Pop-art or comic-book retouching before the mosaic
uv run legome pics/alex.jpg -s 3 --frame 2 --style pop
uv run legome pics/dog.png -s 3 --frame 2 --style comic

# Graphical interface (needs `uv sync --all-extras`)
uv run legome --gui
```

| Flag | Meaning |
| --- | --- |
| `image` | Path to the source image. Required unless `--gui`. |
| `-s`, `--size` | Baseplates along the longest side (default `4`). |
| `-o`, `--output-dir` | Where to write the guide (default `./output`). |
| `--no-plot` | Skip the matplotlib preview. |
| `--no-open` | Do not open the guide in a browser. |
| `--frame` | Black frame this many studs wide around the mosaic (default `0`, max `15`). |
| `--colors` | Only use these LEGO colours, comma-separated, e.g. `"Black,White,Red"`. |
| `--max-colors` | Use at most N colours, chosen to best fit the image. |
| `--style` | Retouch the photo first: `natural` (default), `pop` (vivid, flat, short palette) or `comic` (black outlines). |
| `--dither` | Blend colours the palette lacks with Floyd-Steinberg dithering. |
| `--gui` | Launch the graphical interface. |

The guide lands at `<output-dir>/<image-name>.html`. Open it and print to PDF — the
stylesheet puts one baseplate on each page.

### Web app

```bash
uv sync --extra web
uv run legome-web        # then open http://127.0.0.1:8050
```

Upload a photo (or paste a link), pick the size, frame and colour limit, compare the
natural, pop and comic renders side by side, and download the chosen one as a PDF guide.
PDFs are printed by a headless Chrome, Chromium or Edge; set `LEGOME_BROWSER` to point at
one if it isn't found automatically.

### As a library

```python
from legome.models.canvas import Canvas
from legome.models.pdf_creator import PdfCreator

canvas = Canvas(4)  # 4 baseplates on the longest side
canvas.fit_from_image("pics/dog.png")

print(canvas.shape)  # (rows, cols) in studs
print(canvas.get_distribution())  # {brick_id: count}
print(canvas.get_cost())  # USD

PdfCreator(canvas).generate_html(output_dir="output")
```

## How it works

1. **Resize** — the image is scaled so its longest side is `size * 32` studs, with both
   sides rounded down to whole baseplates (`Canvas.resize_img`).
2. **Quantise** — each pixel is matched to the palette colour that *looks* closest
   (Euclidean distance in CIELAB, not raw RGB), optionally after narrowing the palette
   to the N colours that best fit the image, and optionally with Floyd-Steinberg
   dithering (`Canvas.fit_from_image`).
3. **Split** — the mosaic is cut into 32x32 sub-canvases, one per baseplate, left to right
   and top to bottom (`PdfCreator.get_bases`).
4. **Render** — each baseplate becomes an HTML table of brick ids, bordered around every
   run of same-coloured bricks so you can see the shapes while building
   (`utils.make_map`).

Costs assume $0.03 per 1x1 plate and $2.00 per 32x32 baseplate (`models/brickstore.py`).

## Layout

```
legome/
  cli.py              argparse entry point (`legome`)
  process.py          image -> mosaic -> guide, one call
  gui.py              optional customtkinter front end
  export/             Jinja2 template + print stylesheet
  models/
    brick.py          immutable Brick (palette id + RGB)
    brickstore.py     the orderable palette and its prices
    canvas.py         the brick grid: fitting, resizing, cost, plotting
    pdf_creator.py    splits a canvas into baseplates and renders the guide
    static.py         the 135 LEGO colours and DIM_BASE (32)
    utils.py          brick counting and stud-map HTML
tests/                pytest suite
pics/                 sample images
```

## Develop

```bash
uv run pytest                       # tests
uv run pytest --cov=legome          # tests with coverage
uv run ruff check .                 # lint
uv run ruff format .                # format
```

## Notes

- The palette in `static.py` has 133 entries but only 122 distinct RGB values (for example
  `Red` and `Trans-Red` are identical). By default, matching only considers
  `BrickStore.available_bricks` (81 plain, buildable colours) — trans, chrome, speckle,
  glitter, pearl, metallic and glow-in-dark finishes are excluded, since several sit
  exactly on top of a plain colour (e.g. the discontinued `Speckle Black-Copper` is pure
  `(0, 0, 0)`, same as `Black`) and would otherwise win ties against it. Pass `--full-palette`
  to also match against those finishes.
- Brick ids in the guide are indices into `static.COLORS`; the guide's final table maps
  every id to its name, RGB and a swatch.
- Colour data is cross-checked against the LDraw colour reference and BrickLink's colour
  guide, but it isn't exhaustive: common colours like `Dark Royal Blue`, `Coral`, the
  `Nougat` family and the `Neon`/`Satin Trans-` finishes aren't in the palette yet.
