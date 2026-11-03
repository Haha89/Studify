"""Everything the web app does, minus the UI: fetching and storing the source
image, rendering one preview per style, and building the PDF guide.

Sources are kept server-side, one directory per upload under ``SESSION_ROOT``.
The browser only ever holds the opaque session id, never a file path, so it
can't point the app at an arbitrary file on the server.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import http.client
import ipaddress
import re
import shutil
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np

from studify.models.assembly import DEFAULT_ASSEMBLY
from studify.models.bricklink import ROUND_TILE, wanted_list_xml
from studify.models.canvas import ACCENT_COLORS, DEFAULT_FRAME_COLOR
from studify.models.depth import MAX_PLATES, DepthError, clean_heights, download_progress
from studify.models.pdf_creator import PdfCreator, data_uri_from_rgb_array
from studify.models.plates import PLATE_SIZES
from studify.models.pricing import DEFAULT_PALETTE, PriceEstimate, estimate_price
from studify.models.realistic import render_mosaic
from studify.models.relief import DEFAULT_RELIEF
from studify.models.static import COLORS
from studify.models.utils import fit_within, read_image
from studify.process import build_canvas
from studify.web.i18n import _, current_language

SESSION_ROOT = Path(tempfile.gettempdir()) / "studify-web"
# Sessions older than this are deleted whenever a new image comes in.
SESSION_TTL_SECONDS = 24 * 3600
MAX_IMAGE_BYTES = 15 * 1024 * 1024
# Pixels a picture may have, checked before decoding it: a small, well compressed file
# can still decode into gigabytes. 50 million is a large phone photo's full resolution.
MAX_IMAGE_PIXELS = 50_000_000
# Longest side a source is stored at. The largest mosaic is under 200 studs wide,
# so this leaves room for a tight crop while keeping every later step quick.
MAX_SOURCE_DIMENSION = 2000
URL_TIMEOUT_SECONDS = 10
# Heavy work run at once, beyond which a request waits, then is turned away: each
# PDF starts a full browser, and each depth relief runs a neural network.
MAX_CONCURRENT_HEAVY = 2
# How long a request waits for its turn before being turned away.
HEAVY_WAIT_SECONDS = 30

# Styles offered side by side, in display order, with their French labels.
STYLE_LABELS = {"natural": "Naturel", "pop": "Pop art", "comic": "BD"}

_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
# A crop narrower or shorter than this share of the image is a stray click.
MIN_CROP_FRACTION = 0.04

# (left, top, right, bottom) as fractions of the image's width and height.
Crop = tuple[float, float, float, float]


class ImageError(ValueError):
    """The image couldn't be fetched or read. The message is shown to the user."""


_heavy_slots = threading.BoundedSemaphore(MAX_CONCURRENT_HEAVY)
_heavy_held = threading.local()


class _HeavyWork:
    """Hold one of the ``MAX_CONCURRENT_HEAVY`` slots for PDFs and depth reliefs, so a
    few visitors can't each start a browser or a model and exhaust the server. A slot
    this thread already holds is reused. The limit is per process."""

    def __enter__(self) -> None:
        depth = getattr(_heavy_held, "depth", 0)
        if depth == 0 and not _heavy_slots.acquire(timeout=HEAVY_WAIT_SECONDS):
            raise ImageError(_("Le serveur est très demandé : réessaie dans un instant."))
        _heavy_held.depth = depth + 1

    def __exit__(self, *exc_info) -> None:
        _heavy_held.depth -= 1
        if _heavy_held.depth == 0:
            _heavy_slots.release()


