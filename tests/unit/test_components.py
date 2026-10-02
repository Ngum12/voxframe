"""Components fetched on first use (D-172): the music-fitting libraries.

Real wheels are zips; these build small ones and serve them from files, so
the whole path -- fetch, check, unpack, import -- runs without a network.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from voxframe import components
from voxframe.components import ComponentError, Manifest, Wheel, install


def _wheel(folder: Path, package: str, source: str = "VALUE = 42\n") -> Wheel:
    path = folder / f"{package}-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{package}/__init__.py", source)
        archive.writestr(f"{package}-1.0.dist-info/METADATA", f"Name: {package}\nVersion: 1.0\n")
    data = path.read_bytes()
    return Wheel(path.name, path.as_uri(), hashlib.sha256(data).hexdigest(), len(data))


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from voxframe.config import paths

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    return tmp_path / "data"


def _manifest(tmp_path: Path, *wheels: Wheel) -> Manifest:
    return Manifest(name="music", wheels=wheels, path=tmp_path / "music.json")


class TestInstall:
    def test_wheels_are_unpacked_and_importable(self, tmp_path: Path, data: Path) -> None:
        served = tmp_path / "served"
        served.mkdir()
        manifest = _manifest(tmp_path, _wheel(served, "vfcomponentprobe"))
        seen: list[tuple[float, float]] = []

        home = install(manifest, lambda done, total: seen.append((done, total)))

        assert (home / ".complete").is_file()
        assert home.is_relative_to(data)
        sys.path.insert(0, str(home))
        try:
            import vfcomponentprobe  # type: ignore[import-not-found]

            assert vfcomponentprobe.VALUE == 42
        finally:
            sys.path.remove(str(home))
            sys.modules.pop("vfcomponentprobe", None)
        assert seen and seen[-1][0] == pytest.approx(seen[-1][1])

    def test_a_second_install_does_nothing(self, tmp_path: Path, data: Path) -> None:
        served = tmp_path / "served"
        served.mkdir()
        manifest = _manifest(tmp_path, _wheel(served, "vfcomponentprobe"))
        home = install(manifest)
        (served / manifest.wheels[0].filename).unlink()  # gone: a refetch would fail

        assert install(manifest) == home

    def test_a_wrong_checksum_installs_nothing(self, tmp_path: Path, data: Path) -> None:
        served = tmp_path / "served"
        served.mkdir()
        good = _wheel(served, "vfcomponentprobe")
        bad = Wheel(good.filename, good.url, "0" * 64, good.size)

        with pytest.raises(ComponentError, match="does not match its SHA-256"):
            install(_manifest(tmp_path, bad))

        assert not list((data / "components").glob("*")) if (data / "components").exists() else True

    def test_a_wheel_writing_outside_its_folder_is_refused(self, tmp_path: Path, data: Path) -> None:
        served = tmp_path / "served"
        served.mkdir()
        path = served / "evil-1.0-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("../../escaped.txt", "no")
        raw = path.read_bytes()
        wheel = Wheel(path.name, path.as_uri(), hashlib.sha256(raw).hexdigest(), len(raw))

        with pytest.raises(ComponentError, match="outside its folder"):
            install(_manifest(tmp_path, wheel))

        assert not (tmp_path / "escaped.txt").exists()

    def test_a_transient_zip_error_is_retried(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        served = tmp_path / "served"
        served.mkdir()
        manifest = _manifest(tmp_path, _wheel(served, "vfcomponentprobe"))
        real_extractall = zipfile.ZipFile.extractall
        failed_once = {"done": False}

        def flaky_extractall(self, path, members=None, _ignored=None):  # type: ignore[no-untyped-def]
            if not failed_once["done"]:
                failed_once["done"] = True
                raise EOFError("truncated archive")
            return real_extractall(self, path, members=members)

        monkeypatch.setattr(components.zipfile.ZipFile, "extractall", flaky_extractall)

        home = install(manifest)

        assert failed_once["done"]
        assert (home / ".complete").is_file()



class TestFindingTheManifest:
    @pytest.mark.parametrize(
        "python,folder",
        [
            ("Voxframe/Python/pythonw.exe", "Voxframe/components"),
            ("Voxframe.app/Contents/Resources/python/bin/python3", "Voxframe.app/Contents/Resources/components"),
        ],
    )
    def test_beside_each_installers_python(self, tmp_path: Path, python: str, folder: str) -> None:
        executable = tmp_path / python
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"")
        (tmp_path / folder).mkdir(parents=True)
        (tmp_path / folder / "music.json").write_text(
            json.dumps({"name": "music", "wheels": [
                {"filename": "a-1-py3-none-any.whl", "url": "u", "sha256": "0" * 64, "size": 2_000_000}
            ]}),
            encoding="utf-8",
        )

        manifest = components.music_manifest(executable)

        assert manifest is not None and manifest.megabytes == pytest.approx(2.0)

    def test_a_checkout_has_none(self, tmp_path: Path) -> None:
        assert components.music_manifest(tmp_path / "bin" / "python") is None


class TestFirstUse:
    """The pipeline fetches it the first time a person's own track needs it."""

    def test_the_download_is_shown_then_used(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from voxframe.jobs import pipeline
        from voxframe.render.audio.music import MusicSettings

        manifest = _manifest(tmp_path, Wheel("x.whl", "u", "0" * 64, 91_000_000))
        monkeypatch.setattr(components, "music_ready", lambda: False)
        monkeypatch.setattr(components, "music_manifest", lambda python=None: manifest)
        installed: list[bool] = []

        def fake_install(progress):  # type: ignore[no-untyped-def]
            progress(45.5, 91.0)
            installed.append(True)
            return tmp_path

        monkeypatch.setattr(components, "install_music", fake_install)
        messages: list[str] = []

        warnings = pipeline._prepare_music(
            MusicSettings(path=Path("track.mp3")), lambda stage, message, fraction: messages.append(message)
        )

        assert installed and warnings == []
        assert "one-time download of about 91 MB" in messages[0]
        assert "46 of 91 MB" in messages[1]

    def test_a_failed_download_is_said_and_the_video_still_made(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from voxframe.jobs import pipeline
        from voxframe.render.audio.music import MusicSettings

        monkeypatch.setattr(components, "music_ready", lambda: False)
        monkeypatch.setattr(
            components, "music_manifest", lambda python=None: _manifest(tmp_path, Wheel("x", "u", "0", 1))
        )

        def failing(progress):  # type: ignore[no-untyped-def]
            raise ComponentError("could not download x: offline")

        monkeypatch.setattr(components, "install_music", failing)

        warnings = pipeline._prepare_music(MusicSettings(path=Path("t.mp3")), lambda *a: None)

        assert len(warnings) == 1 and "simple loop" in warnings[0] and "offline" in warnings[0]

    def test_no_track_no_download(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from voxframe.jobs import pipeline

        monkeypatch.setattr(components, "install_music", lambda progress: pytest.fail("downloaded"))

        assert pipeline._prepare_music(None, lambda *a: None) == []
