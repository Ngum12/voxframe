"""The camera that follows the speaker's face, and captions kept clear of it (D-193)."""

from __future__ import annotations

import itertools
import re

import pytest

from voxframe.config.style import CaptionPosition, get_template
from voxframe.models.scene import Scene
from voxframe.models.transcript import Word
from voxframe.plan import MotionKind, PlannedScene, ScenePlan
from voxframe.plan.scene_plan import Footage, PlanError, Shot
from voxframe.render.captions.ass import TOP_STYLE, build_ass
from voxframe.render.compose.footage import face_extent, footage_filter_chain, track_between
from voxframe.render.compose.from_plan import _captions_above_face
from voxframe.render.motion.faces import (
    DEAD_ZONE,
    MIN_COVERAGE,
    SAMPLES_PER_SECOND,
    FaceSample,
    detector_available,
    plan_camera,
    primary_faces,
)

#: A vertical crop of landscape footage keeps this share of its width.
VERTICAL = (9 / 16) / (16 / 9)


def _samples(xs: list[float | None], y: float = 0.4, h: float = 0.2) -> list[FaceSample | None]:
    return [
        None if x is None else FaceSample(t=i / SAMPLES_PER_SECOND, x=x, y=y, h=h)
        for i, x in enumerate(xs)
    ]


def _at(path: list, t: float) -> float:  # type: ignore[type-arg]
    """The path's x at ``t``, linear between keyframes."""
    for a, b in itertools.pairwise(path):
        if a.t <= t <= b.t:
            return a.x if b.t == a.t else a.x + (b.x - a.x) * (t - a.t) / (b.t - a.t)
    return path[-1].x if t > path[-1].t else path[0].x


