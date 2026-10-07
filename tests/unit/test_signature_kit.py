"""Style kits replace styling, never project content or source clocks."""
from tests.unit.test_pacing import source
from voxframe.config.camera import CameraMove
from voxframe.config.creative_presets import BeatStyle, CreativeSettings, load, save
from voxframe.config.transitions import TRANSITION_PRESETS
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.scene_plan import MotionKind, VisualBeat
from voxframe.plan.signature_kit import apply_kit


def test_kit_preserves_words_clocks_media_and_beat_content():
    plan = source()
    scene = plan.scenes[0].model_copy(update={
        "visual_beat": VisualBeat(text="Keep these words", source="user", look="authority"),
        "transition_after": TRANSITION_PRESETS["gentle"],
    })
    plan = plan.model_copy(update={"scenes": (scene,), "music_path": "current.wav",
                                   "music_credit": "Current credit"})
    settings = CreativeSettings(camera_move=CameraMove(direction="left"),
        transition_treatment=TRANSITION_PRESETS["cinematic"],
        beat_style=BeatStyle(look="energy", position="top", zoom=1.1),
        audio_mix=AudioMix(music_arc="punch"))
    result = apply_kit(plan, settings)
    changed = result.scenes[0]
    assert changed.words == scene.words
    assert changed.start_frame == scene.start_frame and changed.end_frame == scene.end_frame
    assert changed.audio_start == scene.audio_start
    assert changed.footage_start == scene.footage_start
    assert changed.asset == scene.asset and changed.shot == scene.shot
    assert changed.visual_beat.text == scene.visual_beat.text
    assert changed.visual_beat.source == "user"
    assert changed.visual_beat.look == "energy"
    assert changed.camera_move.direction == "left"
    assert changed.transition_after is None
    assert result.transition_treatment == settings.transition_treatment
    assert result.music_path == plan.music_path and result.music_credit == plan.music_credit
    assert result.audio_mix.music_arc == "punch"
    assert plan.scenes[0].visual_beat.look == "authority"


def test_held_photo_and_absent_beat_are_not_invented():
    plan = source()
    held = plan.scenes[0].model_copy(update={"motion": MotionKind.NONE})
    result = apply_kit(plan.model_copy(update={"scenes": (held,)}),
                       CreativeSettings(camera_move=CameraMove(), beat_style=BeatStyle(look="cinema")))
    assert result.scenes[0].motion == MotionKind.NONE
    assert result.scenes[0].camera_move is None
    assert result.scenes[0].visual_beat is None


def test_legacy_presets_keep_visual_overrides_and_new_settings_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXFRAME_CONFIG_DIR", str(tmp_path))
    legacy = CreativeSettings.model_validate({"audio_mix": {"music_arc": "rise"}})
    plan = source()
    scene = plan.scenes[0].model_copy(update={"transition_after": TRANSITION_PRESETS["push"],
                                             "camera_move": CameraMove(direction="right")})
    result = apply_kit(plan.model_copy(update={"scenes": (scene,)}), legacy)
    assert result.scenes[0].transition_after == scene.transition_after
    assert result.scenes[0].camera_move == scene.camera_move
    kit = save("Signature", CreativeSettings(camera_move=CameraMove(direction="down"),
        beat_style=BeatStyle(look="cinema"), transition_treatment=TRANSITION_PRESETS["soft"]))
    assert load() == [kit]
    assert "text" not in kit.beat_style.model_dump()
