"""Story dynamics preserve narration and ducking in real preview samples."""

import io
from itertools import pairwise

import numpy as np
import pytest

from tests.unit import test_mix_controls as fixtures
from tests.unit.test_mix_controls import _write_stems
from voxframe.music.story_arc import arc_points
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.audio import mixdown
from voxframe.render.audio.mixdown import Stems
from voxframe.render.compose.from_plan import stems_path

client = fixtures.client
context = fixtures.context
finished = fixtures.finished

sf = pytest.importorskip("soundfile")


@pytest.mark.parametrize("arc", ["rise", "punch"])
@pytest.mark.parametrize("opening,landing,end", [(0, 30, 33), (2, 3, 3), (0, .1, .12), (3, 3, 4)])
def test_arcs_are_bounded_monotonic_and_frame_aligned(arc, opening, landing, end):
    points = arc_points(arc, opening, landing, end, 30)
    assert all(0 <= t <= end and -10 <= db <= 0 for t, db in points)
    assert all(a[0] < b[0] for a, b in pairwise(points))
    assert points[-1][0] == end
    assert all(t == pytest.approx(round(t * 30) / 30) for t, _ in points[:-1])


@pytest.mark.parametrize("arc", ["rise", "punch"])
def test_real_mix_preserves_voice_and_only_attenuates_music(tmp_path, arc):
    video = tmp_path / "video.mp4"
    _write_stems(video)
    stems = Stems.load(stems_path(video))
    baseline = AudioMix(voice_polish=False)
    directed = AudioMix(voice_polish=False, music_arc=arc)
    plain_env = mixdown._music_envelope(stems, baseline)
    arc_env = mixdown._music_envelope(stems, directed)
    clock = np.linspace(0, stems.video_end, 10001)
    assert np.all(np.interp(clock, *arc_env) <= np.interp(clock, *plain_env) + 1e-8)
    size = round(stems.video_end * mixdown.RATE)
    voice = mixdown._mix_block(stems, baseline, 0, size, plain_env, sf, voice_only=True)
    voice_arc = mixdown._mix_block(stems, directed, 0, size, arc_env, sf, voice_only=True)
    np.testing.assert_array_equal(voice, voice_arc)
    plain = mixdown._mix_block(stems, baseline, 0, size, plain_env, sf) - voice
    shaped = mixdown._mix_block(stems, directed, 0, size, arc_env, sf) - voice
    assert np.mean(shaped ** 2) < np.mean(plain ** 2)
    # No abrupt discontinuity at an automation knot (110 Hz test music).
    assert np.max(np.abs(np.diff(shaped[:, 0]))) < .01
    for start in (0, 3):
        a = mixdown.preview(stems, directed, start, 2, tmp_path / f"arc-{start}.wav")
        samples, rate = sf.read(a)
        assert rate == mixdown.RATE and len(samples) == 2 * rate
        assert np.isfinite(samples).all() and np.max(np.abs(samples)) <= .971


def test_old_plans_keep_their_sound_and_invalid_arcs_are_rejected():
    assert AudioMix.model_validate({}).music_arc == "steady"
    with pytest.raises(ValueError):
        AudioMix(music_arc="unknown")


def test_story_clock_preview_save_and_undo(client, context, finished, tmp_path):
    job_id, video = finished
    _write_stems(video)
    state = client.get(f"/api/jobs/{job_id}/mix").json()
    assert state["story_clock"] == {"opening": 1, "landing": 4, "end": 6}
    path = context.store.artifact_path(job_id, "plan")
    original = ScenePlan.load(path)
    for arc in ("rise", "punch"):
        result = client.post(f"/api/jobs/{job_id}/mix/preview", json={
            "mix": {"music_arc": arc}, "start": 3, "seconds": 2})
        assert result.status_code == 200
        samples, rate = sf.read(io.BytesIO(result.content))
        assert len(samples) == 2 * rate
        assert ScenePlan.load(path) == original
    response = client.put(f"/api/jobs/{job_id}/mix", json={"music_arc": "rise"})
    assert response.status_code == 200
    assert ScenePlan.load(path).audio_mix.music_arc == "rise"
    response = client.post(f"/api/jobs/{job_id}/plan/undo")
    assert response.status_code == 200
    assert ScenePlan.load(path) == original
    assert client.put(f"/api/jobs/{job_id}/mix", json={"music_arc": "bad"}).status_code == 422
