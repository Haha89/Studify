from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from studify.web import projects, service

SETTINGS = {"size": 1, "frame": 0, "relief": "stepped"}


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setenv(projects.DATA_ENV, str(tmp_path / "data"))
    return tmp_path


@pytest.fixture
def session():
    img = np.zeros((40, 80, 3), dtype=np.uint8)
    img[:, 40:] = (0, 0, 255)
    ok, data = cv2.imencode(".png", img)
    assert ok
    return service.save_source(data.tobytes(), "Mon chien.png")


@pytest.fixture
def painted(session):
    service.render_styles(session, size=1, frame=0, max_colors=None, relief="stepped")
    service.save_relief_edit(session, "pop", [0] * (32 * 32))
    return session


class TestSave:
    def test_keeps_the_picture_settings_choice_and_reliefs(self, painted):
        saved = projects.save_project(painted, "Mon chien.png", SETTINGS, "pop")
        folder = projects.project_root() / saved["id"]
        assert (folder / "source.png").read_bytes() == service.source_path(painted).read_bytes()
        assert (folder / "relief-pop.npy").is_file() and not (folder / "relief-natural.npy").exists()
        assert projects.read_project(saved["id"]) == {"name": "Mon chien.png", "settings": SETTINGS, "selected": "pop"}
        assert saved["thumbnail"].startswith("data:image/jpeg;base64,")

    def test_the_token_is_not_stored(self, session):
        saved = projects.save_project(session, "x.png", SETTINGS, None)
        assert saved["token"] not in (projects.project_root() / saved["id"] / "project.json").read_text()

    def test_each_save_is_a_new_link(self, session):
        first = projects.save_project(session, "x.png", SETTINGS, None)
        assert projects.save_project(session, "x.png", SETTINGS, None)["id"] != first["id"]

    def test_an_unknown_style_is_not_kept_as_the_choice(self, session):
        saved = projects.save_project(session, "x.png", SETTINGS, "../etc")
        assert projects.read_project(saved["id"])["selected"] is None

    def test_refuses_oversized_settings(self, session):
        with pytest.raises(projects.ProjectError):
            projects.save_project(session, "x.png", {"junk": "x" * 10_000}, None)

    def test_refuses_once_storage_is_full(self, session, monkeypatch):
        projects.save_project(session, "x.png", SETTINGS, None)
        monkeypatch.setattr(projects, "MAX_PROJECTS_BYTES", 10)
        with pytest.raises(projects.ProjectError, match="place"):
            projects.save_project(session, "x.png", SETTINGS, None)

    def test_an_expired_session_cant_be_saved(self):
        with pytest.raises(service.ImageError, match="introuvable"):
            projects.save_project("0" * 32, "x.png", SETTINGS, None)


class TestOpen:
    def test_copies_the_project_into_a_new_session(self, painted):
        saved = projects.save_project(painted, "Mon chien.png", SETTINGS, "pop")
        opened, meta = projects.open_project(saved["id"])
        assert opened != painted
        assert service.source_path(opened).name == "Mon_chien.png"
        assert meta["selected"] == "pop"
        assert np.array_equal(
            np.load(service.relief_edit_path(opened, "pop")), np.load(service.relief_edit_path(painted, "pop"))
        )

    def test_changes_made_after_opening_leave_the_project_alone(self, painted):
        saved = projects.save_project(painted, "x.png", SETTINGS, "pop")
        opened, _ = projects.open_project(saved["id"])
        service.clear_relief_edits(opened)
        again, _ = projects.open_project(saved["id"])
        assert service.relief_edit_path(again, "pop").is_file()

    @pytest.mark.parametrize("project_id", ["", None, "../../etc/passwd", "a" * 22, "a" * 21])
    def test_bad_or_unknown_ids_are_refused(self, project_id):
        with pytest.raises(projects.ProjectError):
            projects.open_project(project_id)

    @pytest.mark.parametrize(
        "relief",
        [np.zeros((4,), dtype=int), np.zeros((300, 10), dtype=int), np.zeros((32, 32), dtype=float)],
    )
    def test_a_relief_that_cant_be_a_mosaics_is_dropped(self, session, relief):
        saved = projects.save_project(session, "x.png", SETTINGS, None)
        np.save(projects.project_root() / saved["id"] / "relief-pop.npy", relief)
        opened, _ = projects.open_project(saved["id"])
        assert not service.relief_edit_path(opened, "pop").exists()

    def test_heights_are_limited(self, session):
        saved = projects.save_project(session, "x.png", SETTINGS, None)
        np.save(projects.project_root() / saved["id"] / "relief-pop.npy", np.full((32, 32), 99))
        opened, _ = projects.open_project(saved["id"])
        assert np.load(service.relief_edit_path(opened, "pop")).max() == projects.MAX_PLATES

    def test_a_damaged_project_reads_as_missing(self, session):
        saved = projects.save_project(session, "x.png", SETTINGS, None)
        (projects.project_root() / saved["id"] / "project.json").write_text("[1, 2")
        with pytest.raises(projects.ProjectError, match="introuvable"):
            projects.open_project(saved["id"])

    def test_odd_metadata_falls_back_to_defaults(self, session):
        saved = projects.save_project(session, "x.png", SETTINGS, None)
        meta = projects.project_root() / saved["id"] / "project.json"
        meta.write_text(json.dumps({"name": 5, "settings": [1], "selected": "nope"}))
        assert projects.read_project(saved["id"]) == {"name": "5", "settings": {}, "selected": None}


class TestLifetime:
    def test_unopened_projects_expire_and_opening_keeps_one(self, session, monkeypatch):
        old = projects.save_project(session, "old.png", SETTINGS, None)
        used = projects.save_project(session, "used.png", SETTINGS, None)
        monkeypatch.setattr(projects, "PROJECT_TTL_SECONDS", 1000)
        stale = projects.time.time() - 2000
        for saved in (old, used):
            projects.os.utime(projects.project_root() / saved["id"] / "project.json", (stale, stale))
        projects.open_project(used["id"])

        projects.save_project(session, "new.png", SETTINGS, None)
        assert projects.kept([old["id"], used["id"]]) == {used["id"]}

    def test_deleting_needs_the_token(self, session):
        saved = projects.save_project(session, "x.png", SETTINGS, None)
        assert projects.delete_project(saved["id"], "wrong") is False
        assert projects.delete_project(saved["id"], None) is False
        assert projects.delete_project(saved["id"], saved["token"]) is True
        assert projects.kept([saved["id"]]) == set()
        assert projects.delete_project(saved["id"], saved["token"]) is False
