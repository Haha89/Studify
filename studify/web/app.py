"""Studify web app: pick a photo, compare the styles, download the PDF guide.

Run it with ``uv run studify-web`` (needs ``uv sync --extra web``), then open
http://127.0.0.1:8050.
"""

from __future__ import annotations

import argparse
import time

import dash_mantine_components as dmc
import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, Patch, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
from flask import Response, abort, jsonify, send_file

from studify.models.plates import ONLY_1X1, PLATE_SIZES
from studify.models.static import DIM_BASE
from studify.web import service

SIZES = range(1, 7)  # Baseplates along the longest side.
FRAMES = range(0, 5)  # Frame width in studs.
FRAME_COLOR_CHOICES = [("Noir", "black"), ("Blanc", "white")]
MODE_CHOICES = [("Plat", "flat"), ("Tuiles rondes", "round"), ("Relief", "relief"), ("Profondeur", "depth")]
ASSEMBLY_CHOICES = [("Baseplates", "baseplates"), ("Panneaux LEGO Art", "panels")]
COLOR_LIMITS = [("Toutes", "0"), ("16", "16"), ("12", "12"), ("8", "8"), ("6", "6")]
BASEPLATE_CHOICES = [("Officielles LEGO", "official"), ("Compatibles", "compatible")]
CURRENCY_CHOICES = [("€", "EUR"), ("$", "USD")]
PALETTE_CHOICES = [("Sans couleurs rares", "buyable"), ("Toutes", "all"), ("Courantes seulement", "common")]
STUD_MM = 8  # Distance between two LEGO studs.
# Crop aspect ratios offered (width / height); "free" lets the box be any shape.
CROP_RATIOS = [
    ("Libre", "free"),
    ("Carré", "1"),
    ("4:3", "1.3333"),
    ("3:4", "0.75"),
    ("3:2", "1.5"),
    ("2:3", "0.6667"),
]
CROP_SHAPE_STYLE = {"line": {"color": "#c8102e", "width": 3}, "fillcolor": "rgba(200, 16, 46, 0.12)"}

# Mantine colour scale built around LEGO red (#c8102e, shade 6: the primary shade).
LEGO_RED = [
    "#ffe9ec",
    "#fdd0d6",
    "#f59fab",
    "#ee6b7e",
    "#e84058",
    "#e42540",
    "#c8102e",
    "#b00d28",
    "#9c0a22",
    "#87061c",
]
FONT = "Inter, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
THEME = {
    "primaryColor": "lego",
    "colors": {"lego": LEGO_RED},
    "defaultRadius": "md",
    "fontFamily": FONT,
    "headings": {"fontFamily": FONT},
}

HIDDEN = {"display": "none"}
BRICKLINK_UPLOAD_URL = "https://www.bricklink.com/v2/wanted/upload.page"


def _step(number: int, title: str, subtitle: str, *children) -> dmc.Paper:
    return dmc.Paper(
        [
            dmc.Group(
                [
                    dmc.ThemeIcon(str(number), radius="xl", size="lg", variant="filled"),
                    dmc.Stack([dmc.Title(title, order=3), dmc.Text(subtitle, c="dimmed", size="sm")], gap=0),
                ],
                gap="md",
                mb="lg",
                wrap="nowrap",
            ),
            *children,
        ],
        withBorder=True,
        shadow="sm",
        radius="lg",
        p="xl",
        mb="lg",
    )


def _error(message: str) -> dmc.Alert:
    return dmc.Alert(message, color="red", variant="light", radius="md")


