"""The picture model must work in an installed app, or say clearly why not (D-163).

v0.1.0's installer shipped without ``transformers``, which the default
multilingual model needs for its tokenizer and text encoder. open_clip loads it
lazily, so nothing failed until a render: then every one of 374 downloaded
images was recorded as "failed", the library stayed empty, and a 17-minute
recording rendered with no pictures and a message blaming the search.

These pin the dependency, the loud failure, the readiness check, and the whole
installed-mode path from search to a filled plan.
"""

from __future__ import annotations

import importlib
import importlib.util
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from voxframe.config import paths
from voxframe.config.settings import Settings
from voxframe.library import embeddings
from voxframe.library.embeddings import (
    EMBEDDING_MODELS,
    Embedder,
    EmbedderUnavailable,
    required_modules,
    stack_problem,
)
from voxframe.models.asset import AssetKind, LicenseInfo
from voxframe.models.transcript import Transcript, Word
from voxframe.sourcing.base import Adapter, Candidate, SearchRequest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The distribution that provides each module the picture model needs.
DISTRIBUTION = {"torch": "torch", "open_clip": "open_clip_torch", "transformers": "transformers"}


def _extra(name: str) -> set[str]:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"]["optional-dependencies"][name]
    return {
        requirement.split(">")[0].split("=")[0].split("[")[0].strip().lower()
        for requirement in declared
    }


class TestTheDependencyIsDeclared:
    """The test that would have caught v0.1.0: it read the model's own needs."""

    def test_the_default_model_needs_transformers(self) -> None:
        assert "transformers" in required_modules("default")

    @pytest.mark.parametrize("model_key", sorted(EMBEDDING_MODELS))
    def test_every_module_a_model_needs_is_in_the_library_extra(self, model_key: str) -> None:
        declared = _extra("library")

        missing = [
            DISTRIBUTION[module]
            for module in required_modules(model_key)
            if DISTRIBUTION[module] not in declared
        ]

        assert not missing, (
            f"The {model_key!r} picture model needs {missing}, which the installer "
            f"will not bundle: add them to the 'library' extra in pyproject.toml."
        )

    def test_the_app_extra_brings_the_library_extra(self) -> None:
        """The installers bundle the ``app`` extra (D-158)."""
        (app,) = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]["optional-dependencies"]["app"]
        assert "library" in app


