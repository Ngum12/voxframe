"""A real video with a generated score (D-176), on the public-domain sonnet.

The score is made from the real samples, mixed under the voice by the same
rules as any music (D-171) and checked; a change to the mix alone re-renders
only the sound and reuses the score from the cache; and the credits name it.

Needs the score's samples: the setting ``VOXFRAME_SCORE_SAMPLES_PATH``, or
the downloaded pack. Skipped without them.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("scipy", reason="music tools not installed")
soundfile = pytest.importorskip("soundfile")

from voxframe.plan.audio_mix import AudioMix, Destination, ScoreLevels  # noqa: E402
from voxframe.plan.scene_plan import ScenePlan  # noqa: E402
from voxframe.plan.score_choice import ScoreChoice  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SONNET = REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav"


@pytest.fixture(scope="module")
def scored(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    from voxframe.config.settings import QualityPreset, get_settings
    from voxframe.config.style import get_template
    from voxframe.jobs.pipeline import JobOptions, render_plan, run_pipeline
    from voxframe.music.score import samples_dir
    from voxframe.music.score.instruments import SampleLibrary, SamplesMissing
    from voxframe.render.encode.probe import probe_capabilities

    try:
        SampleLibrary(samples_dir()).check()
    except SamplesMissing as exc:
        pytest.skip(f"the score's samples are not installed: {exc}")
    try:
        caps = probe_capabilities()
    except Exception as exc:  # pragma: no cover - FFmpeg is part of the dev setup
        pytest.skip(f"FFmpeg not available: {exc}")

    work = tmp_path_factory.mktemp("score")
    settings = get_settings().model_copy(
        update={"transcribe_model": "base", "cache_path": work / "cache"}
    )
    output = work / "out" / "talk.mp4"
    started = time.monotonic()
    first = run_pipeline(
        JobOptions(
            audio=SONNET,
            output=output,
            quality=QualityPreset.DRAFT,
            height=360,
            chapters=False,
            title="A Calendar of Sonnets",
            language="en",
            score=ScoreChoice(style="inspiring", seed=1234),
        ),
        settings,
        get_template("documentary"),
        caps,
    )
    first_seconds = time.monotonic() - started
    scores_after_first = _scores(work)
    pictures_after_first = _pictures(work)

    assert first.plan_path is not None
    plan = ScenePlan.load(first.plan_path)
    plan.model_copy(
        update={"audio_mix": AudioMix(music_db=-3.0, destination=Destination.PODCAST)}
    ).save(first.plan_path)
    second = render_plan(
        first.plan_path, output, settings, caps, quality=QualityPreset.DRAFT, height=360
    )
    scores_after_second = _scores(work)

    def again(update: dict) -> dict:  # type: ignore[type-arg]
        """Edit the plan as the Sound card does, and update the video."""
        current = ScenePlan.load(first.plan_path)
        current.model_copy(update=update).save(first.plan_path)
        started = time.monotonic()
        result = render_plan(
            first.plan_path, output, settings, caps, quality=QualityPreset.DRAFT, height=360
        )
        return {"result": result, "scores": _scores(work), "seconds": time.monotonic() - started}

    # D-179: group levels, a new variation, and more intensity.
    levels = again(
        {"audio_mix": AudioMix(score_levels=ScoreLevels(percussion=-30.0, pads=3.0))}
    )
    variation = again(
        {"score": ScoreChoice(style="inspiring", seed=99), "audio_mix": AudioMix()}
    )
    intensity = again({"score": ScoreChoice(style="inspiring", seed=99, intensity=0.8)})
    return {
        "first": first,
        "second": second,
        "plan": plan,
        "seconds": first_seconds,
        "scores": (scores_after_first, scores_after_second),
        "levels": levels,
        "variation": variation,
        "intensity": intensity,
        "pictures": (pictures_after_first, _pictures(work)),
        "work": work,
    }


def _scores(work: Path) -> dict[Path, int]:
    """The composed scores (their sums, not their groups or level mixes) and their times."""
    return {
        p: p.stat().st_mtime_ns
        for p in (work / "cache" / "sound").glob("score_*.flac")
        if "." not in p.stem
    }


def _pictures(work: Path) -> dict[Path, int]:
    return {p: p.stat().st_mtime_ns for p in (work / "cache" / "pictures").glob("*.mp4")}


def test_the_scored_video_passes_every_check(scored) -> None:  # type: ignore[no-untyped-def]
    sound = scored["first"].result.sound

    assert sound["passed"], sound["problems"]
    assert sound["min_speech_margin_db"] == pytest.approx(15.0, abs=0.5)
    assert sound["integrated_lufs"] == pytest.approx(-14.0, abs=1.0)
    assert sound["clicks_at"] == []
    assert not any("music" in w.lower() for w in scored["first"].warnings), scored["first"].warnings


def test_the_plan_records_the_choice_and_credits_it(scored) -> None:  # type: ignore[no-untyped-def]
    plan = scored["plan"]

    assert plan.score == ScoreChoice(style="inspiring", seed=1234)
    assert "generated by Voxframe" in plan.credits()[-1]


def test_a_mix_change_reuses_the_score(scored) -> None:  # type: ignore[no-untyped-def]
    after_first, after_second = scored["scores"]

    assert len(after_first) == 1
    assert after_second == after_first
    second = scored["second"].result.sound
    assert second["passed"], second["problems"]
    assert second["integrated_lufs"] == pytest.approx(-16.0, abs=1.0)


def test_group_levels_remix_without_composing_again(scored) -> None:  # type: ignore[no-untyped-def]
    after_mix_change = scored["scores"][1]
    levels = scored["levels"]

    assert levels["scores"] == after_mix_change  # the same score, not composed again
    assert list((scored["work"] / "cache" / "sound").glob("score_*.levels_*.wav"))
    sound = levels["result"].result.sound
    assert sound["passed"], sound["problems"]
    assert sound["min_speech_margin_db"] == pytest.approx(15.0, abs=0.5)


def test_a_new_variation_and_more_intensity_compose_new_scores(scored) -> None:  # type: ignore[no-untyped-def]
    before = set(scored["levels"]["scores"])
    after_variation = set(scored["variation"]["scores"])
    after_intensity = set(scored["intensity"]["scores"])

    assert len(after_variation - before) == 1
    assert len(after_intensity - after_variation) == 1
    for step in ("variation", "intensity"):
        sound = scored[step]["result"].result.sound
        assert sound["passed"], (step, sound["problems"])


def test_music_changes_never_touch_the_pictures(scored) -> None:  # type: ignore[no-untyped-def]
    before, after = scored["pictures"]

    assert before and after == before


def test_the_render_time_is_recorded(scored) -> None:  # type: ignore[no-untyped-def]
    """Not a pass mark: the number the owner asked to see, kept in the test output."""
    print(f"\nscored 45 s video made in {scored['seconds']:.0f} s")
    for step in ("levels", "variation", "intensity"):
        print(f"  {step}: sound updated in {scored[step]['seconds']:.0f} s")
    assert scored["seconds"] > 0