def image_size(data: bytes) -> tuple[int, int] | None:
    """Return a PNG, JPEG, WebP, GIF or BMP file's (width, height), read from its
    header without decoding it, or None if it isn't one of these."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if data.startswith(b"BM") and len(data) >= 26:
        width = int.from_bytes(data[18:22], "little", signed=True)
        height = int.from_bytes(data[22:26], "little", signed=True)
        return abs(width), abs(height)
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8 " and len(data) >= 30:
            return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
        if chunk == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8X" and len(data) >= 30:
            return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
        return None
    if data.startswith(b"\xff\xd8"):
        return _jpeg_size(data)
    return None


# JPEG markers that start a frame and give its size: SOF0 to SOF15, but DHT, JPG and DAC.
_JPEG_FRAMES = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Walk a JPEG's segments up to its frame header, which holds its size."""
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # Padding before a marker.
            i += 1
        elif marker == 0x01 or 0xD0 <= marker <= 0xD8:  # Markers without a length.
            i += 2
        elif marker == 0xDA:  # The image data starts, and no frame header was found.
            return None
        elif marker in _JPEG_FRAMES:
            if i + 9 > len(data):
                return None
            return int.from_bytes(data[i + 7 : i + 9], "big"), int.from_bytes(data[i + 5 : i + 7], "big")
        else:
            i += 2 + int.from_bytes(data[i + 2 : i + 4], "big")
    return None