class TestTheCamera:
    def test_a_still_speaker_is_one_still_shot(self) -> None:
        path = plan_camera(_samples([0.3] * 40), crop_width=VERTICAL, crop_height=1)
        assert path is not None
        assert {k.x for k in path} == {0.3}
        assert len(path) == 2

    def test_small_movements_do_not_move_the_camera(self) -> None:
        wobble = DEAD_ZONE * VERTICAL * 0.8
        xs = [0.5 + (wobble if i % 3 else -wobble) for i in range(40)]
        path = plan_camera(_samples(xs), crop_width=VERTICAL, crop_height=1)
        assert path is not None
        assert len({k.x for k in path}) == 1

    def test_a_walk_is_one_smooth_move_that_keeps_up(self) -> None:
        # Still at 0.2 for 3 s, walks to 0.75 over 2 s, then still.
        def where(t: float) -> float:
            return 0.2 if t < 3 else 0.75 if t > 5 else 0.2 + (t - 3) / 2 * 0.55

        xs = [where(i / SAMPLES_PER_SECOND) for i in range(10 * SAMPLES_PER_SECOND)]
        path = plan_camera(_samples(xs), crop_width=VERTICAL, crop_height=1)
        assert path is not None
        positions = [k.x for k in path]
        # One move: never turns back.
        assert positions == sorted(positions)
        assert positions[0] == pytest.approx(0.2) and positions[-1] == pytest.approx(0.75, abs=0.01)
        # Throughout, the face stays well inside the crop.
        for i in range(10 * SAMPLES_PER_SECOND):
            t = i / SAMPLES_PER_SECOND
            assert abs(where(t) - _at(path, t)) < 0.35 * VERTICAL, t

    def test_a_brief_miss_does_not_move_the_camera(self) -> None:
        xs: list[float | None] = [0.3] * 40
        xs[10:13] = [None, None, None]
        path = plan_camera(_samples(xs), crop_width=VERTICAL, crop_height=1)
        assert path is not None and {k.x for k in path} == {0.3}

    def test_a_one_frame_glitch_is_ignored(self) -> None:
        xs = [0.3] * 40
        xs[20] = 0.9
        path = plan_camera(_samples(xs), crop_width=VERTICAL, crop_height=1)
        assert path is not None and {k.x for k in path} == {0.3}

    def test_faces_too_rare_give_no_path(self) -> None:
        seen = int(40 * MIN_COVERAGE) - 1
        xs: list[float | None] = [0.3] * seen + [None] * (40 - seen)
        assert plan_camera(_samples(xs), crop_width=VERTICAL, crop_height=1) is None
        assert plan_camera([], crop_width=VERTICAL, crop_height=1) is None

    def test_the_path_is_in_time_order_and_within_the_frame(self) -> None:
        xs = [0.1 + 0.8 * ((i // 20) % 2) for i in range(120)]
        path = plan_camera(_samples(xs), crop_width=VERTICAL, crop_height=1)
        assert path is not None
        assert [k.t for k in path] == sorted(k.t for k in path)
        assert all(0 <= k.x <= 1 for k in path)


class TestChoosingTheSpeaker:
    def test_the_largest_face_at_the_start(self) -> None:
        chosen = primary_faces([[(0.2, 0.4, 0.1, 0.9, 0.01), (0.7, 0.4, 0.3, 0.9, 0.09)]])
        assert chosen[0] is not None and chosen[0].x == 0.7

    def test_someone_passing_does_not_take_the_camera(self) -> None:
        speaker = (0.3, 0.4, 0.2, 0.9, 0.04)
        passer = (0.8, 0.4, 0.25, 0.9, 0.06)  # larger, but elsewhere
        chosen = primary_faces([[speaker]] * 4 + [[speaker, passer]] * 4)
        assert all(sample is not None and sample.x == 0.3 for sample in chosen)

    def test_a_miss_is_recorded_as_none(self) -> None:
        assert primary_faces([[], [(0.5, 0.5, 0.2, 0.9, 0.04)]])[0] is None


FOOTAGE = Footage(path="talk.mp4", width=1280, height=720, fps=30.0, duration=10.0)


def _tracked(*points: tuple[float, float, float, float]) -> Footage:
    # Built, not copied: copying skips the checks a plan gets when it loads.
    return Footage(
        **{**FOOTAGE.model_dump(), "track": [
            {"t": t, "x": x, "y": y, "h": h} for t, x, y, h in points
        ]}
    )


class TestThePathInThePlan:
    def test_old_plans_have_no_path(self) -> None:
        assert FOOTAGE.track == ()

    def test_points_must_be_in_order(self) -> None:
        with pytest.raises(ValueError, match="time order"):
            _tracked((1.0, 0.5, 0.5, 0.1), (0.5, 0.5, 0.5, 0.1))

    def test_a_segment_takes_its_stretch_with_exact_ends(self) -> None:
        footage = _tracked((0, 0.2, 0.4, 0.1), (4, 0.6, 0.4, 0.1))
        stretch = track_between(footage.track, 1.0, 3.0)
        assert [round(p.t, 3) for p in stretch] == [1.0, 3.0]
        assert stretch[0].x == pytest.approx(0.3) and stretch[1].x == pytest.approx(0.5)

    def test_beyond_the_path_it_holds(self) -> None:
        footage = _tracked((2, 0.2, 0.4, 0.1), (3, 0.6, 0.4, 0.1))
        stretch = track_between(footage.track, 5.0, 6.0)
        assert {p.x for p in stretch} == {0.6}

    def test_the_crop_moves_and_stays_in_the_frame(self) -> None:
        footage = _tracked((0, 0.2, 0.4, 0.1), (3, 0.2, 0.4, 0.1), (4, 0.7, 0.4, 0.1))
        chain = footage_filter_chain(footage, 406, 720, 30.0, start=2.0, seconds=3.0)
        crop = re.search(r"crop=406:720:x='(.+?)':y='(.+?)'", chain)
        assert crop is not None
        assert crop.group(1).startswith("clip(") and crop.group(1).endswith(",0,874)")
        assert "if(lt(t," in crop.group(1)
        assert crop.group(2) == "0"  # the crop is the footage's full height

    def test_without_a_path_the_crop_is_still(self) -> None:
        chain = footage_filter_chain(FOOTAGE, 406, 720, 30.0, start=2.0, seconds=3.0)
        assert re.search(r"crop=406:720:\d+:\d+", chain)


def _plan(footage: Footage) -> ScenePlan:
    scenes = (
        PlannedScene(
            index=0, start_frame=0, end_frame=90, text="hello there",
            shot=Shot.SPEAKER, footage_start=0.0, motion=MotionKind.NONE,
        ),
        PlannedScene(
            index=1, start_frame=90, end_frame=180, text="a river",
            shot=Shot.SPEAKER, footage_start=3.0, motion=MotionKind.NONE,
        ),
    )
    return ScenePlan(
        audio_path="talk.mp4", audio_sha256="0" * 64, audio_duration=6.0, fps=30.0,
        total_frames=180, scenes=scenes, footage=footage,
    )


class TestCaptionsClearOfTheFace:
    WIDTH, HEIGHT = 360, 640

    def test_where_the_face_reaches_in_the_frame(self) -> None:
        footage = _tracked((0, 0.5, 0.5, 0.2))
        top, bottom = face_extent(footage, self.WIDTH, self.HEIGHT, 0.0, 3.0)  # type: ignore[misc]
        # Centre at half the height; the box is 0.2 high, grown for the head.
        assert (top + bottom) / 2 == pytest.approx(self.HEIGHT / 2)
        assert bottom - top == pytest.approx(0.2 * self.HEIGHT * 1.5)

    def test_unknown_without_a_path(self) -> None:
        assert face_extent(FOOTAGE, self.WIDTH, self.HEIGHT, 0.0, 3.0) is None

    def test_a_low_face_sends_its_captions_up(self) -> None:
        # High in the first scene, low in the second.
        footage = _tracked((0, 0.5, 0.35, 0.15), (2.9, 0.5, 0.35, 0.15), (3.0, 0.5, 0.85, 0.15))
        moved = _captions_above_face(
            _plan(footage), get_template("clean-educational"), self.WIDTH, self.HEIGHT
        )
        assert moved == frozenset({1})

    def test_a_face_filling_the_frame_keeps_them_down(self) -> None:
        footage = _tracked((0, 0.5, 0.5, 0.7))
        assert _captions_above_face(
            _plan(footage), get_template("clean-educational"), self.WIDTH, self.HEIGHT
        ) == frozenset()

    def test_a_picture_scene_keeps_them_down(self) -> None:
        plan = _plan(_tracked((0, 0.5, 0.85, 0.15)))
        pictures = plan.model_copy(
            update={"scenes": tuple(s.model_copy(update={"shot": Shot.PICTURE}) for s in plan.scenes)}
        )
        assert _captions_above_face(
            pictures, get_template("clean-educational"), self.WIDTH, self.HEIGHT
        ) == frozenset()

    def test_a_template_with_captions_elsewhere_is_left_alone(self) -> None:
        template = get_template("clean-educational")
        centred = template.model_copy(
            update={"captions": template.captions.model_copy(update={"position": CaptionPosition.CENTER})}
        )
        assert _captions_above_face(
            _plan(_tracked((0, 0.5, 0.85, 0.15))), centred, self.WIDTH, self.HEIGHT
        ) == frozenset()

    def test_the_caption_file_uses_the_top_style_for_those_scenes(self) -> None:
        def scene(index: int, start: float, text: str) -> Scene:
            words = tuple(
                Word(text=w, start=start + i * 0.3, end=start + i * 0.3 + 0.25)
                for i, w in enumerate(text.split())
            )
            return Scene(index=index, start_frame=round(start * 30), end_frame=round(start * 30) + 60, words=words)

        ass = build_ass(
            (scene(0, 0.0, "hello there"), scene(1, 2.0, "a river")),
            get_template().captions, 360, 640, 30.0, top_scenes=frozenset({1}),
        )
        assert f"Style: {TOP_STYLE}," in ass
        lines = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        assert all(",Voxframe," in line for line in lines if "hello" in line)
        assert all(f",{TOP_STYLE}," in line for line in lines if "river" in line)


def test_the_detector_ships_with_voxframe() -> None:
    """The model is in the package; OpenCV is needed only to run it."""
    from voxframe.render.motion.faces import MODEL_PATH

    assert MODEL_PATH.is_file()
    assert MODEL_PATH.stat().st_size == 232_589
    pytest.importorskip("cv2")
    assert detector_available()


def test_a_bad_path_is_refused_when_loaded(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json

    plan = _plan(_tracked((0, 0.5, 0.5, 0.1)))
    raw = json.loads(plan.to_json())
    raw["footage"]["track"] = [
        {"t": 2, "x": 0.5, "y": 0.5, "h": 0.1},
        {"t": 1, "x": 0.5, "y": 0.5, "h": 0.1},
    ]
    path = tmp_path / "p.plan.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PlanError, match="time order"):
        ScenePlan.load(path)
