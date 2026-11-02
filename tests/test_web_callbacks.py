"""The web app's callbacks, fired through Dash's real endpoint: this checks their
wiring (which inputs reach them, which outputs they fill), not just their logic."""

from __future__ import annotations

import base64
import json

import cv2
import numpy as np
import pytest

pytest.importorskip("dash")

from studify.models import depth, pdf_creator  # noqa: E402
from studify.web import service  # noqa: E402
from studify.web.app import create_app  # noqa: E402

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


def generate(
    fire,
    source,
    baseplates="official",
    currency="EUR",
    max_colors="0",
    crop=None,
    palette="buyable",
    dither=False,
    plates=("1x1", "1x2", "1x3", "1x4", "2x2"),
    round_tiles=False,
    frame=0,
    frame_color="black",
    relief="flat",
    assembly="baseplates",
    sharp=False,
    accents=False,
):
    return fire(
        "generate_styles",
        [
            1,
            source,
            crop,
            palette,
            baseplates,
            currency,
            1,
            frame,
            max_colors,
            dither,
            plates,
            True,
            frame_color,
            "round" if round_tiles else {"stepped": "relief", "depth": "depth"}.get(relief, "flat"),
            assembly,
            sharp,
            accents,
        ],
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


class TestEnlarge:
    def test_shows_the_clicked_style(self, fire, source):
        previews = generate(fire, source)["previews"]["data"]
        out = fire("enlarge_style", [[0, 1, 0], previews], wildcard("style-enlarge", "pop") + ".n_clicks")
        assert out["preview-modal"] == {"opened": True, "title": "Pop art"}
        assert out["preview-image"]["src"] == previews["pop"]["image"]

    def test_redrawn_cards_open_nothing(self, fire, source):
        previews = generate(fire, source)["previews"]["data"]
        assert fire("enlarge_style", [[0, 0, 0], previews], wildcard("style-enlarge", "pop") + ".n_clicks") is None


class TestRealistic:
    def test_shows_the_clicked_style(self, fire, source):
        out = fire("show_realistic", [[0, 1, 0], source, None], wildcard("style-realistic", "pop") + ".n_clicks")
        assert out["realistic-modal"]["opened"] is True
        assert "Pop art" in out["realistic-modal"]["title"]
        assert out["realistic-image"]["src"].startswith(f"/realistic/{source['session']}/pop.png?v=")
        assert "Tuiles lisses" in out["realistic-note"]["children"]

    def test_says_when_the_build_really_is_round_tiles(self, fire, source):
        settings = generate(fire, source, round_tiles=True)["settings"]["data"]
        assert settings["round_tiles"] is True
        out = fire("show_realistic", [[0, 1, 0], source, settings], wildcard("style-realistic", "pop") + ".n_clicks")
        assert "tuile ronde" in out["realistic-note"]["children"]

    def test_redrawn_cards_open_nothing(self, fire, source):
        trigger = wildcard("style-realistic", "pop") + ".n_clicks"
        assert fire("show_realistic", [[0, 0, 0], source, None], trigger) is None


class TestModes:
    def test_flat_uses_only_plates(self, fire, source):
        flat = generate(fire, source)
        assert "98138" not in flat["builds"]["data"]["natural"]["wanted_list"]
        assert "plaques cachées" not in str(flat["styles"]["children"])
        assert flat["settings"]["data"]["round_tiles"] is False
        assert flat["settings"]["data"]["relief"] == "flat"

    def test_round_puts_tiles_in_the_picture_and_1x4s_in_the_frame(self, fire, source):
        built = generate(fire, source, round_tiles=True, frame=2)
        wanted_list = built["builds"]["data"]["natural"]["wanted_list"]
        assert "<ITEMID>98138</ITEMID>" in wanted_list and "<ITEMID>2431</ITEMID>" in wanted_list
        assert built["settings"]["data"]["round_tiles"] is True
        assert built["settings"]["data"]["relief"] == "flat"

    def test_relief_is_raised_and_made_only_of_plates(self, fire, source):
        built = generate(fire, source, relief="stepped")
        assert "98138" not in built["builds"]["data"]["natural"]["wanted_list"]
        assert "plaques cachées" in str(built["styles"]["children"])
        assert built["settings"]["data"]["round_tiles"] is False
        assert built["settings"]["data"]["relief"] == "stepped"


class TestDepthDownload:
    def test_shows_the_progress_while_downloading(self, fire, monkeypatch):
        monkeypatch.setattr(depth, "_downloaded", 5 * 10**6)
        out = fire("show_depth_download", [3], "depth-poll.n_intervals")
        assert "5 / 27 Mo" in str(out["depth-status"]["children"])

    def test_shows_nothing_the_rest_of_the_time(self, fire):
        out = fire("show_depth_download", [3], "depth-poll.n_intervals")
        assert out["depth-status"]["children"] == ""


class TestDepthMode:
    def test_uses_the_model_and_only_plates_underneath(self, fire, source, monkeypatch):
        monkeypatch.setattr(depth, "estimate_depth", lambda rgb: np.full(rgb.shape[:2], 1.0, dtype=np.float32))
        built = generate(fire, source, relief="depth")
        assert built["settings"]["data"]["relief"] == "depth"
        assert "plaques cachées" in str(built["styles"]["children"])
        assert "98138" not in built["builds"]["data"]["natural"]["wanted_list"]

    def test_says_when_the_model_cant_run(self, fire, source, monkeypatch):
        def fail(rgb):
            raise depth.DepthError("no network")

        monkeypatch.setattr(depth, "estimate_depth", fail)
        built = generate(fire, source, relief="depth")
        assert "no network" in str(built["styles"]["children"])
        assert built["settings"]["data"] is None


class TestAssembly:
    def test_panels_reach_the_list_and_the_card_and_replace_the_baseplates(self, fire, source):
        plates = generate(fire, source)
        panels = generate(fire, source, assembly="panels")

        assert "<ITEMID>65803</ITEMID>" not in plates["builds"]["data"]["natural"]["wanted_list"]
        assert "<ITEMID>65803</ITEMID>" in panels["builds"]["data"]["natural"]["wanted_list"]
        assert "4 panneaux" in str(panels["styles"]["children"])
        assert panels["settings"]["data"]["assembly"] == "panels"
        assert panels["builds"]["data"]["natural"]["baseplates"] == 0

    def test_repricing_keeps_panels_without_baseplates(self, fire, source):
        built = generate(fire, source, assembly="panels")
        out = fire(
            "update_prices",
            ["compatible", "EUR", built["settings"]["data"], built["builds"]["data"], None],
            "baseplates.value",
        )
        detail = out[wildcard("style-price-detail", "natural")]["children"]
        assert "baseplates" not in detail


class TestAccents:
    def test_reach_the_settings_and_are_ignored_outside_round_tiles(self, fire, source):
        assert generate(fire, source, accents=True)["settings"]["data"]["accents"] is True
        assert generate(fire, source, round_tiles=True, accents=True)["styles"]["children"]


class TestSharpEdges:
    def test_reaches_the_settings(self, fire, source):
        assert generate(fire, source, sharp=True)["settings"]["data"]["sharp"] is True
        assert generate(fire, source)["settings"]["data"]["sharp"] is False


class TestReliefEditor:
    def test_offered_only_with_a_relief(self, fire, source):
        assert "style-relief" not in str(generate(fire, source)["styles"]["children"])
        assert "style-relief" in str(generate(fire, source, relief="stepped")["styles"]["children"])

    def test_opens_on_the_clicked_style(self, fire, source):
        out = fire("show_relief_editor", [[0, 1, 0], source], wildcard("style-relief", "pop") + ".n_clicks")
        assert out["relief-modal"]["opened"] is True
        assert out["relief-url"]["data"] == f"/relief/{source['session']}/pop.json"

    def test_redrawn_cards_open_nothing(self, fire, source):
        trigger = wildcard("style-relief", "pop") + ".n_clicks"
        assert fire("show_relief_editor", [[0, 0, 0], source], trigger) is None

    def test_an_edit_changes_the_card_and_the_list_and_a_new_render_forgets_it(self, fire, source):
        built = generate(fire, source, relief="stepped")
        settings = built["settings"]["data"]
        before = built["builds"]["data"]["natural"]["wanted_list"]

        service.save_relief_edit(source["session"], "natural", [0] * (32 * 32))
        out = fire("apply_relief_edit", [1, source, settings], "relief-saved.data")
        assert out["builds"]["data"]["natural"]["wanted_list"] != before
        assert out["selected-style"]["data"] is None
        assert "suivent" in str(out["relief-status"]["children"])

        again = generate(fire, source, relief="stepped")
        assert again["builds"]["data"]["natural"]["wanted_list"] == before


class TestLanguage:
    def test_french_by_default_and_english_once_chosen(self, app):
        client = app.server.test_client()
        assert "Choisis une image" in client.get("/_dash-layout").get_data(as_text=True)
        response = client.get("/lang/en")
        assert response.status_code == 302
        page = client.get("/_dash-layout").get_data(as_text=True)
        assert "Choose a picture" in page and "Choisis une image" not in page

    def test_follows_the_browser_until_a_choice_is_made(self, app):
        client = app.server.test_client()
        headers = {"Accept-Language": "en-GB,en;q=0.9"}
        assert "Choose a picture" in client.get("/_dash-layout", headers=headers).get_data(as_text=True)
        headers = {"Accept-Language": "fr-FR,fr;q=0.9"}
        assert "Choisis une image" in client.get("/_dash-layout", headers=headers).get_data(as_text=True)

    def test_an_unknown_language_changes_nothing(self, app):
        client = app.server.test_client()
        client.get("/lang/xx")
        assert "Choisis une image" in client.get("/_dash-layout").get_data(as_text=True)


class TestViewer:
    def test_opens_on_the_clicked_style(self, fire, source):
        out = fire("show_3d", [[0, 1, 0], source], wildcard("style-3d", "pop") + ".n_clicks")
        assert out["viewer-modal"]["opened"] is True
        assert "Pop art" in out["viewer-modal"]["title"]
        assert out["viewer-url"]["data"].startswith(f"/model/{source['session']}/pop.json?v=")

    def test_redrawn_cards_open_nothing(self, fire, source):
        assert fire("show_3d", [[0, 0, 0], source], wildcard("style-3d", "pop") + ".n_clicks") is None


class TestFrameColor:
    def test_a_white_frame_reaches_the_wanted_list(self, fire, source):
        white = generate(fire, source, round_tiles=True, frame=2, frame_color="white")
        assert white["settings"]["data"]["frame_color"] == "white"
        wanted_list = white["builds"]["data"]["natural"]["wanted_list"]
        # White is BrickLink colour 1: 60 x 1x4 for this frame, plus 5 % spares.
        assert "<ITEMID>2431</ITEMID><COLOR>1</COLOR><MINQTY>63</MINQTY>" in wanted_list


class TestRoundTiles:
    def test_the_whole_build_uses_round_tiles(self, fire, source):
        out = generate(fire, source, round_tiles=True)
        wanted_list = out["builds"]["data"]["natural"]["wanted_list"]
        assert "<ITEMID>98138</ITEMID>" in wanted_list and "<ITEMID>3070</ITEMID>" not in wanted_list
        assert "tuiles rondes" in str(out["styles"]["children"])

    def test_the_enlarged_view_stays_smooth(self, fire, source):
        previews = generate(fire, source, round_tiles=True)["previews"]["data"]
        out = fire("enlarge_style", [[0, 1, 0], previews], wildcard("style-enlarge", "pop") + ".n_clicks")
        assert out["preview-image"]["className"] == "preview-image smooth"


class TestOptions:
    def test_plates_reach_the_wanted_list_and_are_counted(self, fire, source):
        only_1x1, chosen = generate(fire, source, plates=["1x1"]), generate(fire, source, plates=["1x1", "1x4"])
        assert chosen["settings"]["data"]["plates"] == ["1x1", "1x4"]
        wanted_list = chosen["builds"]["data"]["natural"]["wanted_list"]
        assert "<ITEMID>2431</ITEMID>" in wanted_list and "<ITEMID>3068</ITEMID>" not in wanted_list
        assert "<ITEMID>2431</ITEMID>" not in only_1x1["builds"]["data"]["natural"]["wanted_list"]
        assert "tuiles" in str(chosen["styles"]["children"])

    def test_dither_reaches_the_settings(self, fire, source):
        assert generate(fire, source, dither=True)["settings"]["data"]["dither"] is True


class TestDownloadBricklink:
    def test_downloads_the_selected_styles_wanted_list(self, fire, source):
        builds = generate(fire, source)["builds"]["data"]
        out = fire("download_bricklink", [1, source, builds, "pop"], "download-bricklink.n_clicks")

        file = out["bricklink-file"]["data"]
        assert file["filename"] == "studify-flag-pop-bricklink.xml"
        assert file["content"] == builds["pop"]["wanted_list"]
        assert "<ITEMID>" in file["content"]

    def test_without_a_selected_style_updates_nothing(self, fire, source):
        assert fire("download_bricklink", [1, source, {}, None], "download-bricklink.n_clicks") is None

    def test_is_enabled_with_the_selection(self, fire):
        assert (
            fire("highlight_selection", ["pop", None], "selected-style.data")["download-bricklink"]["disabled"] is False
        )
        assert (
            fire("highlight_selection", [None, None], "selected-style.data")["download-bricklink"]["disabled"] is True
        )


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

        assert "baseplates compatibles" in printed[0] and "$" in printed[0]

    def test_the_guide_is_in_french_and_honours_the_options(self, fire, source, printed):
        settings = generate(fire, source)["settings"]["data"]
        fire("download_pdf", [1, source, {**settings, "tinted": False}, "pop"], "download-pdf.n_clicks")

        assert '<html lang="fr">' in printed[0] and "Tuiles nécessaires" in printed[0]
        assert "background: rgb(" not in printed[0].split("</head>")[0]

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
