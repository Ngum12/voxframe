"""Editing title and chapter cards (D-145).

A card adds time to the video, so adding or removing one moves every later
scene -- and its words, which are on the video's clock (D-110, D-144). These
pin that the timeline stays whole, the words move with their scenes, and a
person's text is what the card shows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.plan.editing import (
    EditError,
    add_chapter,
    add_title,
    edit_card_text,
    remove_card,
)
from voxframe.plan.scene_plan import MotionKind, PlannedScene, PlanWord, ScenePlan
from voxframe.render.compose.cards import MAX_CARD_CHARACTERS, card_lines

FPS = 30.0


def _spoken(index: int, start: int, end: int, text: str) -> PlannedScene:
    words = text.split()
    step = (end - start) / FPS / len(words)
    return PlannedScene(
        index=index, start_frame=start, end_frame=end, text=text,
        motion=MotionKind.NONE,
        words=tuple(
            PlanWord(text=word, start=start / FPS + n * step, end=start / FPS + (n + 1) * step)
            for n, word in enumerate(words)
        ),
    )


def _plan(*scenes: PlannedScene) -> ScenePlan:
    return ScenePlan(
        audio_path="a.wav", audio_sha256="0" * 64,
        audio_duration=sum(s.duration_frames for s in scenes if not s.is_card) / FPS,
        fps=FPS, total_frames=scenes[-1].end_frame, scenes=scenes,
    )


def _two_scenes() -> ScenePlan:
    return _plan(
        _spoken(0, 0, 90, "the river runs gold"),
        _spoken(1, 90, 180, "the brothers were cruel"),
    )


def _with_title() -> ScenePlan:
    title = PlannedScene(
        index=0, start_frame=0, end_frame=90, card_kind="title",
        card_text="The Golden River", motion=MotionKind.NONE,
    )
    first = _spoken(1, 90, 180, "the river runs gold")
    second = _spoken(2, 180, 270, "the brothers were cruel")
    return _plan(title, first, second)


class TestAddingATitle:
    def test_everything_moves_up_by_the_title(self) -> None:
        plan = add_title(_two_scenes(), "The Golden River")

        assert plan.scenes[0].card_kind == "title"
        assert plan.scenes[0].card_text == "The Golden River"
        assert plan.scenes[1].start_frame == 90
        assert plan.total_frames == 270

    def test_the_words_move_with_their_scenes(self) -> None:
        """They are on the video's clock (D-110)."""
        before = _two_scenes().scenes[0].words[0].start

        after = add_title(_two_scenes(), "Title").scenes[1].words[0].start

        assert after == pytest.approx(before + 3.0)

    def test_only_one_title(self) -> None:
        with pytest.raises(EditError, match="already has a title"):
            add_title(_with_title(), "Another")

    def test_scenes_are_renumbered(self) -> None:
        plan = add_title(_two_scenes(), "Title")

        assert [scene.index for scene in plan.scenes] == [0, 1, 2]


class TestChapters:
    def test_a_chapter_takes_the_opening_words_by_default(self) -> None:
        plan = add_chapter(_two_scenes(), 1)

        assert plan.scenes[1].card_kind == "chapter"
        assert plan.scenes[1].card_text == "the brothers were cruel"

    def test_a_chapter_can_say_what_the_person_wants(self) -> None:
        assert add_chapter(_two_scenes(), 1, "Part Two").scenes[1].card_text == "Part Two"

    def test_the_scenes_after_it_move(self) -> None:
        plan = add_chapter(_two_scenes(), 1)

        assert plan.scenes[2].start_frame == 90 + 60
        assert plan.scenes[2].words[0].start == pytest.approx(3.0 + 2.0)
        assert plan.scenes[0].words[0].start == pytest.approx(0.0)

    def test_not_at_the_opening(self) -> None:
        with pytest.raises(EditError, match="cannot open the video"):
            add_chapter(_two_scenes(), 0)

    def test_not_straight_after_another_card(self) -> None:
        with_chapter = add_chapter(_with_title(), 2)  # title, scene, chapter, scene

        with pytest.raises(EditError, match="already follows a card"):
            add_chapter(with_chapter, 3)

    def test_not_before_a_card(self) -> None:
        with pytest.raises(EditError, match="spoken scene"):
            add_chapter(_with_title(), 0)


