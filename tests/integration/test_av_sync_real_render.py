"""Word highlights match the speech in a real render with cards (D-144, D-147).

The owner's decision: a sync regression must fail the build. This renders real
speech -- the public-domain winter sonnet -- through the same pipeline the app
uses, with a title card, adds a chapter card the way the scene plan does, renders
again, and then transcribes the finished video's own soundtrack to check that
every word lights up when it is heard.

It trusts nothing the plan says about timing. D-110's fix passed every check
that did, while captions after a chapter card ran 2s late and titled videos
highlighted every word 3s late.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.config.settings import QualityPreset, get_settings
from voxframe.config.style import get_template
from voxframe.jobs.pipeline import JobOptions, render_plan, run_pipeline
from voxframe.plan.editing import add_chapter
from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.sync_check import card_boundaries, heard, highlights, offsets, stretches

pytestmark = [pytest.mark.slow, pytest.mark.needs_ffmpeg, pytest.mark.needs_models]

REPO_ROOT = Path(__file__).resolve().parents[2]
SONNET = REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav"

#: Largest median offset allowed in any stretch between cards. Transcribed word
#: times wander by a few tenths of a second; the bugs this guards against were
#: 2s and 3s.
TOLERANCE = 0.5


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        return probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")


@pytest.fixture(scope="module")
def whisper_available() -> None:
    try:
        from faster_whisper import WhisperModel

        WhisperModel("base", device="cpu", compute_type="int8", local_files_only=True)
    except Exception:
        pytest.skip("the Whisper base model is not downloaded on this machine")


@pytest.fixture(scope="module")
def rendered(caps, whisper_available, tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    """The sonnet with a title, then a chapter added and the video updated."""
    if not SONNET.is_file():
        pytest.skip("the sonnet sample is missing: run scripts/fetch_samples.py")
    work = tmp_path_factory.mktemp("sync")
    settings = get_settings().model_copy(
        update={
            "library_path": work / "library",
            "cache_path": work / "cache",
            "transcribe_model": "base",
        }
    )
    outcome = run_pipeline(
        JobOptions(
            audio=SONNET,
            output=work / "first.mp4",
            quality=QualityPreset.DRAFT,
            height=240,
            title="A Calendar of Sonnets",
            language="en",
        ),
        settings,
        get_template("documentary"),
        caps,
    )
    assert outcome.plan is not None and outcome.plan_path is not None

    spoken = [scene.index for scene in outcome.plan.scenes if not scene.is_card]
    with_chapter = add_chapter(outcome.plan, spoken[2], "Winter")
    with_chapter.save(outcome.plan_path)

    video = work / "updated.mp4"
    render_plan(
        outcome.plan_path, video, settings, caps, quality=QualityPreset.DRAFT, height=240
    )
    return video, video.with_suffix(".ass"), outcome.plan_path


def test_there_is_a_title_and_a_chapter(rendered) -> None:  # type: ignore[no-untyped-def]
    _, _, plan = rendered

    assert len(card_boundaries(plan)) == 2


def test_every_stretch_between_cards_is_in_step(rendered) -> None:  # type: ignore[no-untyped-def]
    video, ass, plan = rendered

    found = offsets(highlights(ass), heard(video, "en", "base"))
    measured = stretches(found, card_boundaries(plan))

    # Both sides of the chapter card must be measured, or a drift after it
    # could hide behind a correct opening.
    assert len(measured) == 2, f"too few words located: {measured}"
    for stretch in measured:
        assert abs(stretch.median) <= TOLERANCE, (
            f"words from {stretch.start:.1f}s are off by {stretch.median:+.2f}s "
            f"(median of {stretch.words})"
        )
