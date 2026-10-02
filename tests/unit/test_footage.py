"""The recording's own picture in the plan, and the choice of shots (D-192)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from voxframe.models.asset import AssetKind
from voxframe.plan import MotionKind, PlannedScene, ScenePlan
from voxframe.plan.builder import insert_cards
from voxframe.plan.highlights import HighlightSelection, build_highlights_plan
from voxframe.plan.scene_plan import Footage, PlanAsset, PlanError, Shot
from voxframe.plan.shots import (
    MAX_CUTAWAY_SECONDS,
    MAX_CUTAWAY_SHARE,
    attach_footage,
    choose_shots,
)
from voxframe.render.compose.footage import crop_window, footage_filter_chain
from voxframe.render.compose.scenes import _footage_signature
from voxframe.render.compose.segment_cache import segment_key
from voxframe.render.motion.speaker import speaker_x_from_motion

FPS = 30.0

FOOTAGE = Footage(path="talk.mp4", width=1920, height=1080, fps=30.0, duration=60.0)


def _asset(name: str) -> PlanAsset:
    return PlanAsset(
        id=name, path=f"{name}.jpg", width=1600, height=900, kind=AssetKind.IMAGE,
        license_name="CC0", license_author="someone", license_source="local",
    )


def _plan(seconds: list[float], scores: list[float | None]) -> ScenePlan:
    """Scenes of the given lengths; a score means a matched picture."""
    scenes, cursor = [], 0
    for index, (length, score) in enumerate(zip(seconds, scores, strict=True)):
        frames = round(length * FPS)
        scenes.append(
            PlannedScene(
                index=index, start_frame=cursor, end_frame=cursor + frames,
                text=f"scene {index}",
                asset=_asset(f"a{index}") if score is not None else None,
                match_score=score or 0.0,
                motion=MotionKind.KEN_BURNS if score is not None else MotionKind.NONE,
            )
        )
        cursor += frames
    return ScenePlan(
        audio_path="talk.mp4", audio_sha256="0" * 64, audio_duration=cursor / FPS,
        fps=FPS, total_frames=cursor, scenes=tuple(scenes),
    )


def _shots(plan: ScenePlan) -> str:
    """``S`` for the speaker, ``P`` for a picture, ``C`` for a card."""
    return "".join(
        "C" if s.is_card else ("S" if plan.shows_speaker(s) else "P") for s in plan.scenes
    )


class TestThePlan:
    def test_an_old_plan_loads_unchanged(self, tmp_path: Path) -> None:
        plan = _plan([3, 3], [0.5, None])
        raw = json.loads(plan.to_json())
        for scene in raw["scenes"]:
            for key in ("shot", "shot_source", "shot_reason", "footage_start"):
                scene.pop(key)
        raw.pop("footage")
        path = tmp_path / "old.plan.json"
        path.write_text(json.dumps(raw), encoding="utf-8")

        loaded = ScenePlan.load(path)

        assert loaded.footage is None
        assert all(scene.shot is Shot.PICTURE for scene in loaded.scenes)
        assert loaded.speaker_scenes == 0

    def test_a_plan_with_footage_round_trips(self, tmp_path: Path) -> None:
        plan = attach_footage(_plan([3, 3, 3], [0.9, 0.9, 0.9]), FOOTAGE)
        path = plan.save(tmp_path / "p.plan.json")

        assert ScenePlan.load(path) == plan

    def test_a_speaker_shot_needs_footage(self) -> None:
        broken = attach_footage(_plan([3, 3], [None, None]), FOOTAGE).model_dump()
        broken["footage"] = None
        # Pydantic reports the plan's own error inside its ValidationError.
        with pytest.raises(ValueError, match="no footage"):
            ScenePlan.model_validate(broken)

    def test_a_speaker_shot_needs_a_start(self) -> None:
        plan = attach_footage(_plan([3, 3], [None, None]), FOOTAGE)
        broken = plan.model_dump()
        broken["scenes"][0]["footage_start"] = None
        with pytest.raises(ValueError, match="footage_start"):
            ScenePlan.model_validate(broken)

    def test_a_hand_edited_plan_file_says_what_is_wrong(self, tmp_path: Path) -> None:
        broken = json.loads(attach_footage(_plan([3, 3], [None, None]), FOOTAGE).to_json())
        broken["footage"] = None
        path = tmp_path / "edited.plan.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(PlanError, match="shows the speaker, but the plan has no footage"):
            ScenePlan.load(path)

    def test_footage_is_not_credited(self) -> None:
        plan = attach_footage(_plan([3, 3], [None, None]), FOOTAGE)
        assert plan.credits() == ()


class TestAttaching:
    def test_each_scene_records_where_it_starts_in_the_recording(self) -> None:
        plan = attach_footage(_plan([2, 3, 4], [None, None, None]), FOOTAGE)
        assert [s.footage_start for s in plan.scenes] == [0.0, 2.0, 5.0]

    def test_cards_move_scenes_but_not_their_place_in_the_recording(self) -> None:
        plan = attach_footage(_plan([2, 3, 4], [None, None, None]), FOOTAGE)
        titled = insert_cards(plan, title="A talk", chapters=False)

        assert _shots(titled) == "CSSS"
        assert [s.footage_start for s in titled.scenes if not s.is_card] == [0.0, 2.0, 5.0]
        assert titled.scenes[1].start_frame == round(3 * FPS)

    def test_highlights_keep_each_scene_on_its_frames(self) -> None:
        plan = attach_footage(_plan([2, 3, 4, 2], [None] * 4), FOOTAGE)
        short = build_highlights_plan(
            plan, HighlightSelection(scene_indices=(1, 3), total_seconds=5.0, reasons={})
        )
        assert [s.footage_start for s in short.scenes] == [2.0, 9.0]
        assert short.scenes[1].start_frame == round(3 * FPS)

    def test_after_cards_it_refuses(self) -> None:
        titled = insert_cards(_plan([2, 3], [None, None]), title="T", chapters=False)
        with pytest.raises(ValueError, match="before cards"):
            attach_footage(titled, FOOTAGE)

    def test_without_cutaways_the_speaker_is_always_on(self) -> None:
        plan = attach_footage(_plan([3] * 5, [0.9] * 5), FOOTAGE, cutaways=False)
        assert _shots(plan) == "SSSSS"


class TestChoosingCutaways:
    def test_the_video_opens_and_closes_on_the_speaker(self) -> None:
        plan = attach_footage(_plan([3] * 5, [0.9] * 5), FOOTAGE)
        shots = _shots(plan)
        assert shots[0] == "S" and shots[-1] == "S"

    def test_never_two_cutaways_in_a_row(self) -> None:
        plan = attach_footage(_plan([2] * 12, [0.9] * 12), FOOTAGE)
        assert "PP" not in _shots(plan)

    def test_the_best_matches_are_taken_first(self) -> None:
        plan = attach_footage(_plan([3] * 5, [None, 0.4, 0.9, 0.5, None]), FOOTAGE)
        assert _shots(plan) == "SSPSS"
        assert plan.scenes[1].shot_reason.endswith("the scene beside it cuts away")

    def test_pictures_cover_at_most_their_share(self) -> None:
        plan = attach_footage(_plan([2] * 30, [0.9] * 30), FOOTAGE)
        pictures = sum(s.duration_frames for s in plan.scenes if not plan.shows_speaker(s))
        assert 0 < pictures <= plan.total_frames * MAX_CUTAWAY_SHARE

    def test_an_unmatched_scene_stays_on_the_speaker(self) -> None:
        plan = attach_footage(_plan([3] * 3, [None] * 3), FOOTAGE)
        assert _shots(plan) == "SSS"
        assert "no picture matched" in plan.scenes[1].shot_reason

    def test_a_long_or_short_scene_stays_on_the_speaker(self) -> None:
        plan = attach_footage(
            _plan([3, MAX_CUTAWAY_SECONDS + 1, 3, 0.8, 3], [0.9] * 5), FOOTAGE
        )
        assert _shots(plan)[1] == "S" and _shots(plan)[3] == "S"
        assert "too long" in plan.scenes[1].shot_reason
        assert "too short" in plan.scenes[3].shot_reason

    def test_a_persons_choice_is_kept(self) -> None:
        plan = attach_footage(_plan([3] * 5, [0.9] * 5), FOOTAGE)
        mine = plan.scenes[0].model_copy(update={"shot": Shot.PICTURE, "shot_source": "user"})
        edited = plan.model_copy(update={"scenes": (mine, *plan.scenes[1:])})

        again = choose_shots(edited)

        assert again.scenes[0].shot is Shot.PICTURE
        assert again.scenes[0].shot_source == "user"
        # Its neighbour does not cut away beside it.
        assert again.scenes[1].shot is Shot.SPEAKER

    def test_every_scene_says_why(self) -> None:
        plan = attach_footage(_plan([3] * 6, [0.9, None, 0.7, 0.8, None, 0.9]), FOOTAGE)
        assert all(scene.shot_reason for scene in plan.scenes)

    def test_without_footage_nothing_changes(self) -> None:
        plan = _plan([3, 3], [0.9, 0.9])
        assert choose_shots(plan) is plan


class TestFraming:
    @pytest.mark.parametrize(
        ("width", "height"), [(608, 1080), (1920, 1080), (1080, 1080), (406, 720)]
    )
    def test_the_crop_fills_the_frame_and_stays_inside(self, width: int, height: int) -> None:
        for x in (0.0, 0.2, 0.5, 0.9, 1.0):
            footage = FOOTAGE.model_copy(update={"subject_x": x})
            scaled_w, scaled_h, left, top = crop_window(footage, width, height)
            assert scaled_w >= width and scaled_h >= height
            assert 0 <= left <= scaled_w - width
            assert 0 <= top <= scaled_h - height

    def test_a_vertical_crop_follows_the_speaker(self) -> None:
        left_side = FOOTAGE.model_copy(update={"subject_x": 0.25})
        scaled_w, _, left, _ = crop_window(left_side, 608, 1080)
        centre = (left + 304) / scaled_w
        assert centre == pytest.approx(0.25, abs=0.01)

    def test_portrait_footage_in_a_landscape_video(self) -> None:
        phone = Footage(path="p.mp4", width=1080, height=1920, fps=30.0, duration=10.0)
        scaled_w, scaled_h, left, top = crop_window(phone, 1920, 1080)
        assert scaled_w == 1920 and scaled_h >= 1080 and left == 0
        # Kept a little above the middle, where a face is.
        assert top < (scaled_h - 1080) / 2

    def test_the_chain_puts_the_grid_first(self) -> None:
        chain = footage_filter_chain(FOOTAGE, 608, 1080, 30.0)
        assert chain.startswith("fps=30.0,")
        assert "crop=608:1080:" in chain and "tpad=stop_mode=clone" in chain


class TestFindingTheSpeaker:
    def test_movement_on_one_side(self) -> None:
        profile = np.ones(160)
        profile[30:60] += 50
        x, concentration = speaker_x_from_motion(profile)
        assert x == pytest.approx(45 / 160, abs=0.02)
        assert concentration > 0.9

    def test_no_movement_is_the_centre(self) -> None:
        assert speaker_x_from_motion(np.full(160, 7.0)) == (0.5, 0.0)

    def test_movement_everywhere_is_not_concentrated(self) -> None:
        rng = np.random.default_rng(1)
        _, concentration = speaker_x_from_motion(rng.uniform(0, 100, 160))
        assert concentration < 0.5


class TestCaching:
    def _key(self, plan: ScenePlan, position: int) -> str:
        scene = plan.scenes[position]
        return segment_key(
            scene, frames=scene.duration_frames, width=640, height=360, fps=FPS,
            motion_signature="m", quality="draft", background="0x000000",
            footage_signature=_footage_signature(plan, scene),
        )

    def test_speaker_and_picture_shots_never_share_a_segment(self, tmp_path: Path) -> None:
        recording = tmp_path / "talk.mp4"
        recording.write_bytes(b"x")
        footage = FOOTAGE.model_copy(update={"path": str(recording)})
        plan = attach_footage(_plan([3, 3, 3], [0.9, 0.9, 0.9]), footage, cutaways=False)
        as_picture = plan.model_copy(
            update={"scenes": tuple(s.model_copy(update={"shot": Shot.PICTURE}) for s in plan.scenes)}
        )
        assert self._key(plan, 1) != self._key(as_picture, 1)

    def test_a_new_recording_at_the_same_path_is_new_frames(self, tmp_path: Path) -> None:
        recording = tmp_path / "talk.mp4"
        recording.write_bytes(b"first")
        footage = FOOTAGE.model_copy(update={"path": str(recording)})
        plan = attach_footage(_plan([3, 3], [None, None]), footage)
        before = self._key(plan, 1)
        recording.write_bytes(b"second, longer")
        assert self._key(plan, 1) != before

    def test_other_scenes_keep_their_keys(self) -> None:
        plan = _plan([3, 3], [0.9, None])
        scene = plan.scenes[0]
        assert _footage_signature(plan, scene) == ""