def image_bytes_from_upload(contents: str) -> bytes:
    """Decode the ``data:<mime>;base64,<data>`` string a Dash upload produces."""
    encoded = contents.partition(",")[2]
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageError(_("Le fichier envoyé est illisible.")) from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError(_("Image trop lourde (max {size} Mo).").format(size=MAX_IMAGE_BYTES // 2**20))
    return data


def _is_public_address(address: str) -> bool:
    """Whether an IP address is on the public internet."""
    return ipaddress.ip_address(address.split("%")[0]).is_global


def _is_public_host(host: str) -> bool:
    """Whether every address ``host`` resolves to is on the public internet, so
    a link can't be used to probe the server's own network."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return bool(infos) and all(_is_public_address(info[4][0]) for info in infos)


def _connect_public(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, *args, **kwargs):
    """Like ``socket.create_connection``, but connect only to an address just checked
    to be public. Checking the link beforehand isn't enough on its own: a host's DNS
    can answer with a public address for the check, then with one on the server's own
    network a moment later, for the download (DNS rebinding)."""
    host, port = address
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OSError(f"Can't resolve {host}") from exc
    if not infos or not all(_is_public_address(info[4][0]) for info in infos):
        raise ImageError(_("Ce lien pointe vers une adresse inaccessible."))
    error: OSError | None = None
    for family, kind, proto, _name, target in infos:
        sock = socket.socket(family, kind, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(target)
            return sock
        except OSError as exc:
            sock.close()
            error = exc
    raise error or OSError(f"Can't connect to {host}")


class _PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_public


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_public


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTPConnection, req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


def _check_link(url: str) -> None:
    """Refuse a link that isn't http(s), or that leads to a non-public address."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ImageError(_("Le lien doit commencer par http:// ou https://."))
    if not _is_public_host(parsed.hostname):
        raise ImageError(_("Ce lien pointe vers une adresse inaccessible."))


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Apply ``_check_link`` to every redirect, or a public link could simply
    forward the download to the server's own network."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            _check_link(newurl)
        except ImageError:
            fp.close()  # urllib only closes the redirecting response once it follows it.
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# No proxy: the server connects to the link itself, so that the address it checks is
# the one it downloads from.
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _PublicHTTPHandler, _PublicHTTPSHandler, _CheckedRedirects
)


def image_bytes_from_url(url: str) -> bytes:
    """Download an image from an http(s) link, up to ``MAX_IMAGE_BYTES``."""
    _check_link(url.strip())
    request = urllib.request.Request(url.strip(), headers={"User-Agent": "Studify/0.1"})
    try:
        with _OPENER.open(request, timeout=URL_TIMEOUT_SECONDS) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
    except ImageError:
        raise  # A redirect that _check_link refused: its message says why.
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()  # It holds the response, and its connection, open.
        raise ImageError(_("Impossible de télécharger l'image depuis ce lien.")) from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError(_("Image trop lourde (max {size} Mo).").format(size=MAX_IMAGE_BYTES // 2**20))
    return data


def _safe_stem(name: str) -> str:
    """A filesystem- and title-friendly version of an uploaded file's name."""
    stem = re.sub(r"[^\w-]+", "_", Path(name).stem).strip("_")
    return stem[:40] or "image"


def _purge_old_sessions(now: float) -> None:
    if not SESSION_ROOT.is_dir():
        return
    for session in SESSION_ROOT.iterdir():
        if session.is_dir() and now - session.stat().st_mtime > SESSION_TTL_SECONDS:
            shutil.rmtree(session, ignore_errors=True)


def save_source(data: bytes, name: str) -> str:
    """
    Check that ``data`` is an image, store it as PNG in a new session directory,
    shrunk to ``MAX_SOURCE_DIMENSION`` if larger, and return the session id.

    :param data: Raw image file bytes (PNG, JPEG, WebP...).
    :param name: Original file name or URL, used to title the guide.
    """
    size = image_size(data)
    if size is None:
        raise ImageError(_("Ce fichier n'est pas une image reconnue (PNG, JPEG, WebP…)."))
    if size[0] * size[1] > MAX_IMAGE_PIXELS:
        raise ImageError(
            _("Image trop grande : {pixels} millions de pixels, {max} millions au plus.").format(
                pixels=round(size[0] * size[1] / 10**6), max=MAX_IMAGE_PIXELS // 10**6
            )
        )
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ImageError(_("Ce fichier n'est pas une image reconnue (PNG, JPEG, WebP…)."))

    ok, png = cv2.imencode(".png", fit_within(img, MAX_SOURCE_DIMENSION))
    if not ok:
        raise ImageError(_("Impossible d'enregistrer cette image."))
    return new_session(png.tobytes(), name)


def new_session(png: bytes, name: str) -> str:
    """Start a session from a PNG already checked and shrunk, and return its id."""
    _purge_old_sessions(time.time())
    session_id = uuid.uuid4().hex
    session = SESSION_ROOT / session_id
    session.mkdir(parents=True)
    # Written via Python, not cv2.imwrite, which fails on non-ASCII paths on Windows.
    (session / f"{_safe_stem(name)}.png").write_bytes(png)
    return session_id


def source_path(session_id: str) -> Path:
    """Return the stored source image for a session id."""
    if not isinstance(session_id, str) or not _SESSION_ID.match(session_id):
        raise ImageError(_("Session invalide."))
    images = sorted((SESSION_ROOT / session_id).glob("*.png"))
    if not images:
        raise ImageError(_("Image introuvable : elle a peut-être expiré, recharge-la."))
    return images[0]


def path_in_session(session_id: str, name: str) -> Path:
    """Return the path of a file kept next to a session's source image."""
    return source_path(session_id).parent / name


def preview(path: Path, max_dimension: int = 640) -> tuple[str, int, int]:
    """A small PNG ``data:`` URI of the source image for the page, with its
    width and height in pixels."""
    img = fit_within(cv2.cvtColor(read_image(path), cv2.COLOR_BGR2RGB), max_dimension)
    return data_uri_from_rgb_array(img), img.shape[1], img.shape[0]


def validate_crop(crop: Crop | list[float] | None) -> Crop | None:
    """Return ``crop`` clamped to the image, or None for "no crop" (also for a
    crop too small to be deliberate)."""
    if crop is None:
        return None
    left, top, right, bottom = (min(1.0, max(0.0, float(v))) for v in crop)
    left, right = sorted((left, right))
    top, bottom = sorted((top, bottom))
    if right - left < MIN_CROP_FRACTION or bottom - top < MIN_CROP_FRACTION:
        return None
    if (left, top, right, bottom) == (0.0, 0.0, 1.0, 1.0):
        return None
    return left, top, right, bottom


def fit_ratio(box: Crop, ratio: float, width: float, height: float) -> Crop:
    """
    Shrink a box (in pixels) to an aspect ratio around its centre, then slide it
    back inside the ``width`` x ``height`` image if it pokes out.

    :param box: (left, top, right, bottom) in pixels.
    :param ratio: Target width / height.
    """
    left, top, right, bottom = box
    box_width, box_height = right - left, bottom - top
    if box_width / box_height > ratio:
        box_width = box_height * ratio
    else:
        box_height = box_width / ratio
    box_width, box_height = min(box_width, width), min(box_height, height)
    centre_x = min(max((left + right) / 2, box_width / 2), width - box_width / 2)
    centre_y = min(max((top + bottom) / 2, box_height / 2), height - box_height / 2)
    return (
        centre_x - box_width / 2,
        centre_y - box_height / 2,
        centre_x + box_width / 2,
        centre_y + box_height / 2,
    )


def _working_image(session_id: str, crop: Crop | list[float] | None) -> Path:
    """The image to build from: the source itself, or a cropped copy of it.
    The copy keeps the source's file name, so the guide keeps its title."""
    path = source_path(session_id)
    crop = validate_crop(crop)
    if crop is None:
        return path
    img = read_image(path)
    height, width = img.shape[:2]
    left, top, right, bottom = crop
    cropped = img[round(top * height) : round(bottom * height), round(left * width) : round(right * width)]
    ok, png = cv2.imencode(".png", cropped)
    if not ok:
        raise ImageError(_("Impossible de rogner cette image."))
    target = path.parent / "cropped" / path.name
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(png.tobytes())
    return target


def _price_fields(price: PriceEstimate) -> dict[str, str]:
    """The price estimate as the strings a style card shows."""
    return {
        "price": price.format(),
        "price_bricks": f"{price.format(price.bricks_low)} – {price.format(price.bricks_high)}",
        "price_baseplates": price.format(price.baseplates),
    }


def reprice(
    distribution: dict[str | int, int],
    baseplate_count: int,
    baseplates: str,
    currency: str,
    parts: list[list[int | str]] | None = None,
) -> dict[str, str]:
    """Price an already rendered style for other baseplates or another currency,
    without fitting it again.

    :param distribution: The render's bricks, as {brick id: count}. Ids may be
        strings: the page stores this as JSON.
    :param baseplate_count: Number of baseplates the render covers.
    :param parts: The render's parts, as [brick id, part number, count] lists, which
        JSON can store. Defaults to one 1x1 plate per brick.
    """
    counts = {int(brick_id): count for brick_id, count in distribution.items()}
    by_part = None if parts is None else {(brick_id, part): count for brick_id, part, count in parts}
    return _price_fields(estimate_price(counts, baseplate_count, baseplates, currency, by_part))


def bricklink_download(name: str, style: str, wanted_list: str) -> dict[str, str]:
    """A rendered style's BrickLink wanted list, as a ``dcc.Download`` payload.

    :param name: The source image's original name, used to name the file.
    :param wanted_list: The list's XML, from ``render_styles``.
    """
    return {
        "content": wanted_list,
        "filename": f"studify-{_safe_stem(name)}-{style}-bricklink.xml",
        "type": "application/xml",
    }


def depth_download_message() -> str:
    """What to show while the depth model downloads: nothing the rest of the time."""
    progress = download_progress()
    if progress is None:
        return ""
    done, total = progress
    return _(
        "Téléchargement du modèle de profondeur, une seule fois : {done:.0f} / {total:.0f} Mo. Les styles suivront."
    ).format(done=done / 10**6, total=total / 10**6)


def _save_preview(session_id: str, style: str, rgb: np.ndarray) -> str:
    """Keep a style's preview in the session, and return the URL to show it at. It goes
    over plain HTTP: as base64, three of them make a callback response of many megabytes,
    which browsers were seen to drop. The timestamp stops them reusing an earlier one."""
    ok, png = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise ImageError(_("Impossible de dessiner cette mosaïque."))
    path_in_session(session_id, f"preview-{style}.png").write_bytes(png.tobytes())
    return f"/preview/{session_id}/{style}.png?v={time.time_ns()}"


def preview_path(session_id: str, style: str) -> Path:
    """Return where a style's preview is kept."""
    if style not in STYLE_LABELS:
        raise ImageError(_("Style inconnu."))
    path = path_in_session(session_id, f"preview-{style}.png")
    if not path.is_file():
        raise ImageError(_("Cette mosaïque n'a pas encore été générée."))
    return path


def render_styles(
    session_id: str,
    size: int,
    frame: int,
    max_colors: int | None,
    crop: Crop | list[float] | None = None,
    baseplates: str = "official",
    currency: str = "EUR",
    palette: str = DEFAULT_PALETTE,
    dither: bool = False,
    plates: Iterable[str] = PLATE_SIZES,
    tinted: bool = True,
    round_tiles: bool = False,
    frame_color: str = DEFAULT_FRAME_COLOR,
    relief: str = DEFAULT_RELIEF,
    assembly: str = DEFAULT_ASSEMBLY,
    sharp: bool = False,
    accents: bool = False,
    improved_visibility: bool = False,
) -> list[dict[str, object]]:
    """Fit the session's image (cropped to ``crop``, if given) once per style in
    ``STYLE_LABELS`` and return, for each, its preview image, build figures and
    price estimate (see ``pricing.estimate_price``). ``tinted`` and ``improved_visibility``
    only concern the PDF guide, and are accepted so that the same settings can be passed to both.

    Each style's mosaic is also kept in the session, for ``realistic_png``. A style
    whose painted relief no longer fits the mosaic, after a change of size, says so with
    ``relief_dropped``."""
    path = _working_image(session_id, crop)
    # A depth relief runs a neural network: it waits for its turn, like a PDF.
    options = {
        "frame": frame,
        "frame_color": frame_color,
        "max_colors": max_colors,
        "palette": palette,
        "dither": dither,
        "round_tiles": round_tiles,
        "relief": relief,
        "assembly": assembly,
        "sharp": sharp,
        "accents": accents and round_tiles,
    }
    with _HeavyWork() if relief == "depth" else contextlib.nullcontext():
        return [
            _render_style(session_id, path, style, label, size, options, plates, baseplates, currency)
            for style, label in STYLE_LABELS.items()
        ]


def _render_style(
    session_id: str,
    path: Path,
    style: str,
    label: str,
    size: int,
    options: dict[str, object],
    plates: Iterable[str],
    baseplates: str,
    currency: str,
) -> dict[str, object]:
    """Render one style for ``render_styles``.

    :param options: ``build_canvas``'s keyword arguments, but the style and the relief painted.
    """
    round_tiles, relief, assembly = options["round_tiles"], options["relief"], options["assembly"]
    painted = _saved_edit(session_id, style)
    try:
        canvas = build_canvas(size, path, style=style, heights=painted, **options)
    except DepthError as exc:
        raise ImageError(_("Relief par profondeur impossible : {reason}").format(reason=exc)) from exc
    rows, cols = canvas.shape
    distribution = canvas.get_distribution()
    layout = canvas.layout(plates, round_tiles)
    parts = layout.parts(canvas.get_brick_ids())
    np.savez_compressed(
        path_in_session(session_id, f"mosaic-{style}.npz"),
        colors=canvas.to_pixel_array(),
        regions=layout.regions,
        heights=layout.heights,
        border=canvas.border_mask(),
        assembly=assembly,
        plates=np.array([[p.row, p.col, p.height, p.width] for p in layout.plates], dtype=int).reshape(-1, 4),
        supports=np.array(
            [
                [number, p.row, p.col, p.height, p.width]
                for number, layer in enumerate(layout.supports, start=1)
                for p in layer
            ],
            dtype=int,
        ).reshape(-1, 5),
    )
    price = canvas.get_price(baseplates, currency, parts)
    image = canvas.to_preview_image(round_tiles) if relief == "flat" else canvas.to_realistic_image(plates, round_tiles)
    return {
        "style": style,
        "label": label,
        "image": _save_preview(session_id, style, image),
        "round_tiles": round_tiles,
        "relief": relief,
        "relief_dropped": relief != "flat" and painted is not None and canvas.painted_heights is None,
        "smooth": round_tiles or relief != "flat",
        "support_count": layout.support_count,
        "assembly": assembly,
        "assembly_count": layout.assembly_count,
        "priced_baseplates": price.baseplate_count,
        "studs": f"{cols} × {rows}",
        "baseplates": canvas.baseplate_count,
        "colors": len(distribution),
        "bricks": sum(distribution.values()),
        "plates": sum(parts.values()) - layout.assembly_count,
        "round_tile_count": sum(count for (_, part), count in parts.items() if part == ROUND_TILE),
        "distribution": distribution,
        "parts": [[brick_id, part, count] for (brick_id, part), count in parts.items()],
        "wanted_list": wanted_list_xml(distribution, parts),
        **_price_fields(price),
        "rare_colors": list(price.rare_colors),
    }


def realistic_png(session_id: str, style: str) -> bytes:
    """Return the mosaic ``render_styles`` last made for a session and style, drawn
    as tiles, as PNG file bytes."""
    if style not in STYLE_LABELS:
        raise ImageError(_("Style inconnu."))
    mosaic = path_in_session(session_id, f"mosaic-{style}.npz")
    if not mosaic.is_file():
        raise ImageError(_("Cette mosaïque n'a pas encore été générée."))
    with np.load(mosaic) as saved:
        pixels = render_mosaic(saved["colors"], saved["regions"], saved["heights"])
    ok, png = cv2.imencode(".png", cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR))
    if not ok:
        raise ImageError(_("Impossible de dessiner cette mosaïque."))
    return png.tobytes()


def model_data(session_id: str, style: str) -> dict[str, object]:
    """Return the pieces of the mosaic ``render_styles`` last made for a session and
    style, for the 3D view. Grids are flat, row by row, to keep this small."""
    if style not in STYLE_LABELS:
        raise ImageError(_("Style inconnu."))
    mosaic = path_in_session(session_id, f"mosaic-{style}.npz")
    if not mosaic.is_file():
        raise ImageError(_("Cette mosaïque n'a pas encore été générée."))
    with np.load(mosaic) as saved:
        rows, cols = saved["regions"].shape
        palette, colors = np.unique(saved["colors"].reshape(-1, 3), axis=0, return_inverse=True)
        metallic = {tuple(COLORS[name]) for name in ACCENT_COLORS}
        return {
            "rows": rows,
            "cols": cols,
            "assembly": str(saved["assembly"]),
            "palette": [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in palette.tolist()],
            "metallic": [i for i, color in enumerate(palette.tolist()) if tuple(color) in metallic],
            "colors": colors.ravel().tolist(),
            "heights": saved["heights"].ravel().tolist(),
            "regions": saved["regions"].ravel().tolist(),
            "plates": saved["plates"].ravel().tolist(),
            "supports": saved["supports"].ravel().tolist(),
        }


def relief_data(session_id: str, style: str) -> dict[str, object]:
    """Return what the relief editor shows for a rendered mosaic."""
    if style not in STYLE_LABELS:
        raise ImageError(_("Style inconnu."))
    mosaic = path_in_session(session_id, f"mosaic-{style}.npz")
    if not mosaic.is_file():
        raise ImageError(_("Cette mosaïque n'a pas encore été générée."))
    with np.load(mosaic) as saved:
        rows, cols = saved["heights"].shape
        palette, colors = np.unique(saved["colors"].reshape(-1, 3), axis=0, return_inverse=True)
        return {
            "rows": rows,
            "cols": cols,
            "max_height": MAX_PLATES,
            "palette": [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in palette.tolist()],
            "colors": colors.ravel().tolist(),
            "heights": saved["heights"].ravel().tolist(),
            "border": saved["border"].ravel().astype(int).tolist(),
        }


def relief_edit_path(session_id: str, style: str) -> Path:
    return path_in_session(session_id, f"relief-{style}.npy")


def _saved_edit(session_id: str, style: str) -> np.ndarray | None:
    """Return the relief painted for a style, if any."""
    path = relief_edit_path(session_id, style)
    return np.load(path) if path.is_file() else None


def save_relief_edit(session_id: str, style: str, heights: object) -> None:
    """
    Keep a painted relief for a style, for its next render and guide. Heights are
    limited to what a relief can stand on, the border stays flat, and what is too
    thin to stand up is removed.

    :param heights: The plates under each stud, flat, row by row, as the editor sends it.
    :raises ImageError: if it doesn't fit the mosaic.
    """
    if style not in STYLE_LABELS:
        raise ImageError(_("Style inconnu."))
    mosaic = path_in_session(session_id, f"mosaic-{style}.npz")
    if not mosaic.is_file():
        raise ImageError(_("Cette mosaïque n'a pas encore été générée."))
    with np.load(mosaic) as saved:
        shape, border = saved["heights"].shape, saved["border"]
    try:
        grid = np.asarray(heights, dtype=int).reshape(shape)
    except (TypeError, ValueError) as exc:
        raise ImageError(_("Ce relief ne correspond pas à la mosaïque.")) from exc
    grid = np.clip(grid, 0, MAX_PLATES)
    grid[border] = 0
    np.save(relief_edit_path(session_id, style), clean_heights(grid))


def mosaic_key(session_id: str, settings: dict[str, object]) -> dict[str, object]:
    """Return what makes a mosaic the same one: not how it is priced or printed, which
    a painted relief doesn't depend on."""
    ignored = {"baseplates", "currency", "tinted", "improved_visibility"}
    return {"session": session_id, **{key: value for key, value in settings.items() if key not in ignored}}


def _set_aside_path(session_id: str, style: str) -> Path:
    return path_in_session(session_id, f"relief-{style}-set-aside.npy")


def clear_relief_edits(session_id: str) -> bool:
    """Set aside every relief painted for a session: a new render starts afresh, but
    ``restore_relief_edits`` can bring them back. Return whether there were any."""
    found = False
    for style in STYLE_LABELS:
        path = relief_edit_path(session_id, style)
        if path.is_file():
            path.replace(_set_aside_path(session_id, style))
            found = True
    return found


def restore_relief_edits(session_id: str) -> None:
    """Bring back the reliefs last set aside. One that no longer fits the mosaic, after
    a change of size, is left out of the render (see ``Canvas.fit_from_image``)."""
    for style in STYLE_LABELS:
        path = _set_aside_path(session_id, style)
        if path.is_file():
            path.replace(relief_edit_path(session_id, style))


def build_pdf(
    session_id: str,
    size: int,
    frame: int,
    max_colors: int | None,
    style: str,
    crop: Crop | list[float] | None = None,
    baseplates: str = "official",
    currency: str = "EUR",
    palette: str = DEFAULT_PALETTE,
    dither: bool = False,
    plates: Iterable[str] = PLATE_SIZES,
    tinted: bool = True,
    round_tiles: bool = False,
    frame_color: str = DEFAULT_FRAME_COLOR,
    relief: str = DEFAULT_RELIEF,
    assembly: str = DEFAULT_ASSEMBLY,
    sharp: bool = False,
    accents: bool = False,
    improved_visibility: bool = False,
) -> Path:
    """Write the build guide for one style as a PDF and return its path."""
    if style not in STYLE_LABELS:
        raise ImageError(_("Style inconnu."))
    # Printing starts a full browser: it waits for its turn.
    with _HeavyWork():
        return _build_pdf(
            session_id,
            style,
            size,
            {
                "frame": frame,
                "frame_color": frame_color,
                "max_colors": max_colors,
                "palette": palette,
                "dither": dither,
                "round_tiles": round_tiles,
                "relief": relief,
                "assembly": assembly,
                "sharp": sharp,
                "accents": accents and round_tiles,
            },
            crop,
            {
                "baseplates": baseplates,
                "currency": currency,
                "tinted": tinted,
                "improved_visibility": improved_visibility,
                "plates": plates,
                "round_tiles": round_tiles,
            },
        )


def _build_pdf(
    session_id: str,
    style: str,
    size: int,
    options: dict[str, object],
    crop: Crop | list[float] | None,
    guide: dict[str, object],
) -> Path:
    path = _working_image(session_id, crop)
    canvas = build_canvas(size, path, style=style, heights=_saved_edit(session_id, style), **options)
    creator = PdfCreator(canvas, lang=current_language(), **guide)
    return creator.generate_pdf(output_dir=source_path(session_id).parent / f"guide-{style}")


def built_pdf(session_id: str, style: str) -> Path:
    """Return the PDF ``build_pdf`` last wrote for a session and style."""
    if style not in STYLE_LABELS:
        raise ImageError(_("Style inconnu."))
    path = source_path(session_id)
    pdf = path.parent / f"guide-{style}" / f"{path.stem}.pdf"
    if not pdf.is_file():
        raise ImageError(_("Ce guide n'a pas encore été généré."))
    return pdf
