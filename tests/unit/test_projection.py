"""The video projected from the plan and its pace (D-199)."""

from __future__ import annotations

import pytest

from voxframe.plan.overlays import Overlay, OverlayKind
from voxframe.plan.pace import Cut, CutKind, PaceEdits, PunchIn
from voxframe.plan.projection import frames_of, project
from voxframe.plan.scene_plan import Footage, PlannedScene, PlanWord, ScenePlan, Shot

FPS = 30.0


def _words(*spec: tuple[str, float, float]) -> tuple[PlanWord, ...]:
    return tuple(PlanWord(text=t, start=s, end=e) for t, s, e in spec)


def _plan(*, card: bool = False, **changes: object) -> ScenePlan:
    """Two spoken scenes of 3 s; optionally a 2 s chapter card between them."""
    first = PlannedScene(
        index=0, start_frame=0, end_frame=90, text="one um two three",
        words=_words(("one", 0.2, 0.5), ("um", 0.8, 1.0), ("two", 1.5, 1.8), ("three", 2.4, 2.8)),
        shot=Shot.SPEAKER, footage_start=0.0,
    )
    scenes: list[PlannedScene] = [first]
    offset = 0
    if card:
        scenes.append(PlannedScene(index=1, start_frame=90, end_frame=150, card_kind="chapter",
                                   card_text="Next"))
        offset = 60
    second = PlannedScene(
        index=len(scenes), start_frame=90 + offset, end_frame=180 + offset, text="four five six",
        words=_words(*((w, 3.2 + offset / FPS + i * 0.8, 3.6 + offset / FPS + i * 0.8)
                       for i, w in enumerate(["four", "five", "six"]))),
        shot=Shot.SPEAKER, footage_start=3.0,
    )
    scenes.append(second)
    return ScenePlan(
        audio_path="a.wav", audio_sha256="0" * 64, audio_duration=6.0, fps=FPS,
        total_frames=180 + offset, scenes=tuple(scenes),
        footage=Footage(path="v.mp4", width=1280, height=720, fps=30.0, duration=6.0),
        **changes,  # type: ignore[arg-type]
    )


def _cut(start: float, end: float, kind: CutKind = CutKind.SILENCE, on: bool = True) -> Cut:
    return Cut(start=start, end=end, kind=kind, on=on)


def test_nothing_cut_changes_nothing() -> None:
    plan = _plan()
    projection = project(plan)
    assert projection.identity and projection.plan is plan


def test_a_cut_off_is_shown_but_not_made() -> None:
    plan = _plan(pace=PaceEdits(cuts=(_cut(1.0, 1.4, on=False),)))
    assert project(plan).identity


class TestCuts:
    def test_a_pause_cut_shortens_the_scene_and_moves_what_follows(self) -> None:
        plan = _plan(pace=PaceEdits(cuts=(_cut(1.0, 1.4),)))
        video = project(plan).plan
        assert video.total_frames == 180 - 12
        first, second = video.scenes
        assert first.end_frame == 78 and second.start_frame == 78
        # "two" was at 1.5 s; 0.4 s earlier now.
        assert first.words[2].start == pytest.approx(1.1)
        assert second.words[0].start == pytest.approx(3.2 - 0.4)

    def test_the_speaker_plays_the_stretches_either_side_of_it(self) -> None:
        video = project(_plan(pace=PaceEdits(cuts=(_cut(1.0, 1.4),)))).plan
        spans = video.scenes[0].footage_spans
        assert [(s.source, s.seconds) for s in spans] == [(0.0, 1.0), (1.4, 1.6)]
        # The second stretch a little closer, so the jump reads as a cut.
        assert [s.zoom for s in spans] == [1.0, 1.08]
        # An uncut scene keeps the plain path.
        assert video.scenes[1].footage_spans == () and video.scenes[1].footage_start == 3.0

    def test_a_cut_filler_word_is_gone(self) -> None:
        video = project(_plan(pace=PaceEdits(cuts=(_cut(0.8, 1.0, CutKind.FILLER),)))).plan
        assert [w.text for w in video.scenes[0].words] == ["one", "two", "three"]

    def test_the_recording_is_cut_where_the_pictures_are(self) -> None:
        projection = project(_plan(pace=PaceEdits(cuts=(_cut(1.0, 1.4),))))
        assert projection.source_pieces() == [(0.0, 1.0), (1.4, 3.0), (3.0, 6.0)]
        assert projection.plan.audio_duration == pytest.approx(5.6)

    def test_a_scene_cut_away_entirely_is_gone(self) -> None:
        plan = _plan(
            pace=PaceEdits(cuts=(_cut(3.0, 6.0, CutKind.MANUAL),)),
            overlays=(Overlay(id="a", kind=OverlayKind.TEXT, text="x", scene=1, word=0),),
        )
        video = project(plan).plan
        assert len(video.scenes) == 1 and video.total_frames == 90
        assert video.overlays == ()

    def test_pop_ups_stay_on_their_words(self) -> None:
        plan = _plan(
            pace=PaceEdits(cuts=(_cut(0.8, 1.0, CutKind.FILLER),)),
            overlays=(Overlay(id="a", kind=OverlayKind.TEXT, text="x", scene=0, word=2),),
        )
        projection = project(plan)
        overlay = projection.plan.overlays[0]
        # "two" was word 2 and is word 1 now; it shows when "two" is said.
        assert overlay.word == 1
        start, _ = projection.plan.overlay_times(overlay)
        assert start == pytest.approx(projection.story_to_video(1.5))


class TestCards:
    def test_the_recording_skips_the_card_and_is_cut_in_its_own_time(self) -> None:
        projection = project(_plan(card=True, pace=PaceEdits(cuts=(_cut(5.2, 5.6),))))
        # The second scene is 5.0-8.0 on the plan's clock, 3.0-6.0 in the
        # recording: the cut at 5.2-5.6 is 3.2-3.6 there.
        assert projection.source_pieces() == [(0.0, 3.0), (3.0, 3.2), (3.6, 6.0)]
        video = projection.plan
        assert video.scenes[1].is_card and video.card_pauses() == ((3.0, 2.0),)


class TestColdOpen:
    def test_the_hook_plays_first_and_again_in_its_place(self) -> None:
        projection = project(_plan(pace=PaceEdits(cold_open=(3.9, 4.8))))
        video = projection.plan
        teaser = video.scenes[0]
        assert teaser.teaser and teaser.story_index == 1
        assert [w.text for w in teaser.words] == ["five"]
        assert video.total_frames == round((0.9 + 6.0) * FPS)
        assert projection.source_pieces()[0] == pytest.approx((3.9, 4.8))
        assert projection.scene_map == {0: 1, 1: 2}
        assert projection.story_to_video(4.0) == pytest.approx(0.9 + 4.0)


class TestPunchIns:
    def test_a_punch_in_lands_on_its_word_in_its_scene(self) -> None:
        plan = _plan(pace=PaceEdits(
            cuts=(_cut(1.0, 1.4),), punch_ins=(PunchIn(start=4.0, end=4.6, word="five"),)
        ))
        video = project(plan).plan
        (zoom,) = video.scenes[1].zooms
        assert zoom.start == pytest.approx(1.0) and zoom.end == pytest.approx(1.6)


def test_frames_add_up() -> None:
    assert sum(frames_of([0.333, 0.333, 0.334], 30.0)) == 30
