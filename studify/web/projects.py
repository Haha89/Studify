"""Saved projects: a picture, the settings its mosaics were made with, the chosen style
and any painted relief, kept under a link that can be opened again or shared.

A project is a snapshot: opening it copies it into a new session, so whoever opens a
link never changes it. Its id is a long random token, so the link is the only way to
it, and only whoever saved it holds the token that deletes it. Projects are kept
``PROJECT_TTL_SECONDS`` after they were last opened.

Settings are stored as the page sent them: the app checks them when a project is
opened (see ``app.controls_from_settings``), as the format may have changed since.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

import cv2
import numpy as np

from studify.models.depth import MAX_PLATES
from studify.models.static import DIM_BASE
from studify.models.utils import fit_within, read_image
from studify.web import service
from studify.web.i18n import _

# Environment variable naming where to keep projects, overriding ~/.local/share/studify.
DATA_ENV = "STUDIFY_DATA"
PROJECT_TTL_SECONDS = 90 * 24 * 3600
# Room all projects may take together, beyond which saving is refused.
MAX_PROJECTS_BYTES = 2 * 1024**3
# Larger than any settings the page sends: anything bigger isn't from it.
MAX_SETTINGS_BYTES = 8 * 1024
MAX_NAME_LENGTH = 100
THUMBNAIL_SIZE = 160
# Studs along a mosaic's longest side, at its largest.
MAX_STUDS = 6 * DIM_BASE
FORMAT_VERSION = 1

PROJECT_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")
_META = "project.json"
_SOURCE = "source.png"


class ProjectError(service.ImageError):
    """The project can't be saved, opened or deleted. The message is shown to the user."""


def project_root() -> Path:
    """Return where projects are kept."""
    return Path(os.environ.get(DATA_ENV) or Path.home() / ".local" / "share" / "studify") / "projects"


def _folder(project_id: object) -> Path:
    """Return a project's folder, if it has been saved and not deleted."""
    if not isinstance(project_id, str) or not PROJECT_ID.match(project_id):
        raise ProjectError(_("Lien de projet invalide."))
    folder = project_root() / project_id
    if not (folder / _META).is_file():
        raise ProjectError(_("Projet introuvable : il a peut-être expiré ou été supprimé."))
    return folder


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _purge(now: float) -> None:
    """Delete projects not opened for ``PROJECT_TTL_SECONDS``, and any left half-written."""
    root = project_root()
    if not root.is_dir():
        return
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        meta = folder / _META
        last_used = meta.stat().st_mtime if meta.is_file() else folder.stat().st_mtime
        if now - last_used > PROJECT_TTL_SECONDS:
            shutil.rmtree(folder, ignore_errors=True)


def _used_bytes() -> int:
    root = project_root()
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) if root.is_dir() else 0


def _thumbnail(session_id: str, style: str | None) -> str:
    """A small JPEG ``data:`` URI of the chosen style's preview, else of the picture."""
    preview = service.path_in_session(session_id, f"preview-{style or 'natural'}.png")
    img = fit_within(read_image(preview if preview.is_file() else service.source_path(session_id)), THUMBNAIL_SIZE)
    ok, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise ProjectError(_("Impossible d'enregistrer ce projet."))
    return f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode('ascii')}"


