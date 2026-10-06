"""A preset travels through first render and survives in the saved plan."""
from tests.integration import test_footage_pipeline as fixtures
from voxframe.config.captions import CAPTION_PRESETS
from voxframe.config.creative_presets import CreativeSettings
from voxframe.config.settings import QualityPreset
from voxframe.config.style import get_template
from voxframe.jobs.pipeline import JobOptions, run_pipeline
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.scene_plan import ScenePlan

caps = fixtures.caps
fake_transcription = fixtures.fake_transcription
pytestmark = fixtures.pytestmark


def test_first_render_uses_selected_caption_and_mix(caps, fake_transcription, tmp_path):
    source = fixtures._recording(caps, tmp_path / "talk.mp4")
    creative = CreativeSettings(caption_treatment=CAPTION_PRESETS["electric"],
                                audio_mix=AudioMix(music_arc="rise", voice_polish=False,
                                                   destination="whatsapp"))
    outcome = run_pipeline(JobOptions(audio=source, output=tmp_path / "out.mp4",
        chapters=False, footage=True, quality=QualityPreset.DRAFT, height=240,
        creative=creative), fixtures._settings(tmp_path), get_template(), caps)
    saved = ScenePlan.load(outcome.plan_path)
    assert saved.caption_treatment == creative.caption_treatment
    assert saved.audio_mix == creative.audio_mix
    assert saved.footage.path == str(source)
    assert not saved.music_path and saved.score is None
    captions = outcome.result.ass_path.read_text()
    assert "&H00D0F070" in captions  # Electric accent, encoded in ASS BGR.
    assert "WINTER" in captions  # Uppercase treatment is really rendered.
    flashes = fixtures._flashes(caps, outcome.result.video_path)
    tones = [t for t in fixtures._tones(caps, outcome.result.video_path)
             if t < saved.audio_duration - .1]  # Exclude the detector's end-of-file edge.
    assert len(flashes) == len(tones) == len(fixtures.CLAPS)
    for flash, tone in zip(flashes, tones, strict=True):
        assert abs(flash - tone) < fixtures.TOLERANCE