class TestEditingAndRemoving:
    def test_the_text_is_what_the_person_typed(self) -> None:
        plan = edit_card_text(_with_title(), 0, "  A   New Title ")

        assert plan.scenes[0].card_text == "A New Title"
        assert plan.scenes[0].asset_source == "user"

    def test_empty_text_says_to_remove_instead(self) -> None:
        with pytest.raises(EditError, match="remove the card"):
            edit_card_text(_with_title(), 0, "   ")

    def test_too_long_for_a_card(self) -> None:
        with pytest.raises(EditError, match="characters"):
            edit_card_text(_with_title(), 0, "word " * MAX_CARD_CHARACTERS)

    def test_only_cards_have_card_text(self) -> None:
        with pytest.raises(EditError, match="not a title or chapter card"):
            edit_card_text(_with_title(), 1, "text")

    def test_removing_a_card_moves_everything_back(self) -> None:
        original = _with_title()

        plan = remove_card(original, 0)

        assert len(plan.scenes) == 2
        assert plan.scenes[0].start_frame == 0
        assert plan.total_frames == 180
        assert plan.scenes[0].words[0].start == pytest.approx(
            original.scenes[1].words[0].start - 3.0
        )

    def test_add_then_remove_is_where_it_started(self) -> None:
        original = _two_scenes()

        round_trip = remove_card(add_chapter(original, 1), 1)

        assert round_trip.scenes == original.scenes


class TestWrapping:
    def test_a_short_title_is_one_line(self) -> None:
        assert card_lines("The Golden River", width=1920, font_size=92) == ["The Golden River"]

    def test_a_portrait_title_wraps_to_fit(self) -> None:
        """At 9:16, barely a dozen title-sized characters fit across."""
        lines = card_lines("The Golden River", width=1080, font_size=163)

        assert len(lines) > 1
        assert all(len(line) * 163 * 0.56 <= 1080 for line in lines)


# --- the routes ------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi", reason="web extra not installed")

from fastapi.testclient import TestClient  # noqa: E402

from voxframe.api.app import ApiContext, create_app  # noqa: E402
from voxframe.api.security import SessionToken  # noqa: E402
from voxframe.config.settings import Settings  # noqa: E402
from voxframe.jobs.store import JobStore  # noqa: E402


@pytest.fixture
def context(tmp_path: Path) -> ApiContext:
    root = tmp_path / "web"
    library = tmp_path / "library"
    library.mkdir()
    return ApiContext(
        settings=Settings(
            library_path=library, cache_path=tmp_path / "cache",
            output_path=tmp_path / "out",
        ),
        store=JobStore(root, reap_interval=None),
        token=SessionToken("t"),
        allowed_paths=(root.resolve(), library.resolve()),
    )


@pytest.fixture
def client(context: ApiContext) -> TestClient:
    test_client = TestClient(create_app(context), base_url="http://127.0.0.1:8765")
    test_client.headers.update({"x-voxframe-token": "t"})
    return test_client


@pytest.fixture
def job_id(context: ApiContext) -> str:
    job = context.store.create(audio_name="a.wav", options={"height": 480})
    plan_path = context.store.job_directory(job.id) / "source.plan.json"
    _two_scenes().save(plan_path)
    context.store.submit(job, lambda _job: None)
    assert job.future is not None
    job.future.result(timeout=60)
    context.store.record_result(job, artifacts={"plan": plan_path}, warnings=(), summary={})
    return job.id


class TestTheRoutes:
    def test_add_a_title_edit_it_and_remove_it(
        self, client: TestClient, job_id: str
    ) -> None:
        added = client.post(f"/api/jobs/{job_id}/cards", json={"kind": "title", "text": "Gold"})
        assert added.status_code == 200
        assert added.json()["plan"]["scenes"][0]["card_text"] == "Gold"
        assert added.json()["pending_edits"] == 1

        edited = client.put(f"/api/jobs/{job_id}/scenes/0/card", json={"text": "Golden"})
        assert edited.json()["plan"]["scenes"][0]["card_text"] == "Golden"

        removed = client.delete(f"/api/jobs/{job_id}/scenes/0/card")
        assert removed.status_code == 200
        assert len(removed.json()["plan"]["scenes"]) == 2

    def test_a_chapter_before_a_scene(self, client: TestClient, job_id: str) -> None:
        response = client.post(
            f"/api/jobs/{job_id}/cards", json={"kind": "chapter", "before": 1}
        )

        assert response.status_code == 200
        assert response.json()["plan"]["scenes"][1]["card_kind"] == "chapter"

    def test_a_chapter_needs_a_place(self, client: TestClient, job_id: str) -> None:
        response = client.post(f"/api/jobs/{job_id}/cards", json={"kind": "chapter"})

        assert response.status_code == 422
        assert "which scene" in response.json()["detail"]

    def test_the_saved_plan_is_still_whole(
        self, client: TestClient, context: ApiContext, job_id: str
    ) -> None:
        client.post(f"/api/jobs/{job_id}/cards", json={"kind": "title", "text": "Gold"})
        client.post(f"/api/jobs/{job_id}/cards", json={"kind": "chapter", "before": 2})

        plan_path = context.store.artifact_path(job_id, "plan")
        assert plan_path is not None
        saved = ScenePlan.load(plan_path)  # validates the tiling
        assert [scene.card_kind for scene in saved.scenes] == ["title", "", "chapter", ""]