def save_project(session_id: str, name: str, settings: dict[str, object], selected: str | None) -> dict[str, object]:
    """
    Keep a session's picture and painted reliefs, with the settings its mosaics were
    made with and the chosen style, as a new project.

    :param name: The picture's original name, which titles the project and its guide.
    :param settings: The settings, already checked by the app.
    :return: The project's id, the token that deletes it, its name, when it was saved,
        and a small preview to list it by.
    """
    source = service.source_path(session_id)
    if selected not in service.STYLE_LABELS:
        selected = None
    if len(json.dumps(settings)) > MAX_SETTINGS_BYTES:
        raise ProjectError(_("Impossible d'enregistrer ce projet."))
    now = time.time()
    _purge(now)
    if _used_bytes() > MAX_PROJECTS_BYTES:
        raise ProjectError(_("Plus de place pour enregistrer des projets : réessaie plus tard."))

    project_id, token = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    name = str(name)[:MAX_NAME_LENGTH] or "image"
    # Written aside, then moved in place: a project is never seen half-saved.
    partial = project_root() / f".{project_id}"
    partial.mkdir(parents=True)
    shutil.copyfile(source, partial / _SOURCE)
    for style in service.STYLE_LABELS:
        edit = service.relief_edit_path(session_id, style)
        if edit.is_file():
            shutil.copyfile(edit, partial / edit.name)
    meta = {
        "version": FORMAT_VERSION,
        "name": name,
        "saved": now,
        "settings": settings,
        "selected": selected,
        "delete_hash": _token_hash(token),
    }
    (partial / _META).write_text(json.dumps(meta), encoding="utf-8")
    partial.rename(project_root() / project_id)
    return {"id": project_id, "token": token, "name": name, "saved": now, "thumbnail": _thumbnail(session_id, selected)}


def read_project(project_id: object) -> dict[str, object]:
    """Return what a project was saved with: its name, settings and chosen style."""
    try:
        meta = json.loads((_folder(project_id) / _META).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProjectError(_("Projet introuvable : il a peut-être expiré ou été supprimé.")) from exc
    if not isinstance(meta, dict):
        raise ProjectError(_("Projet introuvable : il a peut-être expiré ou été supprimé."))
    settings = meta.get("settings")
    return {
        "name": str(meta.get("name") or "image")[:MAX_NAME_LENGTH],
        "settings": settings if isinstance(settings, dict) else {},
        "selected": meta.get("selected") if meta.get("selected") in service.STYLE_LABELS else None,
    }


def _clean_relief(path: Path) -> np.ndarray | None:
    """Return a saved relief if it is a grid of plate counts a mosaic could have."""
    try:
        heights = np.load(path, allow_pickle=False)
    except (OSError, ValueError):
        return None
    if heights.ndim != 2 or heights.dtype.kind not in "iu" or not all(0 < side <= MAX_STUDS for side in heights.shape):
        return None
    return np.clip(heights, 0, MAX_PLATES).astype(int)


def open_project(project_id: object) -> tuple[str, dict[str, object]]:
    """
    Copy a project into a new session, and return the session's id and what
    ``read_project`` returns. Opening a project keeps it for another ``PROJECT_TTL_SECONDS``.
    """
    meta = read_project(project_id)
    folder = _folder(project_id)
    try:
        png = (folder / _SOURCE).read_bytes()
    except OSError as exc:
        raise ProjectError(_("Projet introuvable : il a peut-être expiré ou été supprimé.")) from exc
    if service.image_size(png) is None:
        raise ProjectError(_("Projet introuvable : il a peut-être expiré ou été supprimé."))
    session_id = service.new_session(png, meta["name"])
    for style in service.STYLE_LABELS:
        relief = _clean_relief(folder / f"relief-{style}.npy")
        if relief is not None:
            np.save(service.relief_edit_path(session_id, style), relief)
    os.utime(folder / _META)
    return session_id, meta


def delete_project(project_id: object, token: object) -> bool:
    """Delete a project, given the token ``save_project`` returned. Return whether it was."""
    try:
        folder = _folder(project_id)
        meta = json.loads((folder / _META).read_text(encoding="utf-8"))
    except (ProjectError, OSError, ValueError):
        return False
    if not isinstance(token, str) or not hmac.compare_digest(str(meta.get("delete_hash", "")), _token_hash(token)):
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True


def kept(project_ids: list[object]) -> set[str]:
    """Return which of these projects are still kept."""
    found = set()
    for project_id in project_ids:
        try:
            _folder(project_id)
        except ProjectError:
            continue
        found.add(project_id)
    return found