def _layout() -> dmc.MantineProvider:
    hero = dmc.Stack(
        [
            dmc.Group(
                [dmc.Title("Studify", order=1, c="lego", fz=44), dmc.Badge("beta", variant="light", size="lg")],
                justify="center",
                gap="sm",
            ),
            dmc.Text(
                "Transforme ta photo en mosaïque LEGO, avec son guide de montage baseplate par baseplate.",
                c="dimmed",
                ta="center",
                size="lg",
            ),
        ],
        gap=4,
        mb="xl",
    )

    source = _step(
        1,
        "Choisis une image",
        "Une photo de famille, ton animal, un portrait… PNG, JPEG ou WebP, 15 Mo max.",
        dmc.SimpleGrid(
            [
                dcc.Upload(
                    id="upload",
                    accept="image/*",
                    className="dropzone",
                    children=dmc.Stack(
                        [
                            dmc.Text("Glisse ton image ici", fw=600),
                            dmc.Text("ou clique pour parcourir tes fichiers", c="dimmed", size="sm"),
                        ],
                        gap=2,
                        align="center",
                    ),
                ),
                dmc.Stack(
                    [
                        dmc.TextInput(
                            id="url", label="Ou colle un lien vers une image", placeholder="https://…", debounce=True
                        ),
                        dmc.Button("Charger le lien", id="load-url", variant="light"),
                    ],
                    justify="center",
                    gap="sm",
                ),
            ],
            cols={"base": 1, "sm": 2},
            spacing="lg",
        ),
        html.Div(id="source-status", className="status"),
        html.Div(
            [
                dmc.Divider(my="lg"),
                dmc.Group(
                    [
                        dmc.Stack(
                            [
                                dmc.Text("Rogner l'image (facultatif)", fw=600, size="sm"),
                                dmc.Text(
                                    "Trace un rectangle sur la photo pour ne garder que cette zone. "
                                    "Tu peux ensuite le déplacer ou l'agrandir.",
                                    c="dimmed",
                                    size="xs",
                                ),
                            ],
                            gap=0,
                        ),
                        dmc.Group(
                            [
                                dmc.SegmentedControl(
                                    id="crop-ratio",
                                    data=[{"label": label, "value": value} for label, value in CROP_RATIOS],
                                    value="free",
                                    radius="xl",
                                    size="xs",
                                ),
                                dmc.Button("Réinitialiser", id="crop-reset", variant="subtle", size="xs"),
                            ],
                            gap="xs",
                        ),
                    ],
                    justify="space-between",
                    align="flex-end",
                    mb="sm",
                ),
                dcc.Graph(
                    id="crop-graph",
                    config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
                    style={"height": "420px"},
                ),
                dmc.Text(id="crop-status", c="dimmed", size="xs", ta="center", mt=4),
            ],
            id="crop-area",
            style=HIDDEN,
        ),
    )

    settings = _step(
        2,
        "Règle la mosaïque",
        "La taille fixe le prix : chaque baseplate fait 32 × 32 tenons, soit 25,6 cm de côté.",
        dmc.Stack(
            [
                dmc.Stack(
                    [
                        dmc.Text(id="size-label", fw=600, size="sm"),
                        dmc.Slider(
                            id="size",
                            min=SIZES[0],
                            max=SIZES[-1],
                            step=1,
                            value=3,
                            marks=[{"value": n, "label": str(n)} for n in SIZES],
                            restrictToMarks=True,
                            mb="md",
                        ),
                    ],
                    gap=6,
                ),
                dmc.Stack(
                    [
                        dmc.Text("Cadre autour de l'image", fw=600, size="sm"),
                        dmc.Slider(
                            id="frame",
                            min=FRAMES[0],
                            max=FRAMES[-1],
                            step=1,
                            value=2,
                            marks=[{"value": n, "label": "aucun" if n == 0 else f"{n} tenon(s)"} for n in FRAMES],
                            restrictToMarks=True,
                            mb="md",
                        ),
                        dmc.SegmentedControl(
                            id="frame-color",
                            data=[{"label": label, "value": value} for label, value in FRAME_COLOR_CHOICES],
                            value="black",
                            radius="xl",
                            style={"alignSelf": "flex-start"},
                        ),
                    ],
                    gap=6,
                ),
                dmc.Stack(
                    [
                        dmc.Text("Nombre de couleurs maximum", fw=600, size="sm"),
                        dmc.Text(
                            "Moins de couleurs : plus simple à commander, et un rendu plus graphique.",
                            c="dimmed",
                            size="xs",
                        ),
                        dmc.SegmentedControl(
                            id="max-colors",
                            data=[{"label": label, "value": value} for label, value in COLOR_LIMITS],
                            value="0",
                            radius="xl",
                        ),
                    ],
                    gap=6,
                    align="flex-start",
                ),
                dmc.Stack(
                    [
                        dmc.Text("Palette de couleurs", fw=600, size="sm"),
                        dmc.Text(
                            "Les couleurs rares (anciens gris et bruns, pastels…) sont chères et dures à trouver "
                            "en quantité. Les écarter change peu le rendu et baisse nettement le prix. "
                            "Le style Pop art garde sa propre palette.",
                            c="dimmed",
                            size="xs",
                        ),
                        dmc.SegmentedControl(
                            id="palette",
                            data=[{"label": label, "value": value} for label, value in PALETTE_CHOICES],
                            value="buyable",
                            radius="xl",
                        ),
                    ],
                    gap=6,
                    align="flex-start",
                ),
                dmc.Stack(
                    [
                        dmc.Switch(
                            id="dither",
                            label="Tramage",
                            description="Mélange les couleurs pour simuler celles qui manquent. Plus lent à calculer.",
                        ),
                        dmc.Switch(
                            id="sharp",
                            label="Contours nets",
                            description="Pour les logos et les aplats : chaque tenon prend la couleur qui le couvre le "
                            "plus, sans teinte intermédiaire sur les bords. À éviter sur les photos. Sans effet avec "
                            "le tramage.",
                        ),
                        dmc.CheckboxGroup(
                            dmc.Group(
                                [
                                    dmc.Checkbox(label=size, value=size, disabled=size in ONLY_1X1)
                                    for size in PLATE_SIZES
                                ],
                                mt="xs",
                            ),
                            id="plates",
                            label="Tailles de tuiles",
                            description="Moins de pièces à poser avec les grandes tuiles, dans le guide et la liste "
                            "BrickLink, et un prix estimé plus bas. La 1x1 reste nécessaire pour les tenons isolés.",
                            value=list(PLATE_SIZES),
                        ),
                        dmc.Stack(
                            [
                                dmc.Text("Mode", fw=600, size="sm"),
                                dmc.Text(
                                    "Plat : uniquement des tuiles lisses carrées et rectangulaires, à même hauteur. "
                                    "Tuiles rondes : une tuile ronde 1x1 par tenon pour l'image, façon LEGO Art, et "
                                    "le cadre en tuiles lisses 1x4 ; plus de pièces, un prix estimé plus haut, et la "
                                    "baseplate se voit entre les tuiles, alors choisis-la sombre. Relief : "
                                    "uniquement des tuiles lisses carrées et rectangulaires, le sujet surélevé sur des "
                                    "plaques noires cachées, son centre plus haut encore, avec une carte par couche "
                                    "dans le guide. Le sujet est détecté automatiquement et peut être imprécis ; le "
                                    "relief n'est pas encore testé avec de vraies briques. "
                                    "Profondeur : comme Relief, mais un modèle "
                                    "estime la distance de chaque zone, jusqu'à 4 plaques cachées ; il se "
                                    "télécharge à la première utilisation (27 Mo).",
                                    c="dimmed",
                                    size="xs",
                                ),
                                dmc.SegmentedControl(
                                    id="mode",
                                    data=[{"label": label, "value": value} for label, value in MODE_CHOICES],
                                    value="flat",
                                    radius="xl",
                                    style={"alignSelf": "flex-start"},
                                ),
                            ],
                            gap=6,
                        ),
                        dmc.Stack(
                            [
                                dmc.Text("Support", fw=600, size="sm"),
                                dmc.Text(
                                    "Baseplates : moins cher, mais elles ne se relient pas entre elles, il faut les "
                                    "fixer sur un panneau rigide. Panneaux LEGO Art : les panneaux 16 x 16 à goujons "
                                    "et le cadre en briques des sets officiels, à accrocher tels quels jusqu'à environ "
                                    "1 m ; plus cher, toutes les pièces sont dans la liste. Non testé en vrai.",
                                    c="dimmed",
                                    size="xs",
                                ),
                                dmc.SegmentedControl(
                                    id="assembly",
                                    data=[{"label": label, "value": value} for label, value in ASSEMBLY_CHOICES],
                                    value="baseplates",
                                    radius="xl",
                                    style={"alignSelf": "flex-start"},
                                ),
                            ],
                            gap=6,
                        ),
                        dmc.Switch(
                            id="tinted",
                            label="Cartes en couleur",
                            description="Teinte chaque tenon dans le guide. Désactive-le pour économiser l'encre.",
                            checked=True,
                        ),
                    ],
                    gap="sm",
                ),
                dmc.Group(
                    [
                        dmc.Stack(
                            [
                                dmc.Text("Baseplates", fw=600, size="sm"),
                                dmc.Text(
                                    "Officielles LEGO ≈ 9 € pièce, compatibles ≈ 2 €.",
                                    c="dimmed",
                                    size="xs",
                                ),
                                dmc.SegmentedControl(
                                    id="baseplates",
                                    data=[{"label": label, "value": value} for label, value in BASEPLATE_CHOICES],
                                    value="official",
                                    radius="xl",
                                ),
                            ],
                            gap=6,
                        ),
                        dmc.Stack(
                            [
                                dmc.Text("Devise", fw=600, size="sm"),
                                dmc.Text("Pour l'estimation du prix.", c="dimmed", size="xs"),
                                dmc.SegmentedControl(
                                    id="currency",
                                    data=[{"label": label, "value": value} for label, value in CURRENCY_CHOICES],
                                    value="EUR",
                                    radius="xl",
                                ),
                            ],
                            gap=6,
                        ),
                    ],
                    gap="xl",
                    align="flex-start",
                ),
                dmc.Group(dmc.Button("Générer les styles", id="generate", size="md", disabled=True), mt="sm"),
            ],
            gap="lg",
        ),
    )

    styles = _step(
        3,
        "Choisis ton style",
        "Clique sur l'aperçu qui te plaît. Les lignes marquent les baseplates.",
        html.Div(id="styles"),
    )

    download = _step(
        4,
        "Télécharge le guide",
        "Un PDF A4 : l'aperçu, puis une page par baseplate avec la carte des tenons et la liste des pièces.",
        dmc.Group(
            [
                dmc.Button("Télécharger le PDF", id="download-pdf", size="md", disabled=True),
                dmc.Button("Copier la liste BrickLink", id="copy-bricklink", size="md", variant="light", disabled=True),
                dmc.Button("Télécharger la liste", id="download-bricklink", size="md", variant="subtle", disabled=True),
                html.Div(id="copy-status"),
                html.Div(id="pdf-status"),
            ],
            gap="md",
        ),
        dmc.Text(
            [
                "La liste BrickLink contient les pièces choisies et le rab, sans les baseplates. Copie-la, "
                "puis colle-la sur la ",
                dmc.Anchor("page d'import BrickLink", href=BRICKLINK_UPLOAD_URL, target="_blank", size="xs"),
                ", option « Upload BrickLink XML format ».",
            ],
            c="dimmed",
            size="xs",
            mt="sm",
        ),
        dcc.Download(id="bricklink-file"),
        dcc.Store(id="pdf-url"),  # Set once a PDF is ready; downloading it is done client-side.
        dcc.Store(id="pdf-download-done"),
    )

    return dmc.MantineProvider(
        dmc.Container(
            [
                hero,
                source,
                settings,
                styles,
                download,
                dcc.Store(id="source"),  # {"session": id, "name": ..., "width"/"height": preview size}
                dcc.Store(id="crop"),  # [left, top, right, bottom] as fractions, or None
                dcc.Store(id="settings"),  # The settings the current previews were made with.
                dcc.Store(id="builds"),  # {style: {"distribution", "baseplates"}}, to reprice without refitting.
                dcc.Store(id="selected-style"),
                dcc.Store(id="previews"),  # {style: {"image", "label"}}, for the enlarged view.
                dmc.Modal(html.Img(id="preview-image", className="preview-image"), id="preview-modal", size="auto"),
                dmc.Modal(
                    [
                        dmc.Text(
                            "Glisse pour tourner, molette pour zoomer, double-clic pour revenir au départ.",
                            c="dimmed",
                            size="xs",
                            mb="sm",
                        ),
                        html.Div(id="viewer-stage", className="viewer-stage"),
                    ],
                    id="viewer-modal",
                    fullScreen=True,
                ),
                dcc.Store(id="viewer-url"),
                dcc.Store(id="viewer-done"),
                dmc.Modal(
                    [
                        dmc.Text(
                            id="realistic-note",
                            c="dimmed",
                            size="xs",
                            mb="sm",
                        ),
                        html.Img(id="realistic-image", className="realistic-image", n_clicks=0),
                    ],
                    id="realistic-modal",
                    fullScreen=True,
                ),
            ],
            size="lg",
            py=40,
        ),
        theme=THEME,
        forceColorScheme="light",
    )


