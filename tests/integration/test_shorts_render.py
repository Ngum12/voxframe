"""Shorts exports and draft previews use real source frames, voice and captions."""
from pathlib import Path

import pytest

from tests.integration import test_footage_sync as sync
from tests.integration import test_render_from_plan as render_fixtures
from voxframe.config.style import get_template
from voxframe.plan.pacing import apply_cuts, suggest_cuts
from voxframe.plan.scene_plan import PlanAsset, PlanWord, ScenePlan
from voxframe.plan.shorts import bounds, build_short, tokens
from voxframe.render.compose import render_from_plan
from voxframe.render.compose.short_preview import short_preview

caps = sync.caps
pytestmark = pytest.mark.needs_ffmpeg


def recording(caps, tmp_path: Path, delay: float = 0) -> ScenePlan:  # type: ignore[no-untyped-def]
    footage = sync._footage(caps, sync._recording(caps, tmp_path / "source.mp4", rate=25, audio_delay=delay))
    scene = sync._speaker(0, 0, 7, 0).model_copy(update={"text": "First last.",
        "words": (PlanWord(text="First", start=.2, end=1.8),
                  PlanWord(text="last.", start=4.5, end=6.5))})
    return sync._plan(footage, scene)


@pytest.mark.parametrize("paced,delay", [(False, 0), (True, 0), (False, .6), (True, .6)])
def test_export_selected_words_preserves_source_claps_and_subtitles(caps, tmp_path: Path, paced: bool, delay: float) -> None:  # type: ignore[no-untyped-def]
    original = recording(caps, tmp_path, delay)
    plan = original
    removed = 0
    if paced:
        cut = suggest_cuts(original)[0]
        removed = cut["seconds"]
        plan = apply_cuts(original, (cut["id"],))
    first, _ = bounds(plan, tokens(plan), 0, 1)
    draft = build_short(plan, 0, 1)
    result = render_from_plan(draft, Path(draft.audio_path), get_template(), caps,
                             tmp_path / "short.mp4", height=480)
    sync._assert_in_sync(caps, result.video_path, [1.5 - first / plan.fps,
                                                4.5 - removed - first / plan.fps])
    assert render_fixtures._frame_count(caps, result.video_path) == draft.total_frames
    assert "First" in result.srt_path.read_text() and "last." in result.srt_path.read_text()
    assert draft.aspect.value == "9:16" and draft.scenes[0].audio_start > 0
    assert original.scenes[0].audio_start is None


def test_real_preview_cached_without_modifying_plan(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = recording(caps, tmp_path)
    # A speaker shot does not need its unused cutaway, and added music is
    # deliberately deferred while preserving the saved choices.
    unused = PlanAsset(id="unused", path=str(tmp_path / "missing.jpg"), width=640, height=360,
                       license_name="CC0", license_author="test", license_source="test")
    source = source.model_copy(update={"music_path": str(tmp_path / "missing.mp3"),
        "scenes": (source.scenes[0].model_copy(update={"asset": unused}),)})
    draft = build_short(source, 0, 1)
    before = draft.model_dump_json()
    output = short_preview(draft, tmp_path / "previews")
    written = output.stat().st_mtime_ns
    assert output.is_file() and output.read_bytes()[4:8] == b"ftyp"
    assert render_fixtures._frame_count(caps, output) == draft.total_frames
    assert short_preview(draft, tmp_path / "previews") == output
    assert output.stat().st_mtime_ns == written
    assert draft.model_dump_json() == before
