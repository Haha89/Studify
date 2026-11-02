"""Nothing in the web app should show up in French to someone who chose English."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytest.importorskip("dash")

from studify.web import app, i18n, service  # noqa: E402

FILES = [Path(app.__file__), Path(service.__file__)]
# Sentences of the page, which callbacks build or which a user reads, have these.
FRENCH = re.compile(r"[éèêàçùôîœ]|\b(le|la|les|des|une?|du|sur|avec|pour|dans|et|ou|tes?|ton)\b", re.IGNORECASE)
IGNORED_CALLS = {"Dash", "print", "add_argument", "ArgumentParser", "clientside_callback"}


def tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_every_wrapped_text_has_an_english_version(path):
    missing = [
        node.args[0].value
        for node in ast.walk(tree(path))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_"
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value not in i18n.ENGLISH
    ]
    assert not missing


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_no_french_text_is_left_unwrapped(path):
    root = tree(path)
    wrapped = set()
    skipped = set()
    for node in ast.walk(root):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name == "_":
                wrapped.update(id(arg) for arg in node.args)
            if name in IGNORED_CALLS:
                skipped.update(id(child) for child in ast.walk(node))
        if isinstance(node, ast.FunctionDef | ast.Module | ast.ClassDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr):
                skipped.add(id(first.value))
    found = [
        node.value
        for function in ast.walk(root)
        if isinstance(function, ast.FunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in wrapped
        and id(node) not in skipped
        and " " in node.value
        and FRENCH.search(node.value)
    ]
    assert not found


def test_every_choice_can_be_translated():
    for choices in (
        app.FRAME_COLOR_CHOICES,
        app.MODE_CHOICES,
        app.ASSEMBLY_CHOICES,
        app.BASEPLATE_CHOICES,
        app.PALETTE_CHOICES,
        app.CROP_RATIOS,
    ):
        for label, _value in choices:
            assert label in i18n.ENGLISH or label[0].isdigit()
    assert all(label in i18n.ENGLISH for label in service.STYLE_LABELS.values())
