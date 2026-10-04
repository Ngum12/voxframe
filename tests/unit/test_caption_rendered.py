"""Exercise all caption presets with libass, including pauses and portrait fit."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.unit import test_caption_studio as fixtures
from voxframe.config.captions import CAPTION_PRESETS
from voxframe.plan.scene_plan import AspectRatio, PlanWord, ScenePlan
from voxframe.render.captions.preview import caption_preview
from voxframe.render.encode.probe import probe_capabilities
from voxframe.render.ffpath import run_ffmpeg
from voxframe.render.ffpath.filters import relative_ass_filter

plan = fixtures.plan
pytestmark = pytest.mark.needs_ffmpeg


def frame(path: Path, at: float, directory: Path) -> np.ndarray:
    target = directory / f"{path.stem}-{at}.png"
    run_ffmpeg(probe_capabilities().ffmpeg_path, ["-loglevel", "error", "-ss", str(at),
        "-i", str(path), "-frames:v", "1", "-y", str(target)])
    return np.asarray(Image.open(target).convert("RGB"))


@pytest.mark.parametrize("preset", CAPTION_PRESETS)
def test_every_preset_draws_words_and_reveals_wait_for_speech(plan: ScenePlan, tmp_path: Path, preset: str) -> None:
    treatment = CAPTION_PRESETS[preset]
    video = caption_preview(plan, 0, treatment, (3,), tmp_path)
    pixels = frame(video, 2.3, tmp_path)
    assert (pixels.max(axis=2) > 150).sum() > 100
    if preset in {"electric", "cinema", "spotlight"}:
        before = frame(video, .1, tmp_path)
        assert (before.max(axis=2) > 150).sum() == 0


def test_long_emphasized_word_fits_portrait(plan: ScenePlan, tmp_path: Path) -> None:
    word = "anticonstitutionnellement"
    scene = plan.scenes[0].model_copy(update={"text": word, "words": (
        PlanWord(text=word, start=.2, end=2.5),)})
    portrait = plan.model_copy(update={"aspect": AspectRatio.VERTICAL, "scenes": (scene,)})
    video = caption_preview(portrait, 0, CAPTION_PRESETS["spotlight"], (0,), tmp_path)
    pixels = frame(video, 1, tmp_path)
    ys, xs = np.where(pixels.max(axis=2) > 150)
    assert len(ys) > 100
    assert xs.min() >= 10 and xs.max() < 260


@pytest.mark.parametrize("name", ["..", ".", "a/b", "a\\b", "a'", "a:b"])
def test_preview_filter_only_accepts_local_staged_names(name: str) -> None:
    with pytest.raises(ValueError):
        relative_ass_filter(name, "fonts")
