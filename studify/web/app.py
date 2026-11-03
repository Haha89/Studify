"""Studify web app: pick a photo, compare the styles, download the PDF guide.

Run it with ``uv run studify-web`` (needs ``uv sync --extra web``), then open
http://127.0.0.1:8050.
"""

from __future__ import annotations

import argparse
import json
import time
from urllib.parse import parse_qs, urlparse

import dash_mantine_components as dmc
import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, Patch, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
from flask import Response, abort, has_request_context, jsonify, redirect, request, send_file

from studify.models.plates import ONLY_1X1, PLATE_SIZES
from studify.models.static import DIM_BASE
from studify.web import projects, service
from studify.web.i18n import COOKIE, LANGUAGES, _, current_language

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
LANGUAGE_COOKIE_SECONDS = 365 * 24 * 3600
BRICKLINK_UPLOAD_URL = "https://www.bricklink.com/v2/wanted/upload.page"
# Requests the production server handles at once. Renders hold one for seconds; PDFs
# and depth reliefs are also limited on their own (see service.MAX_CONCURRENT_HEAVY).
SERVER_THREADS = 8
# Largest request the server reads: a 15 MB picture, sent as base64, is about 20 MB. Without
# a limit, a request is read whole into memory before anything checks its size.
MAX_REQUEST_BYTES = 25 * 2**20

# What each step-2 control starts at, by its id, and the property holding its value.
DEFAULT_CONTROLS = {
    "size": 3,
    "frame": "2",
    "frame-color": "black",
    "max-colors": "0",
    "palette": "buyable",
    "dither": False,
    "accents": False,
    "sharp": False,
    "plates": list(PLATE_SIZES),
    "mode": "flat",
    "assembly": "baseplates",
    "improved-visibility": False,
    "tinted": True,
    "baseplates": "official",
    "currency": "EUR",
}
SWITCHES = ("dither", "accents", "sharp", "improved-visibility", "tinted")
# Projects listed in "Your projects", most recent first.
MAX_LISTED_PROJECTS = 30
THUMBNAIL_PREFIX = "data:image/jpeg;base64,"
# What each finish is, on its card.
MODE_DESCRIPTIONS = {
    "flat": "Des tuiles lisses carrées et rectangulaires, toutes à la même hauteur.",
    "round": "Une tuile ronde 1x1 par tenon, façon LEGO Art. Plus de pièces, et la baseplate se voit entre les tuiles : choisis-la sombre.",
    "relief": "Le sujet surélevé sur des plaques noires cachées, son centre plus haut encore. Détecté automatiquement, il peut être imprécis. Pas encore testé avec de vraies briques.",
    "depth": "Comme Relief, jusqu'à 4 plaques, selon la distance qu'un modèle estime pour chaque zone. Il se télécharge une fois (27 Mo).",
}
# Relief settings, by the mode offered for them.
MODE_RELIEFS = {"relief": "stepped", "depth": "depth"}


def settings_from_controls(controls: dict[str, object], crop: list[float] | None) -> dict[str, object]:
    """Return the settings a render is made with, from the step-2 controls' values."""
    return {
        "size": controls["size"],
        "frame": int(controls["frame"]),
        "frame_color": controls["frame-color"],
        "max_colors": int(controls["max-colors"]) or None,
        "crop": crop,
        "baseplates": controls["baseplates"],
        "currency": controls["currency"],
        "palette": controls["palette"],
        "dither": bool(controls["dither"]),
        "plates": controls["plates"],
        "tinted": bool(controls["tinted"]),
        "round_tiles": controls["mode"] == "round",
        "relief": MODE_RELIEFS.get(controls["mode"], "flat"),
        "assembly": controls["assembly"],
        "sharp": bool(controls["sharp"]),
        "accents": bool(controls["accents"]),
        "improved_visibility": bool(controls["improved-visibility"]),
    }


def _choices() -> dict[str, list[object]]:
    """What each step-2 control with a fixed set of values offers."""
    return {
        "size": list(SIZES),
        "frame": [str(n) for n in FRAMES],
        "frame-color": [value for _label, value in FRAME_COLOR_CHOICES],
        "max-colors": [value for _label, value in COLOR_LIMITS],
        "palette": [value for _label, value in PALETTE_CHOICES],
        "mode": [value for _label, value in MODE_CHOICES],
        "assembly": [value for _label, value in ASSEMBLY_CHOICES],
        "baseplates": [value for _label, value in BASEPLATE_CHOICES],
        "currency": [value for _label, value in CURRENCY_CHOICES],
    }


def clean_controls(raw: object) -> dict[str, object]:
    """
    Return the step-2 controls' values, each checked against what its control offers.
    The page sends them with every request, and a request can be made by hand: an
    unchecked size of 300 would take all the server's memory. Anything a control
    doesn't offer is left at its default.
    """
    raw = raw if isinstance(raw, dict) else {}
    controls = dict(DEFAULT_CONTROLS)
    for control, choices in _choices().items():
        value = raw.get(control)
        if not isinstance(value, bool) and value in choices:
            controls[control] = value
    for control in SWITCHES:
        if isinstance(raw.get(control), bool):
            controls[control] = raw[control]
    plates = raw.get("plates")
    if isinstance(plates, list):
        controls["plates"] = [size for size in PLATE_SIZES if size in plates or size in ONLY_1X1]
    return controls


def controls_from_settings(settings: object) -> dict[str, object]:
    """Return the step-2 controls' values for a render's settings, as a saved project
    or the page's stores keep them, checked by ``clean_controls``."""
    settings = settings if isinstance(settings, dict) else {}
    max_colors = settings.get("max_colors")
    if settings.get("round_tiles") is True:
        mode = "round"
    else:
        mode = {relief: mode for mode, relief in MODE_RELIEFS.items()}.get(settings.get("relief"), "flat")
    return clean_controls(
        {
            "size": settings.get("size"),
            "frame": str(settings.get("frame")),
            "frame-color": settings.get("frame_color"),
            "max-colors": "0" if max_colors is None else str(max_colors),
            "palette": settings.get("palette"),
            "mode": mode,
            "assembly": settings.get("assembly"),
            "baseplates": settings.get("baseplates"),
            "currency": settings.get("currency"),
            "plates": settings.get("plates"),
            **{control: settings.get(control.replace("-", "_")) for control in SWITCHES},
        }
    )


