"""Finishing findings cite saved data, preserve edits and avoid fake pass guarantees."""
import pytest

from tests.unit.test_pacing import source
from voxframe.config.captions import CaptionTreatment
from voxframe.config.short_export import ShortExport
from voxframe.config.style import CaptionPosition
from voxframe.config.visuals import VisualBeat
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.finish_review import review
from voxframe.plan.scene_plan import Footage, PlanAsset, PlanWord, Shot
from voxframe.plan.shorts import revision


def issue_ids(report):
    return {issue["id"].split(":")[0] for issue in report["issues"]}


def test_clean_timed_plan_has_no_cues_and_is_not_claimed_visually_passed():
    plan = source()
    before = plan.model_dump_json()
    report = review(plan, height=720)
    assert report["issues"] == []
    assert (report["width"], report["height"]) == (1280, 720)
    assert report["revision"] == revision(plan)
    assert "not a visual" in report["note"]
    assert plan.model_dump_json() == before


def test_saved_export_size_overrides_job_size_and_long_token_is_measured():
    plan = source()
    scene = plan.scenes[0].model_copy(update={"text": "W" * 120, "words": (
        PlanWord(text="W" * 120, start=1, end=2),)})
    from voxframe.config.settings import AspectRatio
    plan = plan.model_copy(update={"scenes": (scene,), "aspect": AspectRatio.VERTICAL,
                                   "short_export": ShortExport(height=1280)})
    report = review(plan, 240)
    assert (report["width"], report["height"]) == (720, 1280)
    assert "wide-word" in issue_ids(report)


def test_estimated_caption_alignment_and_corrected_words_are_reviewed():
    plan = source()
    for change, code in (({"words": ()}, "untimed"), ({"caption_text": "First and last"}, "corrected")):
        edited = plan.model_copy(update={"scenes": (plan.scenes[0].model_copy(update=change),)})
        assert code in issue_ids(review(edited))


def test_fast_pace_and_manual_layer_placement_are_contextual_cues():
    plan = source()
    words = tuple(PlanWord(text="word", start=i * .1, end=i * .1 + .09) for i in range(10))
    scene = plan.scenes[0].model_copy(update={"words": words, "text": " ".join(w.text for w in words),
        "caption_treatment": CaptionTreatment(position=CaptionPosition.CENTER),
        "visual_beat": VisualBeat(text="WORD", position="center")})
    edited = plan.model_copy(update={"scenes": (scene,)})
    report = review(edited)
    assert {"pace", "text-placement"} <= issue_ids(report)
    scene = scene.model_copy(update={"visual_beat": scene.visual_beat.model_copy(update={"position": "auto"})})
    assert "text-placement" not in issue_ids(review(edited.model_copy(update={"scenes": (scene,)})))


@pytest.mark.parametrize("height,flag", [(240, False), (1080, True)])
def test_resolution_depends_on_the_output_not_just_source_size(height, flag):
    plan = source()
    asset = PlanAsset(id="image", path="unused.png", width=640, height=360,
        license_name="CC0", license_author="Test", license_source="local", prints_text=True)
    scene = plan.scenes[0].model_copy(update={"asset": asset})
    report = review(plan.model_copy(update={"scenes": (scene,)}), height)
    assert ("resolution" in issue_ids(report)) is flag
    assert "printed-image" in issue_ids(report)


def test_speaker_source_tail_uses_original_clock_and_audio_offset():
    plan = source()
    footage = Footage(path="unused.mp4", width=1920, height=1080, fps=30, duration=10, audio_offset=.6)
    scene = plan.scenes[0].model_copy(update={"shot": Shot.SPEAKER, "footage_start": 3})
    edited = plan.model_copy(update={"footage": footage, "scenes": (scene,)})
    assert "source-tail" in issue_ids(review(edited))
    edited = edited.model_copy(update={"scenes": (scene.model_copy(update={"footage_start": 2}),)})
    assert "source-tail" not in issue_ids(review(edited))


def test_voice_margin_only_flags_when_an_added_track_exists():
    plan = source().model_copy(update={"audio_mix": AudioMix(speech_margin_db=6)})
    assert "voice-margin" not in issue_ids(review(plan))
    assert "voice-margin" in issue_ids(review(plan.model_copy(update={"music_path": "track.wav"})))


def test_tiny_wordless_handles_are_not_flash_shot_findings():
    plan = source()
    scene = plan.scenes[0].model_copy(update={"end_frame": 3, "words": (), "text": ""})
    assert "brief-shot" not in issue_ids(review(plan.model_copy(update={"scenes": (scene,), "total_frames": 3})))


def test_missing_fonts_do_not_silently_report_a_clean_caption_check(monkeypatch):
    monkeypatch.setattr("voxframe.plan.finish_review._TextWidth.for_frame", lambda *_: None)
    report = review(source())
    assert "font-measure" in issue_ids(report)
    assert report["counts"]["captions"] == 1
