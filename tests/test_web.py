from __future__ import annotations

import base64
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import pytest

from studify.models import pdf_creator
from studify.models.utils import read_image
from studify.web import service


@pytest.fixture(autouse=True)
def session_root(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    monkeypatch.setattr(service, "SESSION_ROOT", root)
    return root


@pytest.fixture
def png_bytes():
    img = np.zeros((40, 80, 3), dtype=np.uint8)
    img[:, 40:] = (0, 0, 255)
    ok, data = cv2.imencode(".png", img)
    assert ok
    return data.tobytes()


class TestUpload:
    def test_decodes_a_dash_data_uri(self, png_bytes):
        contents = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
        assert service.image_bytes_from_upload(contents) == png_bytes

    def test_rejects_garbage(self):
        with pytest.raises(service.ImageError):
            service.image_bytes_from_upload("data:image/png;base64,@@not base64@@")

    def test_rejects_oversized_files(self, png_bytes, monkeypatch):
        monkeypatch.setattr(service, "MAX_IMAGE_BYTES", 10)
        with pytest.raises(service.ImageError, match="trop lourde"):
            service.image_bytes_from_upload("data:image/png;base64," + base64.b64encode(png_bytes).decode())


class TestUrl:
    @pytest.mark.parametrize("url", ["ftp://example.com/a.png", "file:///etc/passwd", "not a url"])
    def test_rejects_non_http_links(self, url):
        with pytest.raises(service.ImageError, match="http"):
            service.image_bytes_from_url(url)

    @pytest.mark.parametrize("url", ["http://127.0.0.1/a.png", "http://localhost:8050/", "http://192.168.1.1/x.jpg"])
    def test_rejects_private_addresses(self, url):
        with pytest.raises(service.ImageError, match="inaccessible"):
            service.image_bytes_from_url(url)

    def test_a_host_that_does_not_resolve_is_not_public(self, monkeypatch):
        def no_such_host(host, port):
            raise service.socket.gaierror

        monkeypatch.setattr(service.socket, "getaddrinfo", no_such_host)
        assert service._is_public_host("nowhere.invalid") is False

    @pytest.mark.parametrize(
        ("addresses", "public"),
        [(["8.8.8.8"], True), (["8.8.8.8", "10.0.0.1"], False), (["8.8.8.8", "::1"], False)],
    )
    def test_a_host_is_public_only_if_every_address_is(self, addresses, public, monkeypatch):
        infos = [(None, None, None, "", (address, 0)) for address in addresses]
        monkeypatch.setattr(service.socket, "getaddrinfo", lambda host, port: infos)
        assert service._is_public_host("example.com") is public

    @pytest.mark.parametrize("error", [service.urllib.error.URLError("refused"), TimeoutError()])
    def test_a_failed_download_is_a_clear_error(self, error, monkeypatch):
        def fail(request, timeout):
            raise error

        monkeypatch.setattr(service, "_is_public_host", lambda host: True)
        monkeypatch.setattr(service._OPENER, "open", fail)
        with pytest.raises(service.ImageError, match="Impossible de télécharger"):
            service.image_bytes_from_url("https://example.com/dog.png")

    def test_rejects_an_oversized_download(self, png_bytes, monkeypatch):
        monkeypatch.setattr(service, "MAX_IMAGE_BYTES", 10)
        monkeypatch.setattr(service, "_is_public_host", lambda host: True)
        monkeypatch.setattr(service._OPENER, "open", lambda request, timeout: io.BytesIO(png_bytes))
        with pytest.raises(service.ImageError, match="trop lourde"):
            service.image_bytes_from_url("https://example.com/dog.png")

    def test_downloads_a_public_image(self, png_bytes, monkeypatch):
        monkeypatch.setattr(service, "_is_public_host", lambda host: True)
        monkeypatch.setattr(service._OPENER, "open", lambda request, timeout: io.BytesIO(png_bytes))
        assert service.image_bytes_from_url("https://example.com/dog.png") == png_bytes


class TestRedirects:
    """Against a real local server, reached as "localhost", the one host treated as
    public here; 127.0.0.1 stands in for an address on the server's own network."""

    @pytest.fixture
    def server(self, png_bytes, monkeypatch):
        monkeypatch.setattr(service, "_is_public_host", lambda host: host == "localhost")

        class Handler(BaseHTTPRequestHandler):
            hits = []

            def do_GET(self):
                self.hits.append(self.path)
                port = self.server.server_address[1]
                targets = {
                    "/to-public": f"http://localhost:{port}/image",
                    "/to-private": f"http://127.0.0.1:{port}/secret",
                    "/to-file": "file:///etc/passwd",
                }
                if self.path in targets:
                    self.send_response(302)
                    self.send_header("Location", targets[self.path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(png_bytes)))
                    self.end_headers()
                    self.wfile.write(png_bytes)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        yield server, Handler.hits
        server.shutdown()
        thread.join()
        server.server_close()

    def test_follows_a_redirect_to_a_public_address(self, server, png_bytes):
        port = server[0].server_address[1]
        assert service.image_bytes_from_url(f"http://localhost:{port}/to-public") == png_bytes

    def test_refuses_a_redirect_to_a_private_address(self, server):
        port = server[0].server_address[1]
        with pytest.raises(service.ImageError, match="inaccessible"):
            service.image_bytes_from_url(f"http://localhost:{port}/to-private")
        assert "/secret" not in server[1]

    def test_refuses_a_redirect_to_another_scheme(self, server):
        port = server[0].server_address[1]
        with pytest.raises(service.ImageError, match="Impossible de télécharger"):
            service.image_bytes_from_url(f"http://localhost:{port}/to-file")


class TestSessions:
    def test_stores_the_image_under_a_clean_name(self, png_bytes, session_root):
        session = service.save_source(png_bytes, "Mon chien (été 2026).jpg")
        path = service.source_path(session)
        assert path.parent == session_root / session
        assert path.name == "Mon_chien_été_2026.png"
        assert read_image(path).shape == (40, 80, 3)

    def test_shrinks_large_images_keeping_their_proportions(self, monkeypatch):
        monkeypatch.setattr(service, "MAX_SOURCE_DIMENSION", 40)
        ok, data = cv2.imencode(".png", np.zeros((50, 200, 3), dtype=np.uint8))
        assert ok
        session = service.save_source(data.tobytes(), "wide.png")
        assert read_image(service.source_path(session)).shape == (10, 40, 3)

    def test_rejects_files_that_are_not_images(self):
        with pytest.raises(service.ImageError, match="pas une image"):
            service.save_source(b"%PDF-1.7 definitely not an image", "doc.pdf")

    @pytest.mark.parametrize("session", ["../../etc", "", None, "A" * 32, "0" * 31])
    def test_rejects_malformed_session_ids(self, session):
        with pytest.raises(service.ImageError, match="invalide"):
            service.source_path(session)

    def test_reports_an_expired_session(self):
        with pytest.raises(service.ImageError, match="introuvable"):
            service.source_path("0" * 32)

    def test_purges_stale_sessions(self, png_bytes, session_root, monkeypatch):
        old = service.save_source(png_bytes, "old.png")
        monkeypatch.setattr(service, "SESSION_TTL_SECONDS", -1)  # everything counts as stale
        service.save_source(png_bytes, "new.png")
        assert not (session_root / old).exists()


def test_preview_shrinks_the_image_and_reports_its_size(png_bytes):
    session = service.save_source(png_bytes, "flag.png")
    uri, width, height = service.preview(service.source_path(session), max_dimension=20)
    assert uri.startswith("data:image/png;base64,")
    assert (width, height) == (20, 10)


def test_render_styles_returns_one_preview_per_style(png_bytes):
    session = service.save_source(png_bytes, "flag.png")
    results = service.render_styles(session, size=1, frame=1, max_colors=None)

    assert [r["style"] for r in results] == list(service.STYLE_LABELS)
    for result in results:
        assert result["image"].startswith("data:image/png;base64,")
        assert result["studs"] == "32 × 32"
        assert result["baseplates"] == 1
        assert result["bricks"] == 32 * 32
        assert result["colors"] >= 1


def test_build_pdf_writes_the_selected_style(png_bytes, monkeypatch):
    printed = []

    def fake_html_to_pdf(html, pdf):
        printed.append(html)
        pdf.write_bytes(b"%PDF-fake")
        return pdf

    monkeypatch.setattr(pdf_creator, "html_to_pdf", fake_html_to_pdf)
    session = service.save_source(png_bytes, "flag.png")
    pdf = service.build_pdf(session, size=1, frame=0, max_colors=8, style="pop")

    assert pdf.read_bytes() == b"%PDF-fake"
    assert pdf.name == "flag.pdf" and pdf.parent.name == "guide-pop"
    assert printed[0].suffix == ".html"


def test_build_pdf_rejects_an_unknown_style(png_bytes):
    session = service.save_source(png_bytes, "flag.png")
    with pytest.raises(service.ImageError, match="Style"):
        service.build_pdf(session, size=1, frame=0, max_colors=None, style="baroque")


class TestBrowserLookup:
    def test_environment_override_wins(self, monkeypatch):
        monkeypatch.setenv(pdf_creator.BROWSER_ENV, "/opt/my-chrome")
        assert pdf_creator.find_browser() == "/opt/my-chrome"

    def test_missing_browser_is_a_clear_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pdf_creator, "find_browser", lambda: None)
        with pytest.raises(RuntimeError, match="Chrome, Chromium or Edge"):
            pdf_creator.html_to_pdf(tmp_path / "guide.html", tmp_path / "guide.pdf")


