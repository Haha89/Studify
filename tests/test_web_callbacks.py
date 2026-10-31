"""The web app's callbacks, fired through Dash's real endpoint: this checks their
wiring (which inputs reach them, which outputs they fill), not just their logic."""

from __future__ import annotations

import base64
import json

import cv2
import numpy as np
import pytest

pytest.importorskip("dash")

from legome.models import pdf_creator  # noqa: E402
from legome.web import service  # noqa: E402
from legome.web.app import create_app  # noqa: E402

STYLES = list(service.STYLE_LABELS)


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(autouse=True)
def session_root(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "SESSION_ROOT", tmp_path / "sessions")


@pytest.fixture
def png_bytes():
    img = np.zeros((40, 80, 3), dtype=np.uint8)
    img[:, 40:] = (0, 0, 255)
    ok, data = cv2.imencode(".png", img)
    assert ok
    return data.tobytes()


@pytest.fixture
def source(png_bytes):
    return {"session": service.save_source(png_bytes, "flag.png"), "name": "flag.png", "width": 80, "height": 40}


@pytest.fixture
def fire(app):
    """Fire a callback by its function's name, as the page would. ``values`` follow
    its inputs then its state; a wildcard input takes one value per style. Returns
    {component id: {property: value}}, or None if the callback updated nothing."""
    client = app.server.test_client()
    specs = {spec["callback"].__name__: (key, spec) for key, spec in app.callback_map.items() if "callback" in spec}

    def expand(component_id, prop, value=None):
        if not component_id.startswith("{"):
            return {"id": component_id, "property": prop, "value": value}
        kind = json.loads(component_id)["type"]
        ids = [{"type": kind, "index": style} for style in STYLES]
        values = ids if prop == "id" or value is None else value  # None: an output, which has no value yet.
        return [{"id": id_, "property": prop, "value": v} for id_, v in zip(ids, values, strict=True)]

    def _fire(name, values, trigger):
        key, spec = specs[name]
        outputs = [part.rsplit(".", 1) for part in key.strip(".").split("...")]
        expanded = [expand(o[0], o[1]) for o in outputs]
        for entry in [e for group in expanded for e in (group if isinstance(group, list) else [group])]:
            entry.pop("value")
        wanted = [*spec["inputs"], *spec["state"]]
        args = [expand(item["id"], item["property"], value) for item, value in zip(wanted, values, strict=True)]
        response = client.post(
            "/_dash-update-component",
            json={
                "output": key,
                "outputs": expanded if key.startswith("..") else expanded[0],
                "inputs": args[: len(spec["inputs"])],
                "state": args[len(spec["inputs"]) :],
                "changedPropIds": [trigger],
            },
        )
        assert response.status_code in (200, 204), response.text
        return (response.get_json()["response"] or None) if response.status_code == 200 else None

    return _fire


def wildcard(kind: str, style: str) -> str:
    return json.dumps({"index": style, "type": kind}, separators=(",", ":"), sort_keys=True)


def generate(fire, source, baseplates="official", currency="EUR", max_colors="0", crop=None, palette="buyable"):
    return fire(
        "generate_styles",
        [1, source, crop, palette, baseplates, currency, 1, 0, max_colors],
        "generate.n_clicks",
    )