def _price_detail(prices: dict[str, object]) -> str:
    baseplates = "" if prices["price_baseplates"].startswith("0 ") else f" · baseplates {prices['price_baseplates']}"
    return f"Pièces {prices['price_bricks']} (+5 % de rab){baseplates}"


def _pieces(result: dict[str, object]) -> str:
    """The pieces of a build mixing several kinds, which "briques, soit plaques" can't say."""
    tiles, supports = result["round_tile_count"], result["support_count"]
    plates = result["plates"] - tiles - supports
    kinds = [
        (tiles, "tuiles rondes"),
        (plates, "tuiles de bordure" if result["round_tiles"] else "tuiles"),
        (supports, "plaques cachées"),
        (result["assembly_count"], "pièces d'assemblage"),
    ]
    return " + ".join(f"{count:,} {name}" for count, name in kinds if count).replace(",", " ")


def _style_card(result: dict[str, object]) -> html.Div:
    bricks = f"{result['bricks']:,}".replace(",", " ")
    style = result["style"]
    return html.Div(
        dmc.Card(
            [
                dmc.CardSection(
                    html.Img(
                        src=result["image"],
                        className="style-image smooth" if result["smooth"] else "style-image",
                    )
                ),
                dmc.Group(
                    [
                        dmc.Button(
                            "Agrandir",
                            id={"type": "style-enlarge", "index": style},
                            variant="subtle",
                            size="compact-xs",
                        ),
                        dmc.Button(
                            "Rendu réaliste",
                            id={"type": "style-realistic", "index": style},
                            variant="subtle",
                            size="compact-xs",
                        ),
                        dmc.Button(
                            "Vue 3D",
                            id={"type": "style-3d", "index": style},
                            variant="subtle",
                            size="compact-xs",
                        ),
                    ],
                    gap="xs",
                    mt="xs",
                ),
                dmc.Group(
                    [
                        dmc.Text(result["label"], fw=700, size="lg"),
                        dmc.Badge(f"{result['colors']} couleurs", variant="light"),
                    ],
                    justify="space-between",
                    mt="md",
                ),
                dmc.Text(
                    f"{result['studs']} tenons · "
                    + (
                        f"{result['baseplates'] * 4} panneaux"
                        if result["assembly"] == "panels"
                        else f"{result['baseplates']} baseplate(s)"
                    ),
                    c="dimmed",
                    size="sm",
                ),
                dmc.Text(
                    _pieces(result)
                    if result["round_tiles"] or result["relief"] != "flat" or result["assembly"] != "baseplates"
                    else f"{bricks} briques" + f", soit {result['plates']:,} tuiles".replace(",", " "),
                    c="dimmed",
                    size="sm",
                ),
                dmc.Divider(my="sm"),
                dmc.Group(
                    [
                        dmc.Text(result["price"], id={"type": "style-price", "index": style}, fw=700, size="lg"),
                        dmc.Text("hors livraison", c="dimmed", size="xs"),
                    ],
                    justify="space-between",
                    align="baseline",
                ),
                dmc.Text(
                    _price_detail(result), id={"type": "style-price-detail", "index": style}, c="dimmed", size="xs"
                ),
                *(
                    [
                        dmc.Alert(
                            f"Couleurs rares, chères ou difficiles à trouver : {', '.join(result['rare_colors'])}",
                            color="orange",
                            variant="light",
                            radius="md",
                            p="xs",
                            mt="sm",
                            styles={"message": {"fontSize": "12px"}},
                        )
                    ]
                    if result["rare_colors"]
                    else []
                ),
            ],
            withBorder=True,
            radius="lg",
            padding="md",
            shadow="xs",
        ),
        id={"type": "style-card", "index": style},
        className="style-card",
        n_clicks=0,
    )


