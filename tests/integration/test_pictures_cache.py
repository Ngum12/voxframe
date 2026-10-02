"""An edit that changes only a picture shows in the updated video (D-188).

The captioned pictures are cached whole (D-171). Their key once used the
segments' file names, which are positions (``scene_00001.mp4``), so an edit
that changed a segment but not the captions or the timing -- a new picture,
a card's text, the camera movement -- reused the old pictures, and "Update
video" changed nothing. This renders the sonnet, changes only the title
card's text, renders again, and looks at the frames.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from voxframe.plan.scene_plan import ScenePlan

REPO_ROOT = Path(__file__).resolve().parents[2]
SONNET = REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav"


def _frame(video: Path, seconds: float, out: Path) -> np.ndarray:
    from PIL import Image

    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{seconds}", "-i", str(video),
         "-frames:v", "1", str(out)],
        check=True,
    )
    return np.asarray(Image.open(out).convert("L"), dtype=np.float32)


@pytest.fixture(scope="module")
def edited(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    from voxframe.config.settings import QualityPreset, get_settings
    from voxframe.config.style import get_template
    from voxframe.jobs.pipeline import JobOptions, render_plan, run_pipeline
    from voxframe.render.encode.probe import probe_capabilities

    try:
        caps = probe_capabilities()
    except Exception as exc:  # pragma: no cover - FFmpeg is part of the dev setup
        pytest.skip(f"FFmpeg not available: {exc}")

    work = tmp_path_factory.mktemp("pictures")
    settings = get_settings().model_copy(
        update={
            "transcribe_model": "base",
            "cache_path": work / "cache",
            "library_path": work / "library",  # empty: plain backgrounds
        }
    )
    output = work / "out" / "talk.mp4"
    first = run_pipeline(
        JobOptions(
            audio=SONNET, output=output, quality=QualityPreset.DRAFT, height=360,
            chapters=False, title="A Calendar of Sonnets", language="en",
        ),
        settings, get_template("documentary"), caps,
    )
    assert first.plan_path is not None
    before = _frame(output, 1.0, work / "before.png")
    pictures_before = sorted((work / "cache" / "pictures").glob("*.mp4"))

    plan = ScenePlan.load(first.plan_path)
    title = next(scene for scene in plan.scenes if scene.card_kind == "title")
    scenes = [
        scene.model_copy(update={"card_text": "Something Else Entirely, Much Longer"})
        if scene.index == title.index
        else scene
        for scene in plan.scenes
    ]
    plan.model_copy(update={"scenes": scenes}).save(first.plan_path)
    render_plan(first.plan_path, output, settings, caps, quality=QualityPreset.DRAFT, height=360)
    after = _frame(output, 1.0, work / "after.png")
    pictures_after = sorted((work / "cache" / "pictures").glob("*.mp4"))
    return {
        "before": before, "after": after,
        "pictures": (pictures_before, pictures_after),
        "title_seconds": title.end_frame / plan.fps,
    }


def test_the_title_card_is_long_enough_to_look_at(edited) -> None:  # type: ignore[no-untyped-def]
    assert edited["title_seconds"] > 1.0  # the frame compared is inside the card


def test_the_new_title_is_in_the_video(edited) -> None:  # type: ignore[no-untyped-def]
    difference = float(np.abs(edited["before"] - edited["after"]).mean())

    assert difference > 0.5, "the updated video still shows the old title"


def test_the_new_pictures_are_cached_beside_the_old(edited) -> None:  # type: ignore[no-untyped-def]
    before, after = edited["pictures"]

    assert len(before) == 1
    assert len(after) == 2