class TestLoadSource:
    def test_an_upload_starts_a_session_and_shows_the_crop_area(self, fire, png_bytes):
        contents = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
        out = fire("load_source", [contents, None, None, "flag.png"], "upload.contents")

        source = out["source"]["data"]
        assert service.source_path(source["session"]).name == "flag.png"
        assert (source["width"], source["height"]) == (80, 40)
        assert out["crop-area"]["style"] == {}
        assert "flag.png" in str(out["source-status"]["children"])

    def test_a_bad_upload_shows_an_error_and_keeps_the_current_image(self, fire):
        out = fire("load_source", ["data:image/png;base64,bm90IGFuIGltYWdl", None, None, "x.png"], "upload.contents")
        assert list(out) == ["source-status"]
        assert "pas une image" in str(out["source-status"]["children"])

    def test_a_link_to_a_private_address_shows_an_error(self, fire):
        out = fire("load_source", [None, 1, "http://127.0.0.1/a.png", None], "load-url.n_clicks")
        assert list(out) == ["source-status"]
        assert "inaccessible" in str(out["source-status"]["children"])

    def test_a_link_is_named_after_its_file(self, fire, png_bytes, monkeypatch):
        monkeypatch.setattr(service, "image_bytes_from_url", lambda url: png_bytes)
        out = fire("load_source", [None, 1, "https://example.com/pics/dog.png?w=1", None], "load-url.n_clicks")
        assert out["source"]["data"]["name"] == "dog.png"

    def test_nothing_to_load_updates_nothing(self, fire):
        assert fire("load_source", [None, 1, None, None], "load-url.n_clicks") is None


class TestUpdateCrop:
    def test_drawing_a_box_stores_it_as_fractions(self, fire, source):
        relayout = {"shapes": [{"x0": 19.5, "y0": 9.5, "x1": 59.5, "y1": 29.5}]}
        out = fire("update_crop", [relayout, "free", None, {}, source], "crop-graph.relayoutData")
        assert out["crop"]["data"] == [0.25, 0.25, 0.75, 0.75]

    def test_reset_clears_the_crop(self, fire, source):
        figure = {"layout": {"shapes": [{"x0": 0, "y0": 0, "x1": 10, "y1": 10}]}}
        out = fire("update_crop", [None, "free", 1, figure, source], "crop-reset.n_clicks")
        assert out["crop"]["data"] is None

    def test_an_unrelated_relayout_updates_nothing(self, fire, source):
        assert fire("update_crop", [{"autosize": True}, "free", None, {}, source], "crop-graph.relayoutData") is None

    def test_without_an_image_updates_nothing(self, fire):
        assert fire("update_crop", [None, "free", 1, {}, None], "crop-reset.n_clicks") is None


def test_describe_crop(fire):
    assert fire("describe_crop", [None], "crop.data")["crop-status"]["children"] == "Image entière."
    described = fire("describe_crop", [[0.25, 0, 0.75, 0.5]], "crop.data")["crop-status"]["children"]
    assert "50 % de la largeur" in described and "50 % de la hauteur" in described


def test_generate_is_enabled_once_there_is_an_image(fire, source):
    assert fire("enable_generate", [None], "source.data")["generate"]["disabled"] is True
    assert fire("enable_generate", [source], "source.data")["generate"]["disabled"] is False


class TestGenerateStyles:
    def test_only_what_changes_the_mosaics_renders_again(self, app):
        spec = next(
            s for s in app.callback_map.values() if "callback" in s and s["callback"].__name__ == "generate_styles"
        )
        triggers = {item["id"] for item in spec["inputs"]}
        assert triggers == {"generate", "source", "crop", "palette"}  # Not currency or baseplates: see update_prices.

    def test_draws_a_card_per_style_and_remembers_the_settings(self, fire, source):
        out = generate(fire, source, currency="USD", max_colors="8", crop=[0, 0, 0.5, 1])

        cards = str(out["styles"]["children"])
        for label in service.STYLE_LABELS.values():
            assert label in cards
        settings = out["settings"]["data"]
        assert settings["currency"] == "USD" and settings["max_colors"] == 8 and settings["crop"] == [0, 0, 0.5, 1]
        assert out["selected-style"]["data"] is None
        assert set(out["builds"]["data"]) == set(STYLES)

    def test_no_colour_limit_is_stored_as_none(self, fire, source):
        assert generate(fire, source)["settings"]["data"]["max_colors"] is None

    def test_an_expired_image_shows_an_error(self, fire):
        out = generate(fire, {"session": "0" * 32})
        assert "introuvable" in str(out["styles"]["children"])
        assert out["settings"]["data"] is None and out["builds"]["data"] is None

    def test_without_an_image_updates_nothing(self, fire):
        assert generate(fire, None) is None