def clean_settings(settings: object) -> dict[str, object]:
    """Return a render's settings, as the page sent them back, with every value checked."""
    crop = settings.get("crop") if isinstance(settings, dict) else None
    return settings_from_controls(controls_from_settings(settings), clean_crop(crop))


def clean_crop(crop: object) -> list[float] | None:
    """Return a crop as the page keeps it, or None if it isn't one."""
    try:
        crop = service.validate_crop(crop)
    except (TypeError, ValueError):
        return None
    return None if crop is None else [round(v, 4) for v in crop]


def opening_page(project_id: str, stores: dict[str, object]) -> str:
    """
    The page that opens a project: it puts the project's session in the browser tab's
    stores, as a reload would find them, forgets the settings changed before in this
    tab, which would otherwise win over the project's, then goes to the app.
    """
    data = json.dumps(stores).replace("<", "\\u003c")
    target = f"/?project={project_id}"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="robots" content="noindex"><title>Studify</title></head>
<body><a href="{target}">Studify</a>
<script>
const stores = {data};
for (const key of Object.keys(sessionStorage)) {{
  if (key.startsWith("_dash_persistence.")) {{ sessionStorage.removeItem(key); }}
}}
for (const [id, value] of Object.entries(stores)) {{
  sessionStorage.setItem(id, JSON.stringify(value));
  sessionStorage.setItem(id + "-timestamp", String(Date.now()));
}}
location.replace("{target}");
</script></body></html>"""


def _project_list(entries: list[dict[str, object]]) -> object:
    """The "Your projects" list: each with its preview, a link to open it, and a button to delete it."""
    if not entries:
        return None
    cards = []
    for entry in entries:
        thumbnail = entry.get("thumbnail")
        cards.append(
            dmc.Paper(
                dmc.Group(
                    [
                        html.Img(
                            src=thumbnail
                            if isinstance(thumbnail, str) and thumbnail.startswith(THUMBNAIL_PREFIX)
                            else "",
                            className="project-thumbnail",
                        ),
                        dmc.Stack(
                            [
                                dmc.Text(str(entry.get("name", "")), fw=600, size="sm", truncate="end"),
                                dmc.Text(
                                    time.strftime("%d/%m/%Y", time.localtime(float(entry.get("saved") or 0))),
                                    c="dimmed",
                                    size="xs",
                                ),
                                dmc.Group(
                                    [
                                        dmc.Anchor(_("Ouvrir"), href=f"/p/{entry['id']}", size="sm", refresh=True),
                                        dmc.Button(
                                            _("Supprimer"),
                                            id={"type": "project-delete", "index": entry["id"]},
                                            variant="subtle",
                                            color="gray",
                                            size="compact-xs",
                                        ),
                                    ],
                                    gap="sm",
                                ),
                            ],
                            gap=2,
                            style={"minWidth": 0},
                        ),
                    ],
                    gap="sm",
                    wrap="nowrap",
                ),
                withBorder=True,
                radius="md",
                p="xs",
            )
        )
    return [
        dmc.Divider(my="lg"),
        dmc.Text(_("Tes projets"), fw=600, size="sm"),
        dmc.Text(
            _("Enregistrés dans ce navigateur. Chacun reste disponible 90 jours après sa dernière ouverture."),
            c="dimmed",
            size="xs",
            mb="sm",
        ),
        dmc.SimpleGrid(cards, cols={"base": 1, "sm": 2, "md": 3}, spacing="sm"),
    ]


def _listed(entries: object) -> list[dict[str, object]]:
    """The "Your projects" entries the browser sent that look like ones the app wrote."""
    if not isinstance(entries, list):
        return []
    return [
        entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and projects.PROJECT_ID.match(entry["id"])
    ][:MAX_LISTED_PROJECTS]


def _share_panel(url: str) -> dmc.Stack:
    return dmc.Stack(
        [
            dmc.Group(
                [
                    dmc.TextInput(id="share-url", value=url, readOnly=True, style={"flex": 1}),
                    dmc.Button(_("Copier le lien"), id="copy-share", variant="light"),
                ],
                gap="sm",
            ),
            html.Div(id="copy-share-status"),
            dmc.Text(
                _(
                    "Ce lien rouvre la photo, les réglages, le style choisi et le relief peint. Toute personne qui a "
                    "le lien peut voir ta photo. Ce que tu changes ensuite ne modifie pas le lien : enregistre à "
                    "nouveau pour en avoir un nouveau."
                ),
                c="dimmed",
                size="xs",
            ),
        ],
        gap=4,
        mt="sm",
    )


def page_query() -> dict[str, str]:
    """
    Return the query of the page's address. The layout isn't built for the page's own
    request but for ``/_dash-layout``, which the page fetches without its query: the
    query is then read from the address the request came from.
    """
    if not has_request_context():
        return {}
    if request.args:
        return request.args.to_dict()
    referrer = urlparse(request.referrer or "")
    if referrer.netloc and referrer.netloc != request.host:
        return {}
    return {key: values[0] for key, values in parse_qs(referrer.query).items()}


def _project_in_url() -> tuple[str | None, dict[str, object] | None]:
    """Return the project the page was opened from, and what it was saved with."""
    project_id = page_query().get("project")
    if not project_id:
        return None, None
    try:
        return project_id, projects.read_project(project_id)
    except projects.ProjectError:
        return None, None


def _switch(control: str, initial: dict[str, object], label: str, description: str) -> dmc.Switch:
    return dmc.Switch(
        id=control,
        checked=initial[control],
        persistence=True,
        persistence_type="session",
        label=label,
        description=description,
    )


def _step(number: int, title: str, subtitle: str, *children, aside=None) -> dmc.Paper:
    """One numbered step of the page; ``aside`` goes on the right of its title."""
    heading = dmc.Group(
        [
            dmc.ThemeIcon(str(number), radius="xl", size="lg", variant="filled"),
            dmc.Stack([dmc.Title(title, order=3), dmc.Text(subtitle, c="dimmed", size="sm")], gap=0),
        ],
        gap="md",
        wrap="nowrap",
    )
    return dmc.Paper(
        [
            dmc.Group([heading, aside], justify="space-between", align="flex-start", mb="lg")
            if aside is not None  # A component without children is falsy.
            else dmc.Box(heading, mb="lg"),
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
    project_id, project = _project_in_url()
    initial = controls_from_settings(project["settings"]) if project else DEFAULT_CONTROLS
    # Switching language reloads the page: it should come back to the same project.
    language_query = f"?project={project_id}" if project_id else ""
    hero = dmc.Stack(
        [
            dmc.Group(
                [dmc.Title("Studify", order=1, c="lego", fz=44), dmc.Badge("beta", variant="light", size="lg")],
                justify="center",
                gap="sm",
            ),
            dmc.Group(
                [
                    dmc.Anchor(
                        code.upper(),
                        href=f"/lang/{code}{language_query}",
                        size="sm",
                        fw=700 if code == current_language() else 400,
                        # Plain browser navigation: this has to reach the server to set the cookie.
                        refresh=True,
                    )
                    for code in LANGUAGES
                ],
                justify="center",
                gap="xs",
            ),
            dmc.Text(
                _("Transforme ta photo en mosaïque LEGO, avec son guide de montage baseplate par baseplate."),
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
        _("Choisis une image"),
        _("Une photo de famille, ton animal, un portrait… PNG, JPEG ou WebP, 15 Mo max."),
        dmc.SimpleGrid(
            [
                dcc.Upload(
                    id="upload",
                    accept="image/*",
                    className="dropzone",
                    children=dmc.Stack(
                        [
                            dmc.Text(_("Glisse ton image ici"), fw=600),
                            dmc.Text(_("ou clique pour parcourir tes fichiers"), c="dimmed", size="sm"),
                        ],
                        gap=2,
                        align="center",
                    ),
                ),
                dmc.Stack(
                    [
                        dmc.TextInput(
                            id="url", label=_("Ou colle un lien vers une image"), placeholder="https://…", debounce=True
                        ),
                        dmc.Button(_("Charger le lien"), id="load-url", variant="light"),
                    ],
                    justify="center",
                    gap="sm",
                ),
            ],
            cols={"base": 1, "sm": 2},
            spacing="lg",
        ),
        html.Div(
            id="source-status",
            className="status",
            children=_error(_("Projet introuvable : il a peut-être expiré ou été supprimé."))
            if page_query().get("missing")
            else None,
        ),
        html.Div(id="my-projects-list"),
        html.Div(
            [
                dmc.Divider(my="lg"),
                dmc.Group(
                    [
                        dmc.Stack(
                            [
                                dmc.Text(_("Rogner l'image (facultatif)"), fw=600, size="sm"),
                                dmc.Text(
                                    _(
                                        "Trace un rectangle sur la photo pour ne garder que cette zone. "
                                        "Tu peux ensuite le déplacer ou l'agrandir."
                                    ),
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
                                    persistence=True,
                                    persistence_type="session",
                                    data=[{"label": _(label), "value": value} for label, value in CROP_RATIOS],
                                    value="free",
                                    radius="xl",
                                    size="xs",
                                ),
                                dmc.Button(_("Réinitialiser"), id="crop-reset", variant="subtle", size="xs"),
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
        _("Règle la mosaïque"),
        _("La taille fixe le prix : chaque baseplate fait 32 × 32 tenons, soit 25,6 cm de côté."),
        dmc.Stack(
            [
                dmc.Stack(
                    [
                        dmc.Text(id="size-label", fw=600, size="sm"),
                        dmc.Slider(
                            id="size",
                            persistence=True,
                            persistence_type="session",
                            min=SIZES[0],
                            max=SIZES[-1],
                            step=1,
                            value=initial["size"],
                            marks=[{"value": n, "label": str(n)} for n in SIZES],
                            restrictToMarks=True,
                            mb="md",
                        ),
                    ],
                    gap=6,
                ),
                dmc.Stack(
                    [
                        dmc.Text(_("Finition"), fw=600, size="sm"),
                        dmc.RadioGroup(
                            dmc.SimpleGrid(
                                [
                                    dmc.RadioCard(
                                        dmc.Stack(
                                            [
                                                html.Img(
                                                    src=f"/assets/finish-{value}.svg",
                                                    className="finish-picture",
                                                    alt="",
                                                ),
                                                dmc.Text(_(label), fw=600, size="sm"),
                                                dmc.Text(_(MODE_DESCRIPTIONS[value]), c="dimmed", size="xs"),
                                            ],
                                            gap=4,
                                        ),
                                        value=value,
                                        className="finish-card",
                                        radius="md",
                                        p="sm",
                                    )
                                    for label, value in MODE_CHOICES
                                ],
                                cols={"base": 2, "sm": 4},
                                spacing="sm",
                            ),
                            id="mode",
                            persistence=True,
                            persistence_type="session",
                            value=initial["mode"],
                        ),
                    ],
                    gap=6,
                ),
                dmc.SimpleGrid(
                    [
                        dmc.Stack(
                            [
                                dmc.Text(_("Cadre autour de l'image"), fw=600, size="sm"),
                                dmc.Group(
                                    [
                                        dmc.SegmentedControl(
                                            id="frame",
                                            persistence=True,
                                            persistence_type="session",
                                            data=[
                                                {
                                                    "label": _("aucun") if n == 0 else _("{n} tenon(s)").format(n=n),
                                                    "value": str(n),
                                                }
                                                for n in FRAMES
                                            ],
                                            value=initial["frame"],
                                            radius="xl",
                                            size="xs",
                                        ),
                                        dmc.SegmentedControl(
                                            id="frame-color",
                                            persistence=True,
                                            persistence_type="session",
                                            data=[
                                                {"label": _(label), "value": value}
                                                for label, value in FRAME_COLOR_CHOICES
                                            ],
                                            value=initial["frame-color"],
                                            radius="xl",
                                            size="xs",
                                        ),
                                    ],
                                    gap="xs",
                                ),
                            ],
                            gap=6,
                        ),
                        dmc.Stack(
                            [
                                dmc.Text(_("Nombre de couleurs maximum"), fw=600, size="sm"),
                                dmc.SegmentedControl(
                                    id="max-colors",
                                    persistence=True,
                                    persistence_type="session",
                                    data=[{"label": _(label), "value": value} for label, value in COLOR_LIMITS],
                                    value=initial["max-colors"],
                                    radius="xl",
                                    size="xs",
                                    style={"alignSelf": "flex-start"},
                                ),
                                dmc.Text(
                                    _("Moins de couleurs : plus simple à commander, et un rendu plus graphique."),
                                    c="dimmed",
                                    size="xs",
                                ),
                            ],
                            gap=6,
                        ),
                    ],
                    cols={"base": 1, "sm": 2},
                    spacing="lg",
                ),
                dmc.Accordion(
                    dmc.AccordionItem(
                        [
                            dmc.AccordionControl(
                                dmc.Group(
                                    [
                                        dmc.Text(_("Plus de réglages"), fw=600, size="sm"),
                                        dmc.Text(
                                            _("palette, tramage, contours, support, baseplates, tailles de tuiles"),
                                            c="dimmed",
                                            size="xs",
                                        ),
                                    ],
                                    gap="sm",
                                )
                            ),
                            dmc.AccordionPanel(
                                dmc.SimpleGrid(
                                    [
                                        dmc.Stack(
                                            [
                                                dmc.Text(_("Couleurs"), className="group-title"),
                                                dmc.Stack(
                                                    [
                                                        dmc.Text(_("Palette de couleurs"), fw=600, size="sm"),
                                                        dmc.SegmentedControl(
                                                            id="palette",
                                                            persistence=True,
                                                            persistence_type="session",
                                                            data=[
                                                                {"label": _(label), "value": value}
                                                                for label, value in PALETTE_CHOICES
                                                            ],
                                                            value=initial["palette"],
                                                            radius="xl",
                                                            size="xs",
                                                            style={"alignSelf": "flex-start"},
                                                        ),
                                                        dmc.Text(
                                                            _(
                                                                "Les couleurs rares (anciens gris et bruns, pastels…) sont chères et dures à trouver "
                                                                "en quantité. Les écarter change peu le rendu et baisse nettement le prix. "
                                                                "Le style Pop art garde sa propre palette."
                                                            ),
                                                            c="dimmed",
                                                            size="xs",
                                                        ),
                                                    ],
                                                    gap=6,
                                                ),
                                                _switch(
                                                    "dither",
                                                    initial,
                                                    _("Tramage"),
                                                    _(
                                                        "Mélange les couleurs pour simuler celles qui manquent. Plus lent à calculer."
                                                    ),
                                                ),
                                                _switch(
                                                    "sharp",
                                                    initial,
                                                    _("Contours nets"),
                                                    _(
                                                        "Pour les logos et les aplats : chaque tenon prend la couleur qui le couvre le "
                                                        "plus, sans teinte intermédiaire sur les bords. À éviter sur les photos. Sans effet avec "
                                                        "le tramage."
                                                    ),
                                                ),
                                                html.Div(
                                                    _switch(
                                                        "accents",
                                                        initial,
                                                        _("Accents or et argent"),
                                                        _(
                                                            "Quelques touches d'or et d'argent sur les petites zones dorées ou grises. Une grande zone ne change que si c'est très probablement du métal. Sans effet avec une limite de couleurs."
                                                        ),
                                                    ),
                                                    id="accents-area",
                                                ),
                                            ],
                                            gap="sm",
                                        ),
                                        dmc.Stack(
                                            [
                                                dmc.Text(_("Pièces"), className="group-title"),
                                                dmc.Stack(
                                                    [
                                                        dmc.Text(_("Support"), fw=600, size="sm"),
                                                        dmc.SegmentedControl(
                                                            id="assembly",
                                                            persistence=True,
                                                            persistence_type="session",
                                                            data=[
                                                                {"label": _(label), "value": value}
                                                                for label, value in ASSEMBLY_CHOICES
                                                            ],
                                                            value=initial["assembly"],
                                                            radius="xl",
                                                            size="xs",
                                                            style={"alignSelf": "flex-start"},
                                                        ),
                                                        dmc.Text(
                                                            _(
                                                                "Baseplates : moins cher, mais elles ne se relient pas entre elles, il faut les "
                                                                "fixer sur un panneau rigide. Panneaux LEGO Art : les panneaux 16 x 16 à goujons "
                                                                "et le cadre en briques des sets officiels, à accrocher tels quels jusqu'à environ "
                                                                "1 m ; plus cher, toutes les pièces sont dans la liste. Non testé en vrai."
                                                            ),
                                                            c="dimmed",
                                                            size="xs",
                                                        ),
                                                    ],
                                                    gap=6,
                                                ),
                                                dmc.Stack(
                                                    [
                                                        dmc.Text(_("Baseplates"), fw=600, size="sm"),
                                                        dmc.SegmentedControl(
                                                            id="baseplates",
                                                            persistence=True,
                                                            persistence_type="session",
                                                            data=[
                                                                {"label": _(label), "value": value}
                                                                for label, value in BASEPLATE_CHOICES
                                                            ],
                                                            value=initial["baseplates"],
                                                            radius="xl",
                                                            size="xs",
                                                            style={"alignSelf": "flex-start"},
                                                        ),
                                                        dmc.Text(
                                                            _("Officielles LEGO ≈ 9 € pièce, compatibles ≈ 2 €."),
                                                            c="dimmed",
                                                            size="xs",
                                                        ),
                                                    ],
                                                    gap=6,
                                                ),
                                                html.Div(
                                                    dmc.CheckboxGroup(
                                                        dmc.Group(
                                                            [
                                                                dmc.Checkbox(
                                                                    label=size, value=size, disabled=size in ONLY_1X1
                                                                )
                                                                for size in PLATE_SIZES
                                                            ],
                                                            mt="xs",
                                                        ),
                                                        id="plates",
                                                        persistence=True,
                                                        persistence_type="session",
                                                        label=_("Tailles de tuiles"),
                                                        description=_(
                                                            "Moins de pièces à poser avec les grandes tuiles, dans le guide et la liste "
                                                            "BrickLink, et un prix estimé plus bas. La 1x1 reste nécessaire pour les tenons isolés."
                                                        ),
                                                        value=initial["plates"],
                                                    ),
                                                    id="plates-area",
                                                ),
                                            ],
                                            gap="sm",
                                        ),
                                    ],
                                    cols={"base": 1, "sm": 2},
                                    spacing="xl",
                                )
                            ),
                        ],
                        value="more",
                    ),
                    variant="contained",
                    radius="md",
                ),
                dmc.Group(
                    [
                        dmc.Text(id="choices-summary", c="dimmed", size="sm"),
                        dmc.Button(_("Générer les styles"), id="generate", size="md", disabled=True),
                    ],
                    justify="space-between",
                    className="choices-bar",
                ),
                html.Div(id="depth-status", className="status"),
                # Only runs while the styles are being made.
                dcc.Interval(id="depth-poll", interval=1000, disabled=True),
            ],
            gap="lg",
        ),
    )

    styles = _step(
        3,
        _("Choisis ton style"),
        _("Clique sur l'aperçu qui te plaît. Les lignes marquent les baseplates."),
        html.Div(id="relief-status", className="status"),
        html.Div(
            dmc.Button(_("Récupérer le relief peint"), id="relief-restore", variant="light", size="xs", mb="sm"),
            id="relief-restore-area",
            style=HIDDEN,
        ),
        html.Div(id="styles"),
        aside=dmc.SegmentedControl(
            id="currency",
            persistence=True,
            persistence_type="session",
            data=[{"label": _(label), "value": value} for label, value in CURRENCY_CHOICES],
            value=initial["currency"],
            radius="xl",
            size="xs",
        ),
    )

    download = _step(
        4,
        _("Télécharge le guide"),
        _("Un PDF A4 : l'aperçu, puis une page par baseplate avec la carte des tenons et la liste des pièces."),
        dmc.Group(
            [
                dmc.Button(_("Télécharger le PDF"), id="download-pdf", size="md", disabled=True),
                dmc.Button(
                    _("Copier la liste BrickLink"), id="copy-bricklink", size="md", variant="light", disabled=True
                ),
                dmc.Button(
                    _("Télécharger la liste"), id="download-bricklink", size="md", variant="subtle", disabled=True
                ),
                dmc.Button(
                    _("Enregistrer et partager"), id="save-project", size="md", variant="outline", disabled=True
                ),
                html.Div(id="copy-status"),
                html.Div(id="pdf-status"),
            ],
            gap="md",
        ),
        dmc.Group(
            [
                _switch(
                    "improved-visibility",
                    initial,
                    _("Visibilité améliorée"),
                    _(
                        "Dans le guide : des cartes plus grandes, avec des numéros plus gros et des lignes plus "
                        "épaisses, et une case à cocher pour chaque pièce. Le guide compte plus de pages."
                    ),
                ),
                _switch(
                    "tinted",
                    initial,
                    _("Cartes en couleur"),
                    _("Teinte chaque tenon dans le guide. Désactive-le pour économiser l'encre."),
                ),
            ],
            grow=True,
            align="flex-start",
            mt="md",
        ),
        html.Div(id="share-area"),
        dmc.Text(
            [
                _(
                    "La liste BrickLink contient les pièces choisies et le rab, sans les baseplates. Copie-la, "
                    "puis colle-la sur la "
                ),
                dmc.Anchor(_("page d'import BrickLink"), href=BRICKLINK_UPLOAD_URL, target="_blank", size="xs"),
                _(", option « Upload BrickLink XML format »."),
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
                # Kept for the browser tab's session, like the settings: changing language reloads the page.
                dcc.Store(
                    id="source", storage_type="session"
                ),  # {"session": id, "name": ..., "width"/"height": preview size}
                dcc.Store(id="crop", storage_type="session"),  # [left, top, right, bottom] as fractions, or None
                dcc.Store(id="rendered", storage_type="session"),  # What the current mosaics were made from.
                dcc.Store(id="settings"),  # The settings the current previews were made with.
                dcc.Store(id="builds"),  # {style: {"distribution", "baseplates"}}, to reprice without refitting.
                dcc.Store(id="selected-style", storage_type="session"),
                dcc.Store(id="previews"),  # {style: {"image", "label"}}, for the enlarged view.
                dmc.Modal(html.Img(id="preview-image", className="preview-image"), id="preview-modal", size="auto"),
                dmc.Modal(
                    [
                        dmc.Text(
                            _(
                                "Glisse pour tourner, molette ou deux doigts pour zoomer, double-clic pour revenir. "
                                "Dos et accroches approximatifs."
                            ),
                            c="dimmed",
                            size="xs",
                            mb="sm",
                        ),
                        html.Div(id="viewer-stage", className="viewer-stage"),
                    ],
                    id="viewer-modal",
                    fullScreen=True,
                ),
                dcc.Store(id="lang", data=current_language()),
                dcc.Store(id="my-projects", storage_type="local"),  # The projects saved in this browser.
                dcc.Store(id="viewer-url"),
                dmc.Modal(html.Div(id="relief-stage", className="relief-stage"), id="relief-modal", fullScreen=True),
                dcc.Store(id="relief-url"),
                dcc.Store(id="relief-done"),
                dcc.Store(id="relief-saved"),
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
    has_baseplates = not prices["price_baseplates"].startswith("0 ")
    baseplates = _(" · baseplates {price}").format(price=prices["price_baseplates"]) if has_baseplates else ""
    return _("Pièces {price} (+5 % de rab)").format(price=prices["price_bricks"]) + baseplates


def _pieces(result: dict[str, object]) -> str:
    """The pieces of a build mixing several kinds, which "briques, soit plaques" can't say."""
    tiles, supports = result["round_tile_count"], result["support_count"]
    plates = result["plates"] - tiles - supports
    kinds = [
        (tiles, _("tuiles rondes")),
        (plates, _("tuiles de bordure") if result["round_tiles"] else _("tuiles")),
        (supports, _("plaques cachées")),
        (result["assembly_count"], _("pièces d'assemblage")),
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
                            _("Agrandir"),
                            id={"type": "style-enlarge", "index": style},
                            variant="subtle",
                            size="compact-xs",
                        ),
                        dmc.Button(
                            _("Rendu réaliste"),
                            id={"type": "style-realistic", "index": style},
                            variant="subtle",
                            size="compact-xs",
                        ),
                        dmc.Button(
                            _("Vue 3D"),
                            id={"type": "style-3d", "index": style},
                            variant="subtle",
                            size="compact-xs",
                        ),
                        *(
                            [
                                dmc.Button(
                                    _("Modifier le relief"),
                                    id={"type": "style-relief", "index": style},
                                    variant="subtle",
                                    size="compact-xs",
                                )
                            ]
                            if result["relief"] != "flat"
                            else []
                        ),
                    ],
                    gap="xs",
                    mt="xs",
                ),
                dmc.Group(
                    [
                        dmc.Text(_(result["label"]), fw=700, size="lg"),
                        dmc.Badge(_("{count} couleurs").format(count=result["colors"]), variant="light"),
                    ],
                    justify="space-between",
                    mt="md",
                ),
                dmc.Text(
                    _("{studs} tenons").format(studs=result["studs"])
                    + " · "
                    + (
                        _("{count} panneaux").format(count=result["baseplates"] * 4)
                        if result["assembly"] == "panels"
                        else _("{count} baseplate(s)").format(count=result["baseplates"])
                    ),
                    c="dimmed",
                    size="sm",
                ),
                dmc.Text(
                    _pieces(result)
                    if result["round_tiles"] or result["relief"] != "flat" or result["assembly"] != "baseplates"
                    else _("{bricks} briques, soit {tiles} tuiles").format(
                        bricks=bricks, tiles=f"{result['plates']:,}".replace(",", " ")
                    ),
                    c="dimmed",
                    size="sm",
                ),
                dmc.Divider(my="sm"),
                dmc.Group(
                    [
                        dmc.Text(result["price"], id={"type": "style-price", "index": style}, fw=700, size="lg"),
                        dmc.Text(_("hors livraison"), c="dimmed", size="xs"),
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
                            _("Couleurs rares, chères ou difficiles à trouver : {colors}").format(
                                colors=", ".join(result["rare_colors"])
                            ),
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


def _cards(results: list[dict[str, object]]) -> dmc.SimpleGrid:
    return dmc.SimpleGrid([_style_card(result) for result in results], cols={"base": 1, "sm": 3}, spacing="lg")


def _builds(results: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {
        r["style"]: {
            "distribution": r["distribution"],
            "baseplates": r["priced_baseplates"],
            "wanted_list": r["wanted_list"],
            "parts": r["parts"],
        }
        for r in results
    }


def _previews(results: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {r["style"]: {"image": r["image"], "label": r["label"], "smooth": r["smooth"]} for r in results}


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
    app.layout = _layout  # Called for each request: it depends on the language.
    app.server.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES

    @app.server.route("/p/<project_id>")
    def open_project(project_id):
        try:
            session_id, project = projects.open_project(project_id)
            _uri, width, height = service.preview(service.source_path(session_id))
        except service.ImageError:
            return redirect("/?missing=1")
        settings = clean_settings(project["settings"])
        crop = settings["crop"]
        stores = {
            "source": {"session": session_id, "name": project["name"], "width": width, "height": height},
            "crop": crop,
            # As if this mosaic had just been made here: its painted relief and chosen style stay.
            "rendered": service.mosaic_key(session_id, settings),
            "selected-style": project["selected"],
        }
        return Response(
            opening_page(project_id, stores),
            mimetype="text/html",
            headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
        )

    @app.callback(
        Output("save-project", "disabled"),
        Output("share-area", "children", allow_duplicate=True),
        Input("settings", "data"),
        prevent_initial_call=True,
    )
    def enable_saving(settings):
        # A link saved earlier no longer shows what's on screen.
        return not settings, None

    @app.callback(
        Output("share-area", "children"),
        Output("my-projects", "data"),
        Input("save-project", "n_clicks"),
        State("source", "data"),
        State("settings", "data"),
        State("selected-style", "data"),
        State("my-projects", "data"),
        running=[(Output("save-project", "loading"), True, False)],
        prevent_initial_call=True,
    )
    def save_project(_clicks, source, settings, selected, listed):
        if not (source and settings):
            raise PreventUpdate
        checked = clean_settings(settings)
        try:
            project = projects.save_project(source["session"], source.get("name", "image"), checked, selected)
        except service.ImageError as exc:
            return _error(str(exc)), no_update
        url = f"{request.host_url.rstrip('/')}/p/{project['id']}"
        return _share_panel(url), [project, *_listed(listed)][:MAX_LISTED_PROJECTS]

    @app.callback(
        Output("my-projects-list", "children"),
        Output("my-projects", "data", allow_duplicate=True),
        Input("my-projects", "data"),
        prevent_initial_call="initial_duplicate",
    )
    def list_projects(listed):
        entries = _listed(listed)
        # Projects expired or deleted elsewhere are dropped from the list.
        kept = projects.kept([entry["id"] for entry in entries])
        entries = [entry for entry in entries if entry["id"] in kept]
        return _project_list(entries), (entries if entries != listed and listed is not None else no_update)

    @app.callback(
        Output("my-projects", "data", allow_duplicate=True),
        Input({"type": "project-delete", "index": ALL}, "n_clicks"),
        State("my-projects", "data"),
        prevent_initial_call=True,
    )
    def delete_project(clicks, listed):
        # The list being (re)drawn also fires this, with no clicks: ignore that.
        if not ctx.triggered_id or not any(clicks):
            raise PreventUpdate
        project_id = ctx.triggered_id["index"]
        entries = _listed(listed)
        for entry in entries:
            if entry["id"] == project_id:
                projects.delete_project(project_id, entry.get("token"))
        return [entry for entry in entries if entry["id"] != project_id]

    app.clientside_callback(
        """
        async function (clicks, url, language) {
            if (!clicks || !url) { return window.dash_clientside.no_update; }
            const english = language === "en";
            try {
                await navigator.clipboard.writeText(url);
                return english ? "Link copied." : "Lien copié.";
            } catch (error) {
                return english ? "Can't copy here: select the link instead." : "Copie impossible ici : sélectionne le lien.";
            }
        }
        """,
        Output("copy-share-status", "children"),
        Input("copy-share", "n_clicks"),
        State("share-url", "value"),
        State("lang", "data"),
        prevent_initial_call=True,
    )

    @app.server.route("/lang/<code>")
    def set_language(code):
        project_id = request.args.get("project", "")
        response = redirect(f"/?project={project_id}" if projects.PROJECT_ID.match(project_id) else "/")
        if code in LANGUAGES:
            response.set_cookie(COOKIE, code, max_age=LANGUAGE_COOKIE_SECONDS, samesite="Lax")
        return response

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
        status = dmc.Text(_("Image chargée : {name}").format(name=name), c="green", size="sm", fw=500)
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
            return _("Image entière.")
        left, top, right, bottom = crop
        return _("Zone gardée : {width:.0f} % de la largeur × {height:.0f} % de la hauteur.").format(
            width=(right - left) * 100, height=(bottom - top) * 100
        )

    @app.callback(Output("size-label", "children"), Input("size", "value"))
    def describe_size(size):
        studs = size * DIM_BASE
        return _("Taille : {size} baseplate(s) sur le plus grand côté, soit {studs} tenons ≈ {cm:.0f} cm").format(
            size=size, studs=studs, cm=studs * STUD_MM / 10
        )

    @app.callback(
        Output("accents-area", "style"),
        Output("plates-area", "style"),
        Output("sharp", "disabled"),
        Input("mode", "value"),
        Input("dither", "checked"),
    )
    def show_what_applies(mode, dither):
        # Only round tiles come in gold and silver, and they are always 1x1.
        round_tiles = mode == "round"
        return ({} if round_tiles else HIDDEN), (HIDDEN if round_tiles else {}), bool(dither)

    @app.callback(
        Output("choices-summary", "children"),
        Input("size", "value"),
        Input("mode", "value"),
        Input("frame", "value"),
        Input("frame-color", "value"),
        Input("max-colors", "value"),
    )
    def describe_choices(size, mode, frame, frame_color, max_colors):
        controls = clean_controls(
            {"size": size, "mode": mode, "frame": frame, "frame-color": frame_color, "max-colors": max_colors}
        )
        modes = dict((value, label) for label, value in MODE_CHOICES)
        colors = dict((value, label) for label, value in FRAME_COLOR_CHOICES)
        parts = [
            _("≈ {cm:.0f} cm").format(cm=controls["size"] * DIM_BASE * STUD_MM / 10),
            _(modes[controls["mode"]]),
            _("sans cadre")
            if controls["frame"] == "0"
            else _("cadre {color} de {n} tenon(s)").format(
                color=_(colors[controls["frame-color"]]).lower(), n=controls["frame"]
            ),
            _("toutes les couleurs")
            if controls["max-colors"] == "0"
            else _("{n} couleurs au plus").format(n=controls["max-colors"]),
        ]
        return " · ".join(parts)

    @app.callback(Output("generate", "disabled"), Input("source", "data"))
    def enable_generate(source):
        return not source

    @app.callback(
        Output("styles", "children"),
        Output("settings", "data"),
        Output("selected-style", "data"),
        Output("builds", "data"),
        Output("previews", "data"),
        Output("relief-status", "children", allow_duplicate=True),
        Output("relief-restore-area", "style"),
        Output("rendered", "data"),
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
        State("accents", "checked"),
        State("improved-visibility", "checked"),
        State("rendered", "data"),
        State("selected-style", "data"),
        running=[(Output("generate", "loading"), True, False), (Output("depth-poll", "disabled"), False, True)],
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
        accents,
        improved_visibility,
        rendered,
        selected,
    ):
        if not source:
            return (no_update,) * 8
        controls = {
            "size": size,
            "frame": frame,
            "frame-color": frame_color,
            "max-colors": max_colors,
            "palette": palette,
            "dither": dither,
            "accents": accents,
            "sharp": sharp,
            "plates": plates,
            "mode": mode,
            "assembly": assembly,
            "improved-visibility": improved_visibility,
            "tinted": tinted,
            "baseplates": baseplates,
            "currency": currency,
        }
        settings = settings_from_controls(clean_controls(controls), clean_crop(crop))
        try:
            # The same mosaic again, as after a reload, keeps any relief painted on it.
            mosaic = service.mosaic_key(source["session"], settings)
            had_edits = mosaic != rendered and service.clear_relief_edits(source["session"])
            results = service.render_styles(source["session"], **settings)
        except service.ImageError as exc:
            return _error(str(exc)), None, None, None, None, "", HIDDEN, None
        # Nothing to restore a relief onto if the new render is flat.
        lost = had_edits and settings["relief"] != "flat"
        status = (
            dmc.Text(_("Cette génération a retiré le relief que tu avais peint."), c="orange", size="sm")
            if lost
            else ""
        )
        restore = {} if lost else HIDDEN
        # The same mosaic again, as after a reload or a change of language, keeps its chosen style.
        selected = selected if mosaic == rendered else None
        return _cards(results), settings, selected, _builds(results), _previews(results), status, restore, mosaic

    @app.callback(
        Output("crop-graph", "figure", allow_duplicate=True),
        Output("crop-area", "style", allow_duplicate=True),
        Input("source", "data"),
        State("crop", "data"),
        State("crop-graph", "figure"),
        prevent_initial_call=True,
    )
    def restore_crop_area(source, crop, figure):
        # After a reload, the picture is known but its crop area hasn't been drawn.
        if not source or (figure and figure.get("data")):
            raise PreventUpdate
        try:
            image_uri, width, height = service.preview(service.source_path(source["session"]))
        except service.ImageError:
            raise PreventUpdate from None  # Expired: rendering the styles says so.
        restored = _crop_figure(image_uri, width, height)
        if crop:
            left, top, right, bottom = crop
            box = (left * width - 0.5, top * height - 0.5, right * width - 0.5, bottom * height - 0.5)
            restored.update_layout(shapes=[_crop_shape(box)])
        return restored, {}

    @app.callback(
        Output("relief-modal", "opened"),
        Output("relief-modal", "title"),
        Output("relief-url", "data"),
        Input({"type": "style-relief", "index": ALL}, "n_clicks"),
        State("source", "data"),
        prevent_initial_call=True,
    )
    def show_relief_editor(clicks, source):
        # Cards being (re)drawn also fires this, with no clicks: ignore that.
        if not ctx.triggered_id or not any(clicks) or not source:
            raise PreventUpdate
        style = ctx.triggered_id["index"]
        url = f"/relief/{source['session']}/{style}.json"
        return True, _("{style} : modifier le relief").format(style=_(service.STYLE_LABELS[style])), url

    @app.server.route("/relief/<session_id>/<style>.json", methods=["GET", "POST"])
    def serve_relief(session_id, style):
        try:
            if request.method == "POST":
                service.save_relief_edit(session_id, style, (request.get_json(silent=True) or {}).get("heights"))
                return jsonify({"saved": True})
            return jsonify(service.relief_data(session_id, style))
        except service.ImageError:
            abort(404)

    @app.callback(
        Output("styles", "children", allow_duplicate=True),
        Output("selected-style", "data", allow_duplicate=True),
        Output("builds", "data", allow_duplicate=True),
        Output("previews", "data", allow_duplicate=True),
        Output("relief-status", "children", allow_duplicate=True),
        Output("relief-restore-area", "style", allow_duplicate=True),
        Input("relief-saved", "data"),
        Input("relief-restore", "n_clicks"),
        State("source", "data"),
        State("settings", "data"),
        running=[(Output("generate", "loading"), True, False)],
        prevent_initial_call=True,
    )
    def apply_relief_edit(_saved, _restore, source, settings):
        if not (source and settings):
            raise PreventUpdate
        settings = clean_settings(settings)
        try:
            if ctx.triggered_id == "relief-restore":
                service.restore_relief_edits(source["session"])
            results = service.render_styles(source["session"], **settings)
        except service.ImageError as exc:
            return no_update, no_update, no_update, no_update, _error(str(exc)), no_update
        dropped = [_(service.STYLE_LABELS[result["style"]]) for result in results if result["relief_dropped"]]
        if dropped:
            status = dmc.Text(
                _(
                    "Le relief peint ne va plus sur {styles} : la mosaïque a changé de taille. Remets les réglages d'avant, génère, puis récupère-le."
                ).format(styles=", ".join(dropped)),
                c="orange",
                size="sm",
            )
        else:
            status = dmc.Text(
                _("Le relief est modifié : le prix, la liste et le guide suivent. Clique sur un style pour le choisir.")
                + _(" Générer à nouveau le retirera, mais tu pourras le récupérer."),
                c="green",
                size="sm",
            )
        return _cards(results), None, _builds(results), _previews(results), status, HIDDEN

    # Painted in the browser (see assets/relief_editor.js).
    app.clientside_callback(
        """
        function (url, opened, language) {
            if (opened && url) {
                window.studifyReliefEditor.show("relief-stage", url, language);
            } else {
                window.studifyReliefEditor.hide();
            }
            return null;
        }
        """,
        Output("relief-done", "data"),
        Input("relief-url", "data"),
        Input("relief-modal", "opened"),
        State("lang", "data"),
        prevent_initial_call=True,
    )

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
        return True, _(preview["label"]), preview["image"], image_class

    @app.server.route("/preview/<session_id>/<style>.png")
    def serve_preview(session_id, style):
        try:
            return send_file(service.preview_path(session_id, style), mimetype="image/png")
        except service.ImageError:
            abort(404)

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
            note = _("Une tuile ronde par tenon, sur une baseplate sombre.")
        else:
            note = _("Tuiles lisses, carrées et rectangulaires, comme dans le guide et la liste BrickLink.")
        if settings.get("relief") in ("stepped", "depth"):
            note += _(" Le sujet est surélevé sur des plaques noires cachées.")
        title = _("{style} : rendu réaliste").format(style=_(service.STYLE_LABELS[style]))
        return True, title, url, note + _(" Clique sur l'image pour zoomer.")

    @app.callback(
        Output("depth-status", "children"),
        Input("depth-poll", "n_intervals"),
        prevent_initial_call=True,
    )
    def show_depth_download(_ticks):
        message = service.depth_download_message()
        return dmc.Text(message, c="dimmed", size="sm") if message else ""

    # The last tick may come before the end of the download: clear what it left.
    app.clientside_callback(
        """
        function (disabled) {
            return disabled ? "" : window.dash_clientside.no_update;
        }
        """,
        Output("depth-status", "children", allow_duplicate=True),
        Input("depth-poll", "disabled"),
        prevent_initial_call=True,
    )

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
        return True, _("{style} : vue 3D").format(style=_(service.STYLE_LABELS[style])), url

    # Drawn in the browser (see assets/viewer.js), and freed as soon as the window closes.
    app.clientside_callback(
        """
        function (url, opened, language) {
            if (opened && url) {
                window.studifyViewer.show("viewer-stage", url, language);
            } else {
                window.studifyViewer.hide();
            }
            return null;
        }
        """,
        Output("viewer-done", "data"),
        Input("viewer-url", "data"),
        Input("viewer-modal", "opened"),
        State("lang", "data"),
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
        settings = clean_settings({**settings, "baseplates": baseplates, "currency": currency})
        baseplates, currency = settings["baseplates"], settings["currency"]
        prices = [
            service.reprice(build["distribution"], build["baseplates"], baseplates, currency, build["parts"])
            for build in (builds[price_id["index"]] for price_id in price_ids)
        ]
        return (
            [price["price"] for price in prices],
            [_price_detail(price) for price in prices],
            settings,
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
            service.build_pdf(source["session"], style=style, **clean_settings(settings))
        except (service.ImageError, RuntimeError) as exc:
            return no_update, _error(str(exc))
        # The timestamp makes the URL change on every build, so the download re-triggers.
        url = f"/guide/{source['session']}/{style}.pdf?v={time.time_ns()}"
        status = dmc.Text(
            [_("Guide prêt. Le téléchargement ne démarre pas ? "), dmc.Anchor(_("Clique ici"), href=url)],
            c="green",
            size="sm",
        )
        return url, status

    # The clipboard is only reachable from the browser, and the list is already there.
    app.clientside_callback(
        """
        async function (clicks, builds, style, language) {
            if (!clicks || !builds || !style) { return window.dash_clientside.no_update; }
            const english = language === "en";
            try {
                await navigator.clipboard.writeText(builds[style].wanted_list);
                return english ? "List copied: paste it on BrickLink." : "Liste copiée : colle-la sur BrickLink.";
            } catch (error) {
                return english
                    ? "Can't copy here: download the list instead."
                    : "Copie impossible ici : télécharge plutôt la liste.";
            }
        }
        """,
        Output("copy-status", "children"),
        Input("copy-bricklink", "n_clicks"),
        State("builds", "data"),
        State("selected-style", "data"),
        State("lang", "data"),
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
    parser.add_argument(
        "--debug", action="store_true", help="Use Flask's development server, with hot reload and the debug panel."
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=SERVER_THREADS,
        help="Requests served at once (default: %(default)s). Rendering ties one up for seconds.",
    )
    args = parser.parse_args(argv)
    app = create_app()
    if args.debug:
        app.run(host=args.host, port=args.port, debug=True)
    else:
        import waitress

        print(f"Studify on http://{args.host}:{args.port}")
        waitress.serve(app.server, host=args.host, port=args.port, threads=args.threads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
