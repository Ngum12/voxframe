"""The speech waits for every card, measured in the rendered audio (D-144).

A card adds time to the video that the recording does not have. Only cards at
the start used to delay the audio; after a chapter card mid-video, pictures and
captions ran late by the card's length -- 2s after one, 4s after two, measured
by transcribing a real render's soundtrack. Every earlier check trusted the
plan's own timings, which were right; the audio was not.

These render a plan against a recording that is silent except for one tone,
and find the tone in the output.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from voxframe.config.settings import QualityPreset
from voxframe.config.style import get_template
from voxframe.plan import MotionKind, PlannedScene, PlanWord, ScenePlan
from voxframe.plan.highlights import HighlightSelection, audio_ranges
from voxframe.render.compose import render_from_plan
from voxframe.render.compose.from_plan import _narration_graph, _scenes_for_captions
from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import run_ffmpeg

FPS = 30.0

#: When the tone sounds in the recording.
TONE_AT = 2.0


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        return probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")


@pytest.fixture
def recording(caps, tmp_path: Path) -> Path:  # type: ignore[no-untyped-def]
    """Four seconds of silence with a half-second tone at 2.0s."""
    path = tmp_path / "tone.wav"
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"aevalsrc='if(between(t,{TONE_AT},{TONE_AT + 0.5}),sin(2*PI*440*t),0)':s=16000:d=4",
            "-ac", "1", "-y", str(path),
        ],
    )
    return path


def _scene(index: int, start: float, end: float, text: str = "") -> PlannedScene:
    return PlannedScene(
        index=index,
        start_frame=round(start * FPS),
        end_frame=round(end * FPS),
        text=text,
        motion=MotionKind.NONE,
    )


def _card(index: int, start: float, end: float, kind: str) -> PlannedScene:
    return PlannedScene(
        index=index,
        start_frame=round(start * FPS),
        end_frame=round(end * FPS),
        card_kind=kind,
        card_text="Chapter" if kind == "chapter" else "Title",
        motion=MotionKind.NONE,
    )


def _plan(*scenes: PlannedScene) -> ScenePlan:
    return ScenePlan(
        audio_path="tone.wav",
        audio_sha256="0" * 64,
        audio_duration=4.0,
        fps=FPS,
        total_frames=scenes[-1].end_frame,
        scenes=scenes,
    )


def _tone_onset(caps, video: Path) -> float:  # type: ignore[no-untyped-def]
    """When the tone starts in a rendered video: the end of its first silence."""
    result = run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-hide_banner", "-i", str(video),
            "-af", "silencedetect=noise=-30dB:d=0.3",
            "-f", "null", "-",
        ],
        check=False,
    )
    ends = re.findall(r"silence_end: ([\d.]+)", result.stderr)
    assert ends, "no silence found; the tone is missing or the audio is"
    return float(ends[0])


def _render(caps, plan: ScenePlan, recording: Path, tmp_path: Path) -> Path:  # type: ignore[no-untyped-def]
    return render_from_plan(
        plan, recording, get_template(), caps, tmp_path / "out.mp4",
        quality=QualityPreset.DRAFT, height=240, write_sidecars=False,
    ).video_path


@pytest.mark.needs_ffmpeg
class TestTheRenderedAudio:
    def test_a_chapter_card_mid_video_pauses_the_speech(
        self, caps, recording: Path, tmp_path: Path  # type: ignore[no-untyped-def]
    ) -> None:
        """A 1s card inserted at 1.5s: the tone at 2.0s is heard at 3.0s."""
        plan = _plan(
            _scene(0, 0.0, 1.5),
            _card(1, 1.5, 2.5, "chapter"),
            _scene(2, 2.5, 5.0),
        )

        onset = _tone_onset(caps, _render(caps, plan, recording, tmp_path))

        assert onset == pytest.approx(TONE_AT + 1.0, abs=0.08)

    def test_a_title_and_a_chapter_both_count(
        self, caps, recording: Path, tmp_path: Path  # type: ignore[no-untyped-def]
    ) -> None:
        plan = _plan(
            _card(0, 0.0, 1.0, "title"),
            _scene(1, 1.0, 2.5),
            _card(2, 2.5, 3.5, "chapter"),
            _scene(3, 3.5, 6.0),
        )

        onset = _tone_onset(caps, _render(caps, plan, recording, tmp_path))

        assert onset == pytest.approx(TONE_AT + 2.0, abs=0.08)

    def test_a_title_alone_still_delays_the_start(
        self, caps, recording: Path, tmp_path: Path  # type: ignore[no-untyped-def]
    ) -> None:
        plan = _plan(_card(0, 0.0, 1.0, "title"), _scene(1, 1.0, 5.0))

        onset = _tone_onset(caps, _render(caps, plan, recording, tmp_path))

        assert onset == pytest.approx(TONE_AT + 1.0, abs=0.08)

    def test_no_cards_leaves_the_audio_where_it_was(
        self, caps, recording: Path, tmp_path: Path  # type: ignore[no-untyped-def]
    ) -> None:
        plan = _plan(_scene(0, 0.0, 2.0), _scene(1, 2.0, 4.0))

        onset = _tone_onset(caps, _render(caps, plan, recording, tmp_path))

        assert onset == pytest.approx(TONE_AT, abs=0.08)


class TestThePlanClock:
    def test_card_pauses_are_placed_in_recording_time(self) -> None:
        plan = _plan(
            _card(0, 0.0, 1.0, "title"),
            _scene(1, 1.0, 2.5),
            _card(2, 2.5, 3.5, "chapter"),
            _scene(3, 3.5, 6.0),
        )

        assert plan.card_pauses() == ((0.0, 1.0), (1.5, 1.0))

    def test_without_cards_the_narration_is_only_padded(self) -> None:
        assert _narration_graph(_plan(_scene(0, 0.0, 4.0))) == ("apad", False)

    def test_captions_are_not_shifted_twice_by_a_title(self) -> None:
        """Words are already on the video's clock (D-110). Adding the title's
        length again made every titled video's highlights late by it."""
        spoken = PlannedScene(
            index=1, start_frame=30, end_frame=150, text="hello there",
            words=(
                PlanWord(text="hello", start=1.2, end=1.5),
                PlanWord(text="there", start=1.6, end=2.0),
            ),
        )
        plan = _plan(_card(0, 0.0, 1.0, "title"), spoken)

        words = _scenes_for_captions(plan)[0].words

        assert words[0].start == pytest.approx(1.2)

    def test_highlight_cuts_are_taken_from_the_recording(self) -> None:
        """A titled highlight reel cut every range late by the title."""
        plan = _plan(
            _card(0, 0.0, 1.0, "title"),
            _scene(1, 1.0, 2.5),
            _card(2, 2.5, 3.5, "chapter"),
            _scene(3, 3.5, 6.0),
        )
        selection = HighlightSelection(scene_indices=(3,), total_seconds=2.5, reasons={})

        assert audio_ranges(plan, selection) == [pytest.approx((1.5, 4.0))]