class TestUpdatePrices:
    def test_reprices_every_card_and_the_settings_for_the_pdf(self, fire, source):
        built = generate(fire, source)
        settings, builds = built["settings"]["data"], built["builds"]["data"]

        out = fire(
            "update_prices",
            ["compatible", "USD", settings, builds, None],
            "currency.value",
        )

        fresh = service.render_styles(
            source["session"], size=1, frame=0, max_colors=None, baseplates="compatible", currency="USD"
        )
        for result in fresh:
            assert out[wildcard("style-price", result["style"])]["children"] == result["price"]
            assert result["price_baseplates"] in out[wildcard("style-price-detail", result["style"])]["children"]
        assert out["settings"]["data"] == {**settings, "baseplates": "compatible", "currency": "USD"}

    def test_before_any_render_updates_nothing(self, fire):
        assert fire("update_prices", ["official", "USD", None, None, None], "currency.value") is None


class TestSelection:
    def test_clicking_a_card_selects_its_style(self, fire):
        trigger = wildcard("style-card", "pop") + ".n_clicks"
        assert fire("select_style", [[0, 1, 0]], trigger)["selected-style"]["data"] == "pop"

    def test_redrawn_cards_select_nothing(self, fire):
        assert fire("select_style", [[0, 0, 0]], wildcard("style-card", "pop") + ".n_clicks") is None

    def test_highlights_the_selected_card_and_enables_the_download(self, fire):
        out = fire("highlight_selection", ["pop", None], "selected-style.data")
        assert out[wildcard("style-card", "pop")]["className"] == "style-card selected"
        assert out[wildcard("style-card", "comic")]["className"] == "style-card"
        assert out["download-pdf"]["disabled"] is False

    def test_no_selection_keeps_the_download_disabled(self, fire):
        assert fire("highlight_selection", [None, None], "selected-style.data")["download-pdf"]["disabled"] is True


class TestDownloadPdf:
    @pytest.fixture
    def printed(self, monkeypatch):
        """Stand in for the browser; collects each guide's HTML as it is printed."""
        guides = []

        def fake_html_to_pdf(html, pdf):
            guides.append(html.read_text(encoding="utf-8"))
            pdf.write_bytes(b"%PDF-fake")
            return pdf

        monkeypatch.setattr(pdf_creator, "html_to_pdf", fake_html_to_pdf)
        return guides

    def test_links_to_the_built_guide(self, fire, source, printed):
        settings = generate(fire, source)["settings"]["data"]
        out = fire("download_pdf", [1, source, settings, "pop"], "download-pdf.n_clicks")

        assert out["pdf-url"]["data"].startswith(f"/guide/{source['session']}/pop.pdf?v=")
        assert service.built_pdf(source["session"], "pop").read_bytes() == b"%PDF-fake"

    def test_the_guide_uses_prices_changed_after_the_render(self, fire, source, printed):
        built = generate(fire, source)
        repriced = fire(
            "update_prices",
            ["compatible", "USD", built["settings"]["data"], built["builds"]["data"], None],
            "currency.value",
        )
        fire("download_pdf", [1, source, repriced["settings"]["data"], "pop"], "download-pdf.n_clicks")

        assert "compatible baseplates" in printed[0] and "$" in printed[0]

    def test_a_printing_failure_shows_an_error(self, fire, source, monkeypatch):
        def no_browser(html, pdf):
            raise RuntimeError("no browser here")

        monkeypatch.setattr(pdf_creator, "html_to_pdf", no_browser)
        settings = generate(fire, source)["settings"]["data"]
        out = fire("download_pdf", [1, source, settings, "pop"], "download-pdf.n_clicks")
        assert list(out) == ["pdf-status"]
        assert "no browser here" in str(out["pdf-status"]["children"])

    def test_without_a_selected_style_updates_nothing(self, fire, source):
        assert fire("download_pdf", [1, source, {"size": 1}, None], "download-pdf.n_clicks") is None
