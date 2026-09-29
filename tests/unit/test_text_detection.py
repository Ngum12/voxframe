"""The printed-text check (D-133).

Unit tests on synthetic vectors: the arithmetic, the per-model thresholds, and
that an unmeasured model flags nothing. Whether it actually recognises printed
text is measured on real images in ``tests/eval/test_text_images.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from voxframe.match.text_detection import (
    PHOTO_PROMPTS,
    TEXT_PROMPTS,
    TEXT_THRESHOLDS,
    TextDetector,
    text_score,
)
from voxframe.plan.scene_plan import MotionKind, PlanAsset, PlannedScene, ScenePlan

DIM = len(TEXT_PROMPTS) + len(PHOTO_PROMPTS)


def one_hot(position: int) -> list[float]:
    vector = [0.0] * DIM
    vector[position] = 1.0
    return vector


class FakeEmbedder:
    """Each prompt becomes its own axis, so scores are easy to reason about."""

    def __init__(self) -> None:
        self.calls = 0

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [one_hot(position) for position in range(len(texts))]


class TestScore:
    def test_an_image_like_a_text_prompt_scores_near_one(self) -> None:
        prompts = [one_hot(i) for i in range(DIM)]

        assert text_score(one_hot(0), prompts) > 0.99

    def test_an_image_like_a_photo_prompt_scores_near_zero(self) -> None:
        prompts = [one_hot(i) for i in range(DIM)]

        assert text_score(one_hot(len(TEXT_PROMPTS)), prompts) < 0.01

    def test_the_score_ignores_vector_length(self) -> None:
        """Stored vectors need not be normalised for the score to hold."""
        prompts = [one_hot(i) for i in range(DIM)]

        assert text_score([5.0 * v for v in one_hot(0)], prompts) == pytest.approx(
            text_score(one_hot(0), prompts)
        )


class TestDetector:
    def test_the_standard_model_has_a_measured_threshold(self) -> None:
        assert TextDetector(FakeEmbedder(), "default").available

    def test_the_lite_model_has_its_own_threshold(self) -> None:
        """Models score differently (D-089); one threshold would be wrong."""
        assert TEXT_THRESHOLDS["lite"] != TEXT_THRESHOLDS["default"]

    def test_an_unmeasured_model_flags_nothing(self) -> None:
        """Rather than guess a threshold nobody measured."""
        detector = TextDetector(FakeEmbedder(), "some-new-model")

        assert not detector.available
        assert not detector.shows_text(one_hot(0))

    def test_text_is_flagged(self) -> None:
        assert TextDetector(FakeEmbedder(), "default").shows_text(one_hot(0))

    def test_a_photo_is_not(self) -> None:
        detector = TextDetector(FakeEmbedder(), "default")

        assert not detector.shows_text(one_hot(len(TEXT_PROMPTS)))

    def test_prompts_are_embedded_once(self) -> None:
        """A long plan checks many images; the prompts are fixed."""
        embedder = FakeEmbedder()
        detector = TextDetector(embedder, "default")

        for _ in range(5):
            detector.score(one_hot(0))

        assert embedder.calls == 1


# --- the pipeline step ----------------------------------------------------------


def _asset(asset_id: str) -> PlanAsset:
    return PlanAsset(
        id=asset_id, path=f"/lib/{asset_id}.jpg", width=1600, height=900,
        license_name="CC0", license_author="A", license_source="Test",
    )


def _plan() -> ScenePlan:
    return ScenePlan(
        audio_path="a.wav", audio_sha256="0" * 64, audio_duration=10.0,
        fps=30.0, total_frames=300,
        scenes=(
            PlannedScene(
                index=0, start_frame=0, end_frame=150, text="goals",
                asset=_asset("photo"), alternatives=(_asset("sign"),),
            ),
            PlannedScene(
                index=1, start_frame=150, end_frame=300, text="achieve",
                near_misses=(_asset("hundred-percent"), _asset("seedling")),
                motion=MotionKind.NONE,
            ),
        ),
    )


@pytest.fixture
def stored_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the library holds these vectors: two text images, two photos."""
    vectors = {
        "photo": one_hot(len(TEXT_PROMPTS)),
        "sign": one_hot(1),
        "hundred-percent": one_hot(0),
        "seedling": one_hot(len(TEXT_PROMPTS) + 1),
    }

    class FakeLibrary:
        def __init__(self, root: Path) -> None:
            pass

        def vectors(self, ids: list[str]) -> dict[str, list[float]]:
            return {i: vectors[i] for i in ids if i in vectors}

    monkeypatch.setattr("voxframe.library.db.AssetLibrary", FakeLibrary)


class TestFlaggingThePlan:
    def test_text_images_are_flagged_everywhere_they_appear(
        self, stored_vectors: None
    ) -> None:
        from voxframe.jobs.pipeline import _flag_printed_text

        plan = _flag_printed_text(_plan(), Path("lib"), FakeEmbedder(), "default")  # type: ignore[arg-type]

        assert plan.scenes[0].alternatives[0].prints_text  # a runner-up
        assert plan.scenes[1].near_misses[0].prints_text  # a close match

    def test_ordinary_photos_are_not(self, stored_vectors: None) -> None:
        from voxframe.jobs.pipeline import _flag_printed_text

        plan = _flag_printed_text(_plan(), Path("lib"), FakeEmbedder(), "default")  # type: ignore[arg-type]

        assert plan.scenes[0].asset is not None
        assert not plan.scenes[0].asset.prints_text
        assert not plan.scenes[1].near_misses[1].prints_text

    def test_an_unmeasured_model_leaves_the_plan_alone(
        self, stored_vectors: None
    ) -> None:
        from voxframe.jobs.pipeline import _flag_printed_text

        original = _plan()

        assert _flag_printed_text(original, Path("lib"), FakeEmbedder(), "x") is original  # type: ignore[arg-type]

    def test_the_flag_survives_saving(self, stored_vectors: None, tmp_path: Path) -> None:
        """The scene plan in the browser reads it back from disk."""
        from voxframe.jobs.pipeline import _flag_printed_text

        plan = _flag_printed_text(_plan(), Path("lib"), FakeEmbedder(), "default")  # type: ignore[arg-type]
        plan.save(tmp_path / "plan.json")

        assert ScenePlan.load(tmp_path / "plan.json").scenes[1].near_misses[0].prints_text

    def test_a_plan_written_before_the_flag_still_loads(self, tmp_path: Path) -> None:
        """Older plans have no prints_text field at all."""
        import json

        path = tmp_path / "old.json"
        _plan().save(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        for scene in payload["scenes"]:
            for key in ("asset", "alternatives", "near_misses"):
                items = scene.get(key)
                for item in items if isinstance(items, list) else [items]:
                    if item:
                        item.pop("prints_text", None)
        path.write_text(json.dumps(payload), encoding="utf-8")

        loaded = ScenePlan.load(path)

        assert loaded.scenes[1].near_misses[0].prints_text is False