class TestAModelThatCannotLoadSaysSo:
    def test_a_missing_module_is_named(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real = importlib.util.find_spec

        def without_transformers(name: str, package: str | None = None) -> Any:
            return None if name == "transformers" else real(name, package)

        monkeypatch.setattr(importlib.util, "find_spec", without_transformers)

        problem = stack_problem("default")

        assert problem is not None and "transformers" in problem

    def test_loading_fails_once_and_is_remembered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """v0.1.0 tried to load the model again for every batch: 205 times."""
        checks: list[str] = []

        def broken(model_key: str = "default") -> str:
            checks.append(model_key)
            return "the Python module 'transformers' is missing"

        monkeypatch.setattr(embeddings, "stack_problem", broken)
        embedder = Embedder(use_gpu=False)

        for _ in range(3):
            with pytest.raises(EmbedderUnavailable, match="transformers"):
                embedder.embed_texts(["a lighthouse"])

        assert len(checks) == 1

    def test_a_model_that_fails_to_build_says_why(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import open_clip

        monkeypatch.setattr(embeddings, "stack_problem", lambda model_key="default": None)

        def fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("Please `pip install transformers` to use HuggingFace models")

        monkeypatch.setattr(open_clip, "create_model_and_transforms", fail)

        with pytest.raises(EmbedderUnavailable, match="pip install transformers"):
            Embedder(use_gpu=False).load()

    def test_ingest_stops_instead_of_failing_every_file(self, tmp_path: Path) -> None:
        from voxframe.library.db import AssetLibrary
        from voxframe.library.ingest import ingest_directory
        from voxframe.models.provenance import write_provenance

        folder = tmp_path / "sourced"
        folder.mkdir()
        for i in range(3):
            image = folder / f"pexels_{i}.png"
            Image.new("RGB", (64, 64), (i * 60, 90, 30)).save(image)
            write_provenance(image, LicenseInfo(name="CC0-1.0", author="A", source="pexels"))

        class Unavailable:
            model_id = "x/y"

            def embed_images(self, paths: list[Path]) -> list[list[float]]:
                raise EmbedderUnavailable("the Python module 'transformers' is missing")

        with pytest.raises(EmbedderUnavailable):
            ingest_directory(folder, AssetLibrary(tmp_path / "library"), Unavailable())  # type: ignore[arg-type]


class TestReadiness:
    """The Getting-ready screen must not call a half-downloaded model ready."""

    def _need(self) -> Any:
        from voxframe.model_downloads import ModelNeed

        return ModelNeed(
            key="clip:default", label="Pictures", purpose="", megabytes=1465,
            repos=("laion/weights", "xlm-roberta-base"),
        )

    def test_every_repository_must_be_there(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The weights alone pass the size test; the tokenizer never arrived."""
        from voxframe import model_downloads

        sizes = {"laion/weights": 1_464_000_000, "xlm-roberta-base": 0}
        monkeypatch.setattr(model_downloads, "_repo_bytes", sizes.__getitem__)
        monkeypatch.setattr(model_downloads, "_stack_problem", lambda need: None)

        assert not model_downloads.is_ready(self._need())

    def test_the_modules_must_import(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from voxframe import model_downloads

        monkeypatch.setattr(model_downloads, "_repo_bytes", lambda repo: 1_000_000_000)
        monkeypatch.setattr(model_downloads, "_stack_problem", lambda need: "missing")

        assert not model_downloads.is_ready(self._need())

    def test_a_broken_install_is_not_blamed_on_the_connection(self) -> None:
        from voxframe.model_downloads import BrokenInstall, _failure_message

        broken = _failure_message(BrokenInstall("the Python module 'transformers' is missing"))
        offline = _failure_message(OSError("Connection reset"))

        assert "check your internet" not in broken.lower() and "transformers" in broken
        assert "internet connection" in offline


# --- the installed app, from search to a filled plan --------------------------------

COLOURS = {"red": (230, 20, 20), "green": (20, 200, 40), "blue": (20, 40, 230)}


def _colour_vector(index: int) -> list[float]:
    vector = [0.0] * embeddings.EMBEDDING_DIM
    vector[index] = 1.0
    return vector


class ColourEmbedder:
    """Stands in for CLIP: an image is its strongest colour; text, a colour word.

    Enough for the real ingest, library and matcher to fill each scene with the
    right download, without a 1.5 GB model.
    """

    model_id = "test/colour"

    def __init__(self, **_: object) -> None:
        pass

    def load(self) -> None:
        pass

    def embed_images(self, image_paths: list[Path]) -> list[list[float]]:
        vectors = []
        for path in image_paths:
            with Image.open(path) as image:
                pixel = image.convert("RGB").getpixel((0, 0))
            assert isinstance(pixel, tuple)
            vectors.append(_colour_vector(max(range(3), key=lambda i: pixel[i])))
        return vectors

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._text(text) for text in texts]

    def embed_text_chunks(self, text: str) -> list[list[float]]:
        return [self._text(text)]

    def embed_text(self, text: str) -> list[float]:
        return self._text(text)

    def _text(self, text: str) -> list[float]:
        for index, colour in enumerate(COLOURS):
            if colour in text.lower():
                return _colour_vector(index)
        return _colour_vector(10)  # nothing in common with any image


class LocalStock(Adapter):
    """A stock source serving real files, so downloading works offline."""

    name = "pexels"

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.requests = 0

    def search(self, request: SearchRequest) -> tuple[Candidate, ...]:
        self.requests += 1
        found = [colour for colour in COLOURS if colour in request.query.lower()]
        results = []
        for colour in found:
            for n in range(2):
                path = self.folder / f"{colour}-{n}.png"
                if not path.exists():
                    Image.new("RGB", (1920, 1080), COLOURS[colour]).save(path)
                results.append(
                    Candidate(
                        url=path.as_uri(),
                        license=LicenseInfo(name="CC0-1.0", author="A", source="pexels"),
                        width=1920, height=1080, kind=AssetKind.IMAGE,
                        title=f"{colour} photograph", tags=(colour,),
                    )
                )
        return tuple(results[: request.limit])


SENTENCES = [
    "The old red barn stood alone.",
    "Beyond it lay a green meadow.",
    "Far off, the blue sea shone.",
]


def _transcript() -> Transcript:
    words = []
    for s, sentence in enumerate(SENTENCES):
        for w, word in enumerate(sentence.split()):
            start = s * 9.0 + w * 0.8
            words.append(Word(text=word, start=start, end=start + 0.6))
    return Transcript(
        words=tuple(words), language="en", duration=27.0, model_id="test/whisper",
        audio_sha256="0" * 64,
    )


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An installed app, its data in the person's folder under tmp_path (D-156)."""
    monkeypatch.setattr(paths, "is_source_checkout", lambda package=None: False)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(paths, "videos_dir", lambda: tmp_path / "Videos" / "Voxframe")
    for name in ("VOXFRAME_LIBRARY_PATH", "VOXFRAME_CACHE_PATH", "VOXFRAME_OUTPUT_PATH"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _run_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, embedder: type
) -> Any:
    from voxframe.config.settings import QualityPreset
    from voxframe.config.style import get_template
    from voxframe.jobs.pipeline import JobOptions, run_pipeline

    served = tmp_path / "served"
    served.mkdir()
    stock = LocalStock(served)
    transcribed: list[Path] = []

    class FakeTranscriber:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def transcribe(self, audio: Path, *, force: bool = False) -> Transcript:
            transcribed.append(audio)
            return _transcript()

    def render(plan: Any, audio: Path, *args: object, **kwargs: object) -> Any:
        return SimpleNamespace(
            video_path=tmp_path / "out.mp4", elapsed_seconds=1.0, realtime_factor=0.1,
            frame_count=plan.total_frames,
        )

    monkeypatch.setattr("voxframe.library.embeddings.Embedder", embedder)
    monkeypatch.setattr("voxframe.transcribe.whisper.Transcriber", FakeTranscriber)
    monkeypatch.setattr("voxframe.sourcing.registry.build_adapters", lambda *a, **k: [stock])
    monkeypatch.setattr("voxframe.render.compose.from_plan.render_from_plan", render)

    audio = tmp_path / "talk.mp3"
    audio.write_bytes(b"not decoded: transcription is faked")
    settings = Settings(media_mix="stills")
    outcome = run_pipeline(
        JobOptions(
            audio=audio, output=tmp_path / "out.mp4", quality=QualityPreset.DRAFT,
            height=720, chapters=False, source_imagery=True,
            plan_out=tmp_path / "out.plan.json",
        ),
        settings,
        get_template("documentary"),
        SimpleNamespace(),  # type: ignore[arg-type]
    )
    return outcome, settings, transcribed


class TestTheInstalledAppFillsItsScenes:
    """End to end in installed mode: search, download into the person's data
    folder, ingest into its library, match -- the path v0.1.0 broke."""

    def test_every_scene_gets_the_right_download(
        self, installed: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcome, settings, _ = _run_installed(installed, monkeypatch, ColourEmbedder)

        library = installed / "data" / "library"
        assert settings.library_path == library
        assert (library / "library.db").is_file()
        scenes = [scene for scene in outcome.plan.scenes if not scene.is_card]
        assert scenes and all(scene.asset is not None for scene in scenes), [
            scene.match_reason for scene in scenes
        ]
        for scene in scenes:
            assert Path(scene.asset.path).resolve().is_relative_to(library / "sourced")
            colour = next(c for c in COLOURS if c in scene.text.lower())
            assert Path(scene.asset.path).name.startswith("pexels_")
            with Image.open(scene.asset.path) as image:
                assert image.convert("RGB").getpixel((0, 0)) == COLOURS[colour]

    def test_a_model_that_cannot_load_stops_before_transcribing(
        self, installed: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The v0.1.0 case, in the installed layout: fail fast and say why,
        instead of a 17-minute render of plain backgrounds."""
        monkeypatch.setattr(
            embeddings, "stack_problem",
            lambda model_key="default": "the Python module 'transformers' is missing",
        )

        with pytest.raises(EmbedderUnavailable, match="transformers") as failure:
            _run_installed(installed, monkeypatch, Embedder)

        assert "No video was made" in str(failure.value)