def test_app_builds_with_every_step():
    pytest.importorskip("dash")
    from studify.web.app import create_app

    layout = str(create_app().layout)
    for component_id in ("upload", "url", "size", "frame", "max-colors", "generate", "styles", "download-pdf"):
        assert f"'{component_id}'" in layout or f'"{component_id}"' in layout
    assert "https://www.bricklink.com/v2/wanted/upload.page" in layout


def test_built_pdf_finds_the_last_build(png_bytes, monkeypatch):
    monkeypatch.setattr(pdf_creator, "html_to_pdf", lambda html, pdf: pdf.write_bytes(b"%PDF-fake") and pdf)
    session = service.save_source(png_bytes, "flag.png")
    with pytest.raises(service.ImageError, match="pas encore"):
        service.built_pdf(session, "pop")
    service.build_pdf(session, size=1, frame=0, max_colors=None, style="pop")
    assert service.built_pdf(session, "pop").read_bytes() == b"%PDF-fake"


def test_pdf_route_serves_built_guides_only(png_bytes, monkeypatch):
    pytest.importorskip("dash")
    from studify.web.app import create_app

    monkeypatch.setattr(pdf_creator, "html_to_pdf", lambda html, pdf: pdf.write_bytes(b"%PDF-fake") and pdf)
    session = service.save_source(png_bytes, "flag.png")
    service.build_pdf(session, size=1, frame=0, max_colors=None, style="comic")
    client = create_app().server.test_client()

    with client.get(f"/guide/{session}/comic.pdf") as response:  # streamed: close the file
        assert response.status_code == 200
        assert response.data == b"%PDF-fake"
        assert "filename=studify-flag-comic.pdf" in response.headers["Content-Disposition"]

    assert client.get(f"/guide/{session}/pop.pdf").status_code == 404  # never built
    # Dash answers unknown paths with its page (HTML), never with a file.
    assert client.get("/guide/..%2F..%2Fetc/comic.pdf").mimetype == "text/html"
    assert client.get(f"/guide/{'0' * 32}/comic.pdf").status_code == 404  # unknown session
    assert client.get(f"/guide/{session}/baroque.pdf").status_code == 404


