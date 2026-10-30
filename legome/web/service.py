"""Everything the web app does, minus the UI: fetching and storing the source
image, rendering one preview per style, and building the PDF guide.

Sources are kept server-side, one directory per upload under ``SESSION_ROOT``.
The browser only ever holds the opaque session id, never a file path, so it
can't point the app at an arbitrary file on the server.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
import shutil
import socket
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np

from legome.models.pdf_creator import PdfCreator, data_uri_from_rgb_array
from legome.models.utils import read_image
from legome.process import build_canvas

SESSION_ROOT = Path(tempfile.gettempdir()) / "legome-web"
# Sessions older than this are deleted whenever a new image comes in.
SESSION_TTL_SECONDS = 24 * 3600
MAX_IMAGE_BYTES = 15 * 1024 * 1024
URL_TIMEOUT_SECONDS = 10

# Styles offered side by side, in display order, with their French labels.
STYLE_LABELS = {"natural": "Naturel", "pop": "Pop art", "comic": "BD"}

_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
# A crop narrower or shorter than this share of the image is a stray click.
MIN_CROP_FRACTION = 0.04

# (left, top, right, bottom) as fractions of the image's width and height.
Crop = tuple[float, float, float, float]


class ImageError(ValueError):
    """The image couldn't be fetched or read. The message is shown to the user."""


def image_bytes_from_upload(contents: str) -> bytes:
    """Decode the ``data:<mime>;base64,<data>`` string a Dash upload produces."""
    _, _, encoded = contents.partition(",")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageError("Le fichier envoyé est illisible.") from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError(f"Image trop lourde (max {MAX_IMAGE_BYTES // 2**20} Mo).")
    return data


def _is_public_host(host: str) -> bool:
    """Whether every address ``host`` resolves to is on the public internet, so
    a link can't be used to probe the server's own network."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


def image_bytes_from_url(url: str) -> bytes:
    """Download an image from an http(s) link, up to ``MAX_IMAGE_BYTES``."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ImageError("Le lien doit commencer par http:// ou https://.")
    if not _is_public_host(parsed.hostname):
        raise ImageError("Ce lien pointe vers une adresse inaccessible.")

    request = urllib.request.Request(url.strip(), headers={"User-Agent": "LegoMe/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=URL_TIMEOUT_SECONDS) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ImageError("Impossible de télécharger l'image depuis ce lien.") from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError(f"Image trop lourde (max {MAX_IMAGE_BYTES // 2**20} Mo).")
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
    Check that ``data`` is an image, store it as PNG in a new session directory
    and return the session id.

    :param data: Raw image file bytes (PNG, JPEG, WebP...).
    :param name: Original file name or URL, used to title the guide.
    """
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ImageError("Ce fichier n'est pas une image reconnue (PNG, JPEG, WebP…).")

    _purge_old_sessions(time.time())
    session_id = uuid.uuid4().hex
    session = SESSION_ROOT / session_id
    session.mkdir(parents=True)
    ok, png = cv2.imencode(".png", img)
    if not ok:
        raise ImageError("Impossible d'enregistrer cette image.")
    # Written via Python, not cv2.imwrite, which fails on non-ASCII paths on Windows.
    (session / f"{_safe_stem(name)}.png").write_bytes(png.tobytes())
    return session_id


def source_path(session_id: str) -> Path:
    """Return the stored source image for a session id."""
    if not isinstance(session_id, str) or not _SESSION_ID.match(session_id):
        raise ImageError("Session invalide.")
    images = sorted((SESSION_ROOT / session_id).glob("*.png"))
    if not images:
        raise ImageError("Image introuvable : elle a peut-être expiré, recharge-la.")
    return images[0]


def preview(path: Path, max_dimension: int = 640) -> tuple[str, int, int]:
    """A small PNG ``data:`` URI of the source image for the page, with its
    width and height in pixels."""
    img = cv2.cvtColor(read_image(path), cv2.COLOR_BGR2RGB)
    scale = min(1.0, max_dimension / max(img.shape[:2]))
    img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
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
        raise ImageError("Impossible de rogner cette image.")
    target = path.parent / "cropped" / path.name
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(png.tobytes())
    return target


def render_styles(
    session_id: str,
    size: int,
    frame: int,
    max_colors: int | None,
    crop: Crop | list[float] | None = None,
    baseplates: str = "official",
    currency: str = "EUR",
) -> list[dict[str, object]]:
    """Fit the session's image (cropped to ``crop``, if given) once per style in
    ``STYLE_LABELS`` and return, for each, its preview image, build figures and
    price estimate (see ``pricing.estimate_price``)."""
    path = _working_image(session_id, crop)
    results = []
    for style, label in STYLE_LABELS.items():
        canvas = build_canvas(size, path, frame=frame, max_colors=max_colors, style=style)
        rows, cols = canvas.shape
        distribution = canvas.get_distribution()
        price = canvas.get_price(baseplates, currency)
        results.append(
            {
                "style": style,
                "label": label,
                "image": data_uri_from_rgb_array(canvas.to_preview_image()),
                "studs": f"{cols} × {rows}",
                "baseplates": canvas.baseplate_count,
                "colors": len(distribution),
                "bricks": sum(distribution.values()),
                "price": price.format(),
                "price_bricks": f"{price.format(price.bricks_low)} – {price.format(price.bricks_high)}",
                "price_baseplates": price.format(price.baseplates),
                "rare_colors": list(price.rare_colors),
            }
        )
    return results


def build_pdf(
    session_id: str,
    size: int,
    frame: int,
    max_colors: int | None,
    style: str,
    crop: Crop | list[float] | None = None,
    baseplates: str = "official",
    currency: str = "EUR",
) -> Path:
    """Write the build guide for one style as a PDF and return its path."""
    if style not in STYLE_LABELS:
        raise ImageError("Style inconnu.")
    path = _working_image(session_id, crop)
    canvas = build_canvas(size, path, frame=frame, max_colors=max_colors, style=style)
    creator = PdfCreator(canvas, baseplates=baseplates, currency=currency)
    return creator.generate_pdf(output_dir=source_path(session_id).parent / f"guide-{style}")


def built_pdf(session_id: str, style: str) -> Path:
    """Return the PDF ``build_pdf`` last wrote for a session and style."""
    if style not in STYLE_LABELS:
        raise ImageError("Style inconnu.")
    path = source_path(session_id)
    pdf = path.parent / f"guide-{style}" / f"{path.stem}.pdf"
    if not pdf.is_file():
        raise ImageError("Ce guide n'a pas encore été généré.")
    return pdf