def _crop_figure(image_uri: str, width: int, height: int) -> go.Figure:
    """The source image as a Plotly figure where dragging draws the crop box.
    Pixel (0, 0) is centred on (0, 0), so the image spans -0.5 to width - 0.5."""
    figure = go.Figure(go.Image(source=image_uri))
    figure.update_layout(
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        dragmode="drawrect",
        newshape=CROP_SHAPE_STYLE,
        xaxis={"visible": False, "range": [-0.5, width - 0.5]},
        yaxis={"visible": False, "range": [height - 0.5, -0.5], "scaleanchor": "x"},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        shapes=[],
    )
    return figure


def _crop_shape(box: tuple[float, float, float, float]) -> dict[str, object]:
    left, top, right, bottom = box
    return {"type": "rect", "editable": True, "x0": left, "y0": top, "x1": right, "y1": bottom, **CROP_SHAPE_STYLE}


def next_crop_box(
    trigger: str | None,
    relayout: dict[str, object] | None,
    current: dict[str, float] | None,
    ratio: str,
    width: int,
    height: int,
) -> tuple[float, float, float, float] | None | bool:
    """
    Work out the crop box after an interaction, in preview pixels shifted so the
    image spans exactly 0..width and 0..height. Returns None for "no crop", or
    False when the interaction doesn't touch the crop.

    :param trigger: Which control fired: the graph, the ratio or the reset button.
    :param relayout: The graph's ``relayoutData``: a list of ``shapes`` when one
        is drawn or erased, ``shapes[0].x0``-style keys when one is moved.
    :param current: The crop shape currently on the graph, if any.
    :param ratio: A ``CROP_RATIOS`` value.
    """
    if trigger == "crop-reset":
        return None
    if trigger == "crop-graph":
        relayout = relayout or {}
        if "shapes" in relayout:
            shapes = relayout["shapes"]
            if not shapes:
                return None
            current = shapes[-1]  # Drawing again replaces the previous box.
        elif current and any(key.startswith("shapes[") for key in relayout):
            current = dict(current)
            for key, value in relayout.items():
                if key.startswith("shapes[") and "." in key:
                    current[key.rsplit(".", 1)[1]] = value
        else:
            return False  # An autosize or similar: nothing to do with the crop.
    elif trigger == "crop-ratio" and not current:
        if ratio == "free":
            return False
        current = {"x0": -0.5, "y0": -0.5, "x1": width - 0.5, "y1": height - 0.5}  # Fit the ratio to the whole image.

    if not current:
        return None
    left, right = sorted((float(current["x0"]) + 0.5, float(current["x1"]) + 0.5))
    top, bottom = sorted((float(current["y0"]) + 0.5, float(current["y1"]) + 0.5))
    left, right = max(0.0, left), min(float(width), right)
    top, bottom = max(0.0, top), min(float(height), bottom)
    if right - left < 1 or bottom - top < 1:
        return None
    box = (left, top, right, bottom)
    if ratio != "free":
        box = service.fit_ratio(box, float(ratio), width, height)
    return box