class TestCrop:
    def test_validate_crop_clamps_and_orders(self):
        assert service.validate_crop([0.8, -0.2, 0.2, 0.5]) == (0.2, 0.0, 0.8, 0.5)

    @pytest.mark.parametrize("crop", [None, [0, 0, 1, 1], [0.5, 0.5, 0.51, 0.9], [0.1, 0.1, 0.9, 0.12]])
    def test_validate_crop_treats_whole_or_tiny_boxes_as_no_crop(self, crop):
        assert service.validate_crop(crop) is None

    @pytest.mark.parametrize("ratio", [1.0, 4 / 3, 0.75, 1.5, 2 / 3])
    def test_fit_ratio_hits_the_ratio_inside_the_image(self, ratio):
        left, top, right, bottom = service.fit_ratio((10, 20, 190, 80), ratio, 200, 100)
        assert (right - left) / (bottom - top) == pytest.approx(ratio)
        assert 0 <= left < right <= 200 and 0 <= top < bottom <= 100

    def test_fit_ratio_keeps_the_box_centre_when_it_can(self):
        left, top, right, bottom = service.fit_ratio((40, 10, 160, 90), 1.0, 200, 100)
        assert ((left + right) / 2, (top + bottom) / 2) == pytest.approx((100, 50))

    def test_fit_ratio_slides_a_box_back_inside(self):
        # Centred near the right edge: a square of the full height must shift left.
        left, top, right, bottom = service.fit_ratio((150, 0, 200, 100), 1.0, 200, 100)
        assert right <= 200 and right - left == pytest.approx(bottom - top)

    def test_cropped_render_uses_only_the_kept_area(self, png_bytes):
        # png_bytes is 80 x 40: black left half, red right half.
        session = service.save_source(png_bytes, "flag.png")
        whole = service.render_styles(session, size=1, frame=0, max_colors=None)
        right_half = service.render_styles(session, size=1, frame=0, max_colors=None, crop=[0.5, 0, 1, 1])
        assert whole[0]["colors"] >= 2
        assert right_half[0]["colors"] == 1  # only red left
        assert service.source_path(session).name == "flag.png"  # the original is untouched

    def test_cropped_pdf_keeps_the_guide_title(self, png_bytes, monkeypatch):
        monkeypatch.setattr(pdf_creator, "html_to_pdf", lambda html, pdf: pdf.write_bytes(b"%PDF-fake") and pdf)
        session = service.save_source(png_bytes, "flag.png")
        service.build_pdf(session, size=1, frame=0, max_colors=None, style="pop", crop=[0, 0, 0.5, 1])
        assert service.built_pdf(session, "pop").name == "flag.pdf"


