"""Cuts, a cold open and punch-ins keep the speaker on their voice (D-199).

The clapper recording of the footage tests (D-192): a white flash in the
picture at the very moment a tone sounds. Cut, reordered and zoomed, the
finished video must still have every flash on its tone, at the time the
cuts put it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.test_footage_sync import (
    FPS,
    TOLERANCE,
    _flashes,
    _footage,
    _plan,
    _recording,
    _speaker,
    _tones,
    caps,
)
from voxframe.config.settings import QualityPreset
from voxframe.config.style import get_template
from voxframe.plan import PlannedScene
from voxframe.plan.pace import Cut, CutKind, PaceEdits, PunchIn
from voxframe.render.compose import render_from_plan

__all__ = ["caps"]  # the fixture, shared

pytestmark = [pytest.mark.slow, pytest.mark.needs_ffmpeg]


def _render(caps, tmp_path: Path, plan, name: str) -> Path:  # type: ignore[no-untyped-def]
    template = get_template().derive(motion={"transition": "cut"})
    return render_from_plan(
        plan, Path(plan.audio_path), template, caps, tmp_path / name,
        quality=QualityPreset.DRAFT, height=360, cache_dir=tmp_path / "cache" / "segments",
    ).video_path


def _matched(flashes: list[float], tones: list[float], expected: list[float]) -> None:
    # The silence detector also marks where the file ends.
    tones = [tone for tone in tones if tone < expected[-1] + 1.0]
    assert len(flashes) == len(tones) == len(expected), (flashes, tones)
    for flash, tone, at in zip(flashes, tones, expected, strict=True):
        assert abs(flash - tone) <= TOLERANCE, (flash, tone)
        assert abs(tone - at) <= TOLERANCE, (tone, at)


def test_a_cut_and_a_cold_open_keep_every_flash_on_its_tone(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    recording = _recording(caps, tmp_path / "talk.mp4")
    footage = _footage(caps, recording)
    plan = _plan(footage, _speaker(0, 0.0, 3.5, 0.0), _speaker(1, 3.5, 7.0, 3.5))
    paced = plan.model_copy(update={"pace": PaceEdits(
        # A pause between the claps, and the second clap's line played first.
        cuts=(Cut(start=2.0, end=2.8, kind=CutKind.SILENCE),),
        cold_open=(4.2, 5.0),
        punch_ins=(PunchIn(start=4.4, end=4.8, word="clap"),),
    )})
    video = _render(caps, tmp_path, paced, "paced.mp4")

    # The cold open (0.8 s) shows the second clap 0.3 s in; then the first
    # clap at 0.8 + 1.5; then the second again, 0.8 s of pause earlier.
    _matched(_flashes(caps, video), _tones(caps, video), [0.3, 2.3, 4.5])


def test_cuts_after_a_card_are_cut_in_the_recordings_own_time(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    recording = _recording(caps, tmp_path / "talk.mp4")
    footage = _footage(caps, recording)
    card = PlannedScene(index=1, start_frame=round(3.5 * FPS), end_frame=round(5.5 * FPS),
                        card_kind="chapter", card_text="Next")
    second = _speaker(2, 5.5, 9.0, 3.5)
    plan = _plan(footage, _speaker(0, 0.0, 3.5, 0.0), card, second)
    # On the plan's clock the second clap is at 4.5 + 2.0 (the card): cut
    # 0.5 s of the pause before it.
    paced = plan.model_copy(update={"pace": PaceEdits(
        cuts=(Cut(start=5.8, end=6.3, kind=CutKind.SILENCE),)
    )})
    video = _render(caps, tmp_path, paced, "card.mp4")
    _matched(_flashes(caps, video), _tones(caps, video), [1.5, 6.5 - 0.5])


def test_a_punch_in_zooms_in_and_back_out(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """A still photograph as the recording: every frame alike, but for the zoom."""
    import numpy as np
    from PIL import Image

    from voxframe.render.ffpath import run_ffmpeg

    still = Path(__file__).resolve().parents[1] / "fixtures" / "astronaut_collins.jpg"
    recording = tmp_path / "still.mp4"
    run_ffmpeg(caps.ffmpeg_path, [
        "-loglevel", "error", "-loop", "1", "-framerate", "30", "-t", "4", "-i", str(still),
        "-f", "lavfi", "-i", "sine=f=220:d=4", "-shortest", "-vf", "scale=640:-2,format=yuv420p",
        "-c:a", "aac", "-y", str(recording),
    ])
    footage = _footage(caps, recording)
    plan = _plan(footage, _speaker(0, 0.0, 3.5, 0.0))
    plan = plan.model_copy(update={"audio_duration": 3.5, "pace": PaceEdits(
        punch_ins=(PunchIn(start=1.0, end=1.5, zoom=1.2),)
    )})
    video = _render(caps, tmp_path, plan, "punch.mp4")

    def frame(seconds: float) -> np.ndarray:
        out = tmp_path / f"f{seconds}.png"
        run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-i", str(video), "-vf",
                                      f"select=eq(n\\,{round(seconds * FPS)})", "-frames:v", "1",
                                      "-y", str(out)])
        return np.asarray(Image.open(out).convert("L")).astype(float)

    before, during, after = frame(0.5), frame(1.4), frame(2.8)
    assert np.abs(before - after).mean() < 2
    assert np.abs(before - during).mean() > 8