def create_app() -> Dash:
    # Three.js is an ES module, which viewer.js imports when needed: not a page script.
    app = Dash(__name__, title="Studify", assets_ignore=r"^three\.")
    app.layout = _layout()

    @app.callback(
        Output("source", "data"),
        Output("crop-graph", "figure"),
        Output("crop-area", "style"),
        Output("crop", "data"),
        Output("source-status", "children"),
        Input("upload", "contents"),
        Input("load-url", "n_clicks"),
        Input("url", "value"),
        State("upload", "filename"),
        running=[(Output("load-url", "loading"), True, False)],
        prevent_initial_call=True,
    )
    def load_source(contents, _clicks, url, filename):
        try:
            if ctx.triggered_id == "upload":
                if not contents:
                    return no_update, no_update, no_update, no_update, no_update
                data, name = service.image_bytes_from_upload(contents), filename or "image"
            else:
                if not url:
                    return no_update, no_update, no_update, no_update, no_update
                data = service.image_bytes_from_url(url)
                name = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "image"
            session = service.save_source(data, name)
        except service.ImageError as exc:
            return no_update, no_update, no_update, no_update, _error(str(exc))
        image_uri, width, height = service.preview(service.source_path(session))
        status = dmc.Text(f"Image chargée : {name}", c="green", size="sm", fw=500)
        source = {"session": session, "name": name, "width": width, "height": height}
        return source, _crop_figure(image_uri, width, height), {}, None, status

    @app.callback(
        Output("crop", "data", allow_duplicate=True),
        Output("crop-graph", "figure", allow_duplicate=True),
        Input("crop-graph", "relayoutData"),
        Input("crop-ratio", "value"),
        Input("crop-reset", "n_clicks"),
        State("crop-graph", "figure"),
        State("source", "data"),
        prevent_initial_call=True,
    )
    def update_crop(relayout, ratio, _reset, figure, source):
        if not source:
            return no_update, no_update
        shapes = (figure or {}).get("layout", {}).get("shapes") or []
        width, height = source["width"], source["height"]
        box = next_crop_box(ctx.triggered_id, relayout, shapes[0] if shapes else None, ratio, width, height)
        if box is False:
            return no_update, no_update
        # Patch only the shapes, rather than sending the whole image back.
        patch = Patch()
        if box is None:
            patch["layout"]["shapes"] = []
            return None, patch
        left, top, right, bottom = box
        patch["layout"]["shapes"] = [_crop_shape((left - 0.5, top - 0.5, right - 0.5, bottom - 0.5))]
        crop = service.validate_crop([left / width, top / height, right / width, bottom / height])
        return (None if crop is None else [round(v, 4) for v in crop]), patch

    @app.callback(Output("crop-status", "children"), Input("crop", "data"))
    def describe_crop(crop):
        if not crop:
            return "Image entière."
        left, top, right, bottom = crop
        return f"Zone gardée : {(right - left) * 100:.0f} % de la largeur × {(bottom - top) * 100:.0f} % de la hauteur."

    @app.callback(Output("size-label", "children"), Input("size", "value"))
    def describe_size(size):
        studs = size * DIM_BASE
        return (
            f"Taille : {size} baseplate(s) sur le plus grand côté, soit {studs} tenons ≈ {studs * STUD_MM / 10:.0f} cm"
        )

    @app.callback(Output("generate", "disabled"), Input("source", "data"))
    def enable_generate(source):
        return not source

    @app.callback(
        Output("styles", "children"),
        Output("settings", "data"),
        Output("selected-style", "data"),
        Output("builds", "data"),
        Output("previews", "data"),
        Input("generate", "n_clicks"),
        Input("source", "data"),
        Input("crop", "data"),
        Input("palette", "value"),
        State("baseplates", "value"),
        State("currency", "value"),
        State("size", "value"),
        State("frame", "value"),
        State("max-colors", "value"),
        State("dither", "checked"),
        State("plates", "value"),
        State("tinted", "checked"),
        State("frame-color", "value"),
        State("mode", "value"),
        State("assembly", "value"),
        State("sharp", "checked"),
        running=[(Output("generate", "loading"), True, False)],
        prevent_initial_call=True,
    )
    def generate_styles(
        _clicks,
        source,
        crop,
        palette,
        baseplates,
        currency,
        size,
        frame,
        max_colors,
        dither,
        plates,
        tinted,
        frame_color,
        mode,
        assembly,
        sharp,
    ):
        if not source:
            return no_update, no_update, no_update, no_update, no_update
        settings = {
            "size": size,
            "frame": frame,
            "frame_color": frame_color,
            "max_colors": int(max_colors) or None,
            "crop": crop,
            "baseplates": baseplates,
            "currency": currency,
            "palette": palette,
            "dither": bool(dither),
            "plates": plates,
            "tinted": bool(tinted),
            "round_tiles": mode == "round",
            "relief": {"relief": "stepped", "depth": "depth"}.get(mode, "flat"),
            "assembly": assembly,
            "sharp": bool(sharp),
        }
        try:
            results = service.render_styles(source["session"], **settings)
        except service.ImageError as exc:
            return _error(str(exc)), None, None, None, None
        cards = dmc.SimpleGrid([_style_card(result) for result in results], cols={"base": 1, "sm": 3}, spacing="lg")
        builds = {
            r["style"]: {
                "distribution": r["distribution"],
                "baseplates": r["priced_baseplates"],
                "wanted_list": r["wanted_list"],
                "parts": r["parts"],
            }
            for r in results
        }
        previews = {r["style"]: {"image": r["image"], "label": r["label"], "smooth": r["smooth"]} for r in results}
        return cards, settings, None, builds, previews

    @app.callback(
        Output("preview-modal", "opened"),
        Output("preview-modal", "title"),
        Output("preview-image", "src"),
        Output("preview-image", "className"),
        Input({"type": "style-enlarge", "index": ALL}, "n_clicks"),
        State("previews", "data"),
        prevent_initial_call=True,
    )
    def enlarge_style(clicks, previews):
        # Cards being (re)drawn also fires this, with no clicks: ignore that.
        if not ctx.triggered_id or not any(clicks) or not previews:
            raise PreventUpdate
        preview = previews[ctx.triggered_id["index"]]
        image_class = "preview-image smooth" if preview["smooth"] else "preview-image"
        return True, preview["label"], preview["image"], image_class

    @app.server.route("/realistic/<session_id>/<style>.png")
    def serve_realistic(session_id, style):
        # Over plain HTTP, like the PDF: a large mosaic makes a heavy image, drawn
        # only if asked for, which shouldn't sit in the page as base64.
        try:
            return Response(service.realistic_png(session_id, style), mimetype="image/png")
        except service.ImageError:
            abort(404)

    @app.callback(
        Output("realistic-modal", "opened"),
        Output("realistic-modal", "title"),
        Output("realistic-image", "src"),
        Output("realistic-note", "children"),
        Input({"type": "style-realistic", "index": ALL}, "n_clicks"),
        State("source", "data"),
        State("settings", "data"),
        prevent_initial_call=True,
    )
    def show_realistic(clicks, source, settings):
        # Cards being (re)drawn also fires this, with no clicks: ignore that.
        if not ctx.triggered_id or not any(clicks) or not source:
            raise PreventUpdate
        style = ctx.triggered_id["index"]
        # The timestamp stops the browser reusing the image of an earlier render.
        url = f"/realistic/{source['session']}/{style}.png?v={time.time_ns()}"
        settings = settings or {}
        if settings.get("round_tiles"):
            note = "Une tuile ronde par tenon, sur une baseplate sombre."
        else:
            note = "Tuiles lisses, carrées et rectangulaires, comme dans le guide et la liste BrickLink."
        if settings.get("relief") in ("stepped", "depth"):
            note += " Le sujet est surélevé sur des plaques noires cachées."
        return True, f"{service.STYLE_LABELS[style]} : rendu réaliste", url, f"{note} Clique sur l'image pour zoomer."

    @app.server.route("/model/<session_id>/<style>.json")
    def serve_model(session_id, style):
        try:
            return jsonify(service.model_data(session_id, style))
        except service.ImageError:
            abort(404)

    @app.callback(
        Output("viewer-modal", "opened"),
        Output("viewer-modal", "title"),
        Output("viewer-url", "data"),
        Input({"type": "style-3d", "index": ALL}, "n_clicks"),
        State("source", "data"),
        prevent_initial_call=True,
    )
    def show_3d(clicks, source):
        # Cards being (re)drawn also fires this, with no clicks: ignore that.
        if not ctx.triggered_id or not any(clicks) or not source:
            raise PreventUpdate
        style = ctx.triggered_id["index"]
        # The timestamp stops the browser reusing the model of an earlier render.
        url = f"/model/{source['session']}/{style}.json?v={time.time_ns()}"
        return True, f"{service.STYLE_LABELS[style]} : vue 3D", url

    # Drawn in the browser (see assets/viewer.js), and freed as soon as the window closes.
    app.clientside_callback(
        """
        function (url, opened) {
            if (opened && url) {
                window.studifyViewer.show("viewer-stage", url);
            } else {
                window.studifyViewer.hide();
            }
            return null;
        }
        """,
        Output("viewer-done", "data"),
        Input("viewer-url", "data"),
        Input("viewer-modal", "opened"),
        prevent_initial_call=True,
    )

    # Zoom in the browser: nothing about it concerns the server.
    app.clientside_callback(
        """
        function (clicks) {
            return clicks % 2 ? "realistic-image zoomed" : "realistic-image";
        }
        """,
        Output("realistic-image", "className"),
        Input("realistic-image", "n_clicks"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output({"type": "style-price", "index": ALL}, "children"),
        Output({"type": "style-price-detail", "index": ALL}, "children"),
        Output("settings", "data", allow_duplicate=True),
        Input("baseplates", "value"),
        Input("currency", "value"),
        State("settings", "data"),
        State("builds", "data"),
        State({"type": "style-price", "index": ALL}, "id"),
        prevent_initial_call=True,
    )
    def update_prices(baseplates, currency, settings, builds, price_ids):
        # Only the prices depend on these two: the mosaics stay as they are, and so
        # does the selected style. The settings are updated for the PDF guide.
        if not (settings and builds):
            raise PreventUpdate  # no_update isn't accepted for the wildcard outputs.
        prices = [
            service.reprice(build["distribution"], build["baseplates"], baseplates, currency, build["parts"])
            for build in (builds[price_id["index"]] for price_id in price_ids)
        ]
        return (
            [price["price"] for price in prices],
            [_price_detail(price) for price in prices],
            {**settings, "baseplates": baseplates, "currency": currency},
        )

    @app.callback(
        Output("selected-style", "data", allow_duplicate=True),
        Input({"type": "style-card", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def select_style(clicks):
        # Cards being (re)drawn also fires this, with no clicks: ignore that.
        if not ctx.triggered_id or not any(clicks):
            return no_update
        return ctx.triggered_id["index"]

    @app.callback(
        Output({"type": "style-card", "index": ALL}, "className"),
        Output("download-pdf", "disabled"),
        Output("download-bricklink", "disabled"),
        Output("copy-bricklink", "disabled"),
        Input("selected-style", "data"),
        State({"type": "style-card", "index": ALL}, "id"),
    )
    def highlight_selection(selected, card_ids):
        classes = ["style-card selected" if card["index"] == selected else "style-card" for card in card_ids]
        return classes, selected is None, selected is None, selected is None

    @app.callback(
        Output("bricklink-file", "data"),
        Input("download-bricklink", "n_clicks"),
        State("source", "data"),
        State("builds", "data"),
        State("selected-style", "data"),
        prevent_initial_call=True,
    )
    def download_bricklink(_clicks, source, builds, style):
        if not (source and builds and style):
            return no_update
        return service.bricklink_download(source["name"], style, builds[style]["wanted_list"])

    @app.server.route("/guide/<session_id>/<style>.pdf")
    def serve_pdf(session_id, style):
        # The PDF goes over plain HTTP, streamed, rather than base64 inside a
        # callback response: a 6x6 guide is tens of megabytes.
        try:
            pdf = service.built_pdf(session_id, style)
        except service.ImageError:
            abort(404)
        return send_file(
            pdf, mimetype="application/pdf", as_attachment=True, download_name=f"studify-{pdf.stem}-{style}.pdf"
        )

    @app.callback(
        Output("pdf-url", "data"),
        Output("pdf-status", "children"),
        Input("download-pdf", "n_clicks"),
        State("source", "data"),
        State("settings", "data"),
        State("selected-style", "data"),
        running=[(Output("download-pdf", "loading"), True, False)],
        prevent_initial_call=True,
    )
    def download_pdf(_clicks, source, settings, style):
        if not (source and settings and style):
            return no_update, no_update
        try:
            service.build_pdf(source["session"], style=style, **settings)
        except (service.ImageError, RuntimeError) as exc:
            return no_update, _error(str(exc))
        # The timestamp makes the URL change on every build, so the download re-triggers.
        url = f"/guide/{source['session']}/{style}.pdf?v={time.time_ns()}"
        status = dmc.Text(
            ["Guide prêt. Le téléchargement ne démarre pas ? ", dmc.Anchor("Clique ici", href=url)],
            c="green",
            size="sm",
        )
        return url, status

    # The clipboard is only reachable from the browser, and the list is already there.
    app.clientside_callback(
        """
        async function (clicks, builds, style) {
            if (!clicks || !builds || !style) { return window.dash_clientside.no_update; }
            try {
                await navigator.clipboard.writeText(builds[style].wanted_list);
                return "Liste copiée : colle-la sur BrickLink.";
            } catch (error) {
                return "Copie impossible ici : télécharge plutôt la liste.";
            }
        }
        """,
        Output("copy-status", "children"),
        Input("copy-bricklink", "n_clicks"),
        State("builds", "data"),
        State("selected-style", "data"),
        prevent_initial_call=True,
    )

    # Start the download in the browser through a temporary link, so the page stays put.
    app.clientside_callback(
        """
        function (url) {
            if (!url) { return window.dash_clientside.no_update; }
            const link = document.createElement("a");
            link.href = url;
            link.download = "";
            document.body.appendChild(link);
            link.click();
            link.remove();
            return url;
        }
        """,
        Output("pdf-download-done", "data"),
        Input("pdf-url", "data"),
        prevent_initial_call=True,
    )

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="studify-web", description="Run the Studify web app.")
    parser.add_argument("--host", default="127.0.0.1", help="Interface to listen on (default: %(default)s).")
    parser.add_argument("--port", type=int, default=8050, help="Port to listen on (default: %(default)s).")
    parser.add_argument("--debug", action="store_true", help="Enable Dash hot reload and the debug panel.")
    args = parser.parse_args(argv)
    create_app().run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
