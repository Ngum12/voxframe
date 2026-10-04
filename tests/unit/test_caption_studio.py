"""Saved caption choices, word clocks, and acoustic emphasis without model downloads."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from pydantic import ValidationError

from voxframe.config.captions import CAPTION_PRESETS, CaptionAnimation, CaptionTreatment
from voxframe.config.style import CaptionStyle
from voxframe.models.scene import Scene
from voxframe.models.transcript import Word
from voxframe.plan.caption_studio import set_captions, suggest_emphasis
from voxframe.plan.editing import EditError, correct_caption
from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan
from voxframe.render.captions.ass import build_ass
from voxframe.render.compose.from_plan import _scenes_for_captions


@pytest.fixture
def plan(tmp_path: Path) -> ScenePlan:
    audio = tmp_path / "speech.wav"
    sf.write(audio, np.zeros(48000, dtype=np.float32), 16000)
    return ScenePlan(audio_path=str(audio), audio_sha256="a" * 64, audio_duration=3,
                     fps=30, total_frames=90, scenes=(PlannedScene(
        index=0, start_frame=0, end_frame=90, text="Make every word matter",
        words=tuple(PlanWord(text=t, start=a, end=b) for t, a, b in (
            ("Make", .2, .5), ("every", .7, 1), ("word", 1.4, 1.8), ("matter", 2, 2.5),
        )),
    ),))


@pytest.mark.parametrize("mode", list(CaptionAnimation))
def test_modes_preserve_word_timings_and_survive_save(plan: ScenePlan, tmp_path: Path, mode: CaptionAnimation) -> None:
    updated = set_captions(plan, 0, CaptionTreatment(animation=mode), (3,))
    path = tmp_path / "plan.json"
    updated.save(path)
    loaded = ScenePlan.load(path)
    assert loaded.scenes[0].words == plan.scenes[0].words
    assert loaded.scenes[0].text == plan.scenes[0].text
    assert loaded.scenes[0].caption_treatment.animation == mode
    rebuilt = _scenes_for_captions(loaded)
    assert rebuilt[0].emphasis == (3,)
    ass = build_ass(rebuilt, CaptionStyle(), 270, 480, 30,
                    scene_treatments={0: loaded.scenes[0].caption_treatment})
    assert "Style: Caption0," in ass
    assert "Dialogue:" in ass
    assert "matter" in ass


def test_whole_video_look_clears_overrides_but_keeps_emphasis(plan: ScenePlan) -> None:
    old = set_captions(plan, 0, CAPTION_PRESETS["electric"], (1,))
    result = set_captions(old, 0, CAPTION_PRESETS["cinema"], (1,), all_scenes=True)
    assert result.caption_treatment == CAPTION_PRESETS["cinema"]
    assert result.scenes[0].caption_treatment is None and result.scenes[0].caption_emphasis == (1,)


def test_correcting_words_clears_stale_emphasis(plan: ScenePlan) -> None:
    marked = set_captions(plan, 0, CAPTION_PRESETS["impact"], (3,))
    corrected = correct_caption(marked, 0, "Make words matter")
    assert corrected.scenes[0].caption_emphasis == ()
    assert corrected.scenes[0].caption_treatment == marked.scenes[0].caption_treatment


@pytest.mark.parametrize("bad", [(-1,), (4,), (1, 1)])
def test_invalid_emphasis_is_rejected(plan: ScenePlan, bad: tuple[int, ...]) -> None:
    with pytest.raises(EditError):
        set_captions(plan, 0, CaptionTreatment(), bad)


@pytest.mark.parametrize("field,value", [("accent", "{\\alpha&HFF&}"), ("color", "red"), ("size", 20), ("words_per_page", 0), ("lift", -1)])
def test_invalid_styles_cannot_reach_ass(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        CaptionTreatment(**{field: value})


def test_reveals_begin_on_word_start_after_a_pause() -> None:
    scene = Scene(index=0, start_frame=0, end_frame=90,
                  words=(Word(text="First", start=.2, end=.5), Word(text="Next", start=1.5, end=2)))
    ass = build_ass((scene,), CaptionStyle(), 480, 270, 30,
                    scene_treatments={0: CaptionTreatment(animation="typewriter")})
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 2
    assert events[0].split(",")[1:3] == ["0:00:00.20", "0:00:01.50"]
    assert "\\alpha&HFF&" in events[0] and "Next" in events[0]
    assert events[1].split(",")[1] == "0:00:01.50"
    assert "\\alpha&HFF&" not in events[1]


def test_karaoke_fills_current_word_without_using_gap_as_duration() -> None:
    scene = Scene(index=0, start_frame=0, end_frame=90,
                  words=(Word(text="First", start=.2, end=.5), Word(text="Next", start=1.5, end=2)))
    ass = build_ass((scene,), CaptionStyle(), 480, 270, 30,
                    scene_treatments={0: CaptionTreatment(animation="karaoke")})
    assert "\\kf30" in ass and "\\kf50" in ass
    assert "\\kf130" not in ass


@pytest.mark.needs_ffmpeg
def test_suggestions_measure_louder_delivery_with_card_offset(plan: ScenePlan) -> None:
    samples = np.zeros(48000, dtype=np.float32)
    for i, word in enumerate(plan.scenes[0].words):
        a, b = int(word.start * 16000), int(word.end * 16000)
        samples[a:b] = (0.6 if i == 2 else .02) * np.sin(np.arange(b - a) * .1)
    sf.write(plan.audio_path, samples, 16000)
    card = PlannedScene(index=0, start_frame=0, end_frame=30, card_kind="title", card_text="Title")
    speech = plan.scenes[0].model_copy(update={"index": 1, "start_frame": 30, "end_frame": 120,
        "words": tuple(w.model_copy(update={"start": w.start + 1, "end": w.end + 1}) for w in plan.scenes[0].words)})
    titled = plan.model_copy(update={"total_frames": 120, "scenes": (card, speech)})
    assert suggest_emphasis(titled, 1) == (2,)
    assert titled.scenes[1].caption_emphasis == ()


@pytest.mark.needs_ffmpeg
def test_emphasis_measures_original_source_range_after_a_short_cut(plan: ScenePlan) -> None:
    samples = np.zeros(13 * 16000, dtype=np.float32)
    for i, word in enumerate(plan.scenes[0].words):
        a, b = int((10 + word.start) * 16000), int((10 + word.end) * 16000)
        samples[a:b] = (.6 if i == 2 else .02) * np.sin(np.arange(b - a) * .1)
    # The beginning of the source deliberately stresses a different word.
    first = plan.scenes[0].words[0]
    a, b = int(first.start * 16000), int(first.end * 16000)
    samples[a:b] = .9 * np.sin(np.arange(b - a) * .1)
    sf.write(plan.audio_path, samples, 16000)
    cut = plan.model_copy(update={"scenes": (plan.scenes[0].model_copy(update={"audio_start": 10}),)})
    assert suggest_emphasis(cut, 0) == (2,)
