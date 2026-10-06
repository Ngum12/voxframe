"""Measure picture/audio sync in exported MP4s after actual pause removal."""
from pathlib import Path

import pytest

from tests.integration import test_footage_sync as sync
from tests.integration import test_render_from_plan as render_fixtures
from voxframe.config.style import get_template
from voxframe.plan.pacing import apply_cuts, suggest_cuts
from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan
from voxframe.render.compose import render_from_plan

caps = sync.caps
pytestmark = pytest.mark.needs_ffmpeg


@pytest.mark.parametrize("cards,audio_delay,video_delay", [(False, 0, 0), (True, 0, 0), (False, .6, 0), (False, 0, .4)])
def test_pause_cut_keeps_both_claps_and_captions_in_sync(caps, tmp_path: Path, cards: bool, audio_delay: float, video_delay: float) -> None:  # type: ignore[no-untyped-def]
    footage = sync._footage(caps, sync._recording(caps, tmp_path / "source.mp4", rate=25,
        audio_delay=audio_delay, video_delay=video_delay))
    lead = 2 if cards else 0
    scene = sync._speaker(int(cards), lead, lead + 7, 0).model_copy(update={
        "text": "First last", "words": (
            PlanWord(text="First", start=lead + 1.5, end=lead + 1.8),
            PlanWord(text="last", start=lead + 4.5, end=lead + 4.8))})
    scenes = (scene,)
    if cards:
        scenes = (PlannedScene(index=0, start_frame=0, end_frame=60,
                               card_kind="title", card_text="Title"), scene)
    original = sync._plan(footage, *scenes)
    cut = suggest_cuts(original)[0]
    changed = apply_cuts(original, (cut["id"],))
    result = render_from_plan(changed, Path(changed.audio_path), get_template(), caps,
                              tmp_path / "out.mp4", height=360)
    sync._assert_in_sync(caps, result.video_path, [lead + 1.5, lead + 4.5 - cut["seconds"]])
    assert render_fixtures._frame_count(caps, result.video_path) == changed.total_frames
    assert changed.scenes[-1].words[0].start == pytest.approx(lead + 4.5 - cut["seconds"])
    # Sidecars cover scene spans; highlighted words retain their own timestamps.
    expected_ms = round(changed.scenes[-1].start_frame / changed.fps * 1000)
    assert f"00:00:{expected_ms // 1000:02d},{expected_ms % 1000:03d}" in result.srt_path.read_text()
    assert "last" in result.srt_path.read_text()
    # Reloading the saved plan preserves the same source mapping.
    path = tmp_path / "saved.plan.json"
    changed.save(path)
    assert ScenePlan.load(path) == changed


def test_fractional_frame_audio_spans_do_not_accumulate_rounding_drift(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import soundfile as sf

    from voxframe.render.compose.from_plan import _narration_graph
    from voxframe.render.ffpath import run_ffmpeg

    # At 29.97 fps, rounding 100 one-frame spans independently adds 40 samples.
    # Check the actual assembled waveform, not the graph's arithmetic.
    frames = 100
    scenes = tuple(PlannedScene(index=i, start_frame=i, end_frame=i + 1,
                               audio_start=i / 29.97) for i in range(frames))
    plan = ScenePlan(audio_path="unused.wav", audio_sha256="0" * 64,
                     audio_duration=frames / 29.97, fps=29.97,
                     total_frames=frames, scenes=scenes)
    graph, _ = _narration_graph(plan)
    script = tmp_path / "audio.ffgraph"
    script.write_text(graph.replace(",apad[narr]", "[narr]"), encoding="utf-8")
    destination = tmp_path / "voice.wav"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-f", "lavfi", "-i",
        "anullsrc=r=48000:cl=mono", "-f", "lavfi", "-t", "4", "-i",
        "sine=frequency=440:sample_rate=48000", "-/filter_complex", str(script),
        "-map", "[narr]", "-c:a", "pcm_f32le", "-y", str(destination)])
    info = sf.info(destination)
    assert info.frames == round(frames / 29.97 * 48000)


@pytest.mark.parametrize("audio_delay,video_delay", [(0, 0), (.6, 0), (0, .4)])
def test_word_removal_keeps_claps_source_frames_and_captions(caps, tmp_path, audio_delay, video_delay):
    from voxframe.plan.pacing import suggest_speech_cuts

    footage = sync._footage(caps, sync._recording(caps, tmp_path / "source.mp4", rate=25,
        audio_delay=audio_delay, video_delay=video_delay))
    words = tuple(PlanWord(text=text, start=start, end=end) for text, start, end in (
        ("First", 1.5, 1.8), ("I", 2, 2.15), ("think", 2.25, 2.4),
        ("I", 2.6, 2.75), ("think", 2.8, 2.95), ("um", 3.2, 3.5), ("last", 4.5, 4.8)))
    scene = sync._speaker(0, 0, 7, 0).model_copy(update={
        "text": "First I think I think um last", "words": words})
    original = sync._plan(footage, scene)
    cuts = suggest_speech_cuts(original)
    assert {c["kind"] for c in cuts} == {"filler", "repeat"}
    changed = apply_cuts(original, tuple(c["id"] for c in cuts))
    result = render_from_plan(changed, Path(changed.audio_path), get_template(), caps,
                             tmp_path / "cut.mp4", height=240)
    removed = sum(c["seconds"] for c in cuts)
    sync._assert_in_sync(caps, result.video_path, [1.5, 4.5 - removed])
    assert render_fixtures._frame_count(caps, result.video_path) == changed.total_frames
    assert "um" not in result.srt_path.read_text()
    assert " ".join(w.text for s in changed.scenes for w in s.words) == "First I think last"
