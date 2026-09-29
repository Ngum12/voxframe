"""Where an installed Voxframe keeps things (D-156).

A source checkout keeps its local folders; an installed app uses the person's
own. The environment always wins, a library moved in Settings is used from the
next start, and a finished video is placed in the videos folder under a
readable name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.config import paths
from voxframe.config.settings import Settings


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Pretend to be an installed app, with a home folder under tmp_path."""
    monkeypatch.setattr(paths, "is_source_checkout", lambda package=None: False)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(paths, "videos_dir", lambda: tmp_path / "Videos" / "Voxframe")
    for name in ("VOXFRAME_LIBRARY_PATH", "VOXFRAME_CACHE_PATH", "VOXFRAME_OUTPUT_PATH",
                 "HF_HOME", "HF_HUB_CACHE"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


class TestWhereThingsGo:
    def test_this_checkout_is_recognised(self) -> None:
        assert paths.is_source_checkout()

    def test_an_installed_package_is_not_a_checkout(self, tmp_path: Path) -> None:
        package = tmp_path / "site-packages" / "voxframe"
        package.mkdir(parents=True)

        assert not paths.is_source_checkout(package)

    def test_a_checkout_keeps_its_local_folders(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VOXFRAME_LIBRARY_PATH", raising=False)

        assert Settings(_env_file=None).library_path == Path("./library")  # type: ignore[call-arg]

    def test_installed_uses_the_persons_folders(self, installed: Path) -> None:
        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        assert settings.library_path == installed / "data" / "library"
        assert settings.cache_path == installed / "data" / "cache"
        assert settings.output_path == installed / "Videos" / "Voxframe"

    def test_the_environment_wins(self, installed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VOXFRAME_LIBRARY_PATH", str(installed / "elsewhere"))

        assert Settings(_env_file=None).library_path == installed / "elsewhere"  # type: ignore[call-arg]


class TestModels:
    def test_installed_models_go_in_the_app_folder(
        self, installed: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        monkeypatch.setattr(paths, "models_dir", lambda: installed / "data" / "models")

        folder = paths.configure_model_cache()

        assert folder == installed / "data" / "models" / "huggingface"
        assert os.environ["HF_HUB_CACHE"] == str(folder)

    def test_a_persons_own_cache_setting_is_kept(
        self, installed: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HF_HOME", str(installed / "mine"))

        assert paths.configure_model_cache() is None

    def test_a_checkout_is_left_alone(self) -> None:
        assert paths.configure_model_cache() is None


# --- the app ----------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi", reason="web extra not installed")

from fastapi.testclient import TestClient  # noqa: E402

from voxframe.api.app import ApiContext, _place_in_videos, create_app  # noqa: E402
from voxframe.api.security import SessionToken  # noqa: E402
from voxframe.jobs.store import JobStore  # noqa: E402


def _context(tmp_path: Path, videos: Path | None) -> ApiContext:
    root = tmp_path / "jobs"
    return ApiContext(
        settings=Settings(
            library_path=tmp_path / "library", cache_path=tmp_path / "cache",
            output_path=tmp_path / "out",
        ),
        store=JobStore(root, reap_interval=None),
        token=SessionToken("t"),
        allowed_paths=(root.resolve(),),
        videos_folder=videos,
    )


def _video(tmp_path: Path, name: str = "source.mp4") -> Path:
    path = tmp_path / "render" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"video")
    return path


class TestFinishedVideos:
    def test_named_after_the_title(self, tmp_path: Path) -> None:
        context = _context(tmp_path, tmp_path / "Videos")
        job = context.store.create(audio_name="Recording.m4a", options={"title": "My Talk"})

        saved = _place_in_videos(context, job, _video(tmp_path))

        assert saved == str(tmp_path / "Videos" / "My Talk.mp4")
        assert Path(saved).read_bytes() == b"video"

    def test_or_the_recording_without_one(self, tmp_path: Path) -> None:
        context = _context(tmp_path, tmp_path / "Videos")
        job = context.store.create(audio_name="lesson one.m4a", options={})

        assert _place_in_videos(context, job, _video(tmp_path)).endswith("lesson one.mp4")  # type: ignore[union-attr]

    def test_two_videos_with_one_name_both_kept(self, tmp_path: Path) -> None:
        context = _context(tmp_path, tmp_path / "Videos")
        first = context.store.create(audio_name="a.m4a", options={"title": "Talk"})
        second = context.store.create(audio_name="b.m4a", options={"title": "Talk"})

        _place_in_videos(context, first, _video(tmp_path))
        saved = _place_in_videos(context, second, _video(tmp_path))

        assert saved is not None and saved.endswith("Talk (2).mp4")

    def test_a_rerender_replaces_its_own_file(self, tmp_path: Path) -> None:
        context = _context(tmp_path, tmp_path / "Videos")
        job = context.store.create(audio_name="a.m4a", options={"title": "Talk"})
        job.summary["saved_to"] = _place_in_videos(context, job, _video(tmp_path))

        again = _place_in_videos(context, job, _video(tmp_path))

        assert again == job.summary["saved_to"]
        assert len(list((tmp_path / "Videos").iterdir())) == 1

    def test_a_checkout_leaves_videos_with_their_job(self, tmp_path: Path) -> None:
        context = _context(tmp_path, None)
        job = context.store.create(audio_name="a.m4a", options={})

        assert _place_in_videos(context, job, _video(tmp_path)) is None


class TestFolderRoutes:
    def _client(self, context: ApiContext) -> TestClient:
        client = TestClient(create_app(context), base_url="http://127.0.0.1:8765")
        client.headers.update({"x-voxframe-token": "t"})
        return client

    def test_folders_are_listed(self, tmp_path: Path) -> None:
        body = self._client(_context(tmp_path, tmp_path / "Videos")).get("/api/folders").json()

        assert body["library"] == str((tmp_path / "library").resolve())
        assert body["videos"] == str((tmp_path / "Videos").resolve())

    def test_only_named_folders_open(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from voxframe.api import desktop

        opened: list[Path] = []
        monkeypatch.setattr(desktop, "open_folder", opened.append)
        client = self._client(_context(tmp_path, tmp_path / "Videos"))

        assert client.post("/api/folders/open", json={"which": "library"}).status_code == 200
        assert opened == [(tmp_path / "library").resolve()]
        assert client.post("/api/folders/open", json={"which": "C:/Windows"}).status_code == 422

    def test_a_chosen_library_is_used_from_the_next_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from voxframe.api import desktop
        from voxframe.api.serve import build_context

        monkeypatch.delenv("VOXFRAME_LIBRARY_PATH", raising=False)
        chosen = tmp_path / "Pictures" / "For videos"
        monkeypatch.setattr(desktop, "choose_folder", lambda title, start=None: chosen)
        client = self._client(_context(tmp_path, None))

        body = client.post("/api/folders/library/choose").json()

        assert body["library_pending"] == str(chosen.resolve())
        monkeypatch.setattr("voxframe.api.serve.get_settings", lambda: Settings(
            cache_path=tmp_path / "cache", output_path=tmp_path / "out", _env_file=None,  # type: ignore[call-arg]
        ))
        assert build_context().settings.library_path == chosen.resolve()

    def test_the_environment_cannot_be_overridden_from_the_page(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXFRAME_LIBRARY_PATH", str(tmp_path / "lib"))

        response = self._client(_context(tmp_path, None)).post("/api/folders/library/choose")

        assert response.status_code == 409