class TestNextCropBox:
    @pytest.fixture(autouse=True)
    def _needs_dash(self):
        pytest.importorskip("dash")

    def _next(self, *args):
        from studify.web.app import next_crop_box

        return next_crop_box(*args)

    def test_drawing_a_box(self):
        relayout = {"shapes": [{"x0": 9.5, "y0": 19.5, "x1": 59.5, "y1": 39.5}]}
        assert self._next("crop-graph", relayout, None, "free", 100, 50) == (10, 20, 60, 40)

    def test_drawing_again_replaces_the_box(self):
        relayout = {"shapes": [{"x0": 0, "y0": 0, "x1": 10, "y1": 10}, {"x0": 19.5, "y0": 9.5, "x1": 29.5, "y1": 19.5}]}
        assert self._next("crop-graph", relayout, None, "free", 100, 50) == (20, 10, 30, 20)

    def test_moving_the_box(self):
        current = {"x0": 9.5, "y0": 9.5, "x1": 19.5, "y1": 19.5}
        relayout = {"shapes[0].x0": 29.5, "shapes[0].x1": 39.5}
        assert self._next("crop-graph", relayout, current, "free", 100, 50) == (30, 10, 40, 20)

    def test_a_box_dragged_past_the_edge_is_clamped(self):
        relayout = {"shapes": [{"x0": -20, "y0": -20, "x1": 49.5, "y1": 500}]}
        assert self._next("crop-graph", relayout, None, "free", 100, 50) == (0, 0, 50, 50)

    def test_unrelated_relayout_is_ignored(self):
        assert self._next("crop-graph", {"autosize": True}, None, "free", 100, 50) is False

    def test_reset_and_erasing_clear_the_crop(self):
        current = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
        assert self._next("crop-reset", None, current, "free", 100, 50) is None
        assert self._next("crop-graph", {"shapes": []}, current, "free", 100, 50) is None

    def test_picking_a_ratio_without_a_box_fits_it_to_the_whole_image(self):
        assert self._next("crop-ratio", None, None, "1", 100, 50) == pytest.approx((25, 0, 75, 50))
        assert self._next("crop-ratio", None, None, "free", 100, 50) is False

    def test_a_ratio_reshapes_the_drawn_box(self):
        relayout = {"shapes": [{"x0": -0.5, "y0": -0.5, "x1": 79.5, "y1": 19.5}]}
        left, top, right, bottom = self._next("crop-graph", relayout, None, "1", 100, 50)
        assert right - left == pytest.approx(bottom - top)


def test_render_styles_prices_each_style(png_bytes):
    session = service.save_source(png_bytes, "flag.png")
    official = service.render_styles(session, size=1, frame=0, max_colors=None)
    compatible = service.render_styles(
        session, size=1, frame=0, max_colors=None, baseplates="compatible", currency="USD"
    )
    assert official[0]["price"].endswith("€") and compatible[0]["price"].endswith("$")
    assert official[0]["price_baseplates"] == "9 €"
    assert isinstance(official[0]["rare_colors"], list)


def test_reprice_matches_a_fresh_render(png_bytes):
    session = service.save_source(png_bytes, "flag.png")
    official = service.render_styles(session, size=1, frame=0, max_colors=None)[0]
    compatible = service.render_styles(
        session, size=1, frame=0, max_colors=None, baseplates="compatible", currency="USD"
    )[0]

    # Brick ids come back from the page as strings: it stores them as JSON.
    distribution = {str(brick_id): count for brick_id, count in official["distribution"].items()}
    prices = service.reprice(distribution, official["baseplates"], "compatible", "USD")

    assert prices == {key: compatible[key] for key in ("price", "price_bricks", "price_baseplates")}
