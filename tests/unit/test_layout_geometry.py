"""Where the parts of a split or inset frame go (D-197)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from voxframe.plan.scene_layout import InsetShape, LayoutKind, SceneLayout
from voxframe.render.compose.layout import (
    divider_position,
    inset_images,
    inset_pane,
    split_panes,
)

SPLIT = SceneLayout(kind=LayoutKind.SPLIT)


class TestSplit:
    def test_vertical_stacks_the_picture_over_the_speaker(self) -> None:
        picture, speaker = split_panes(1080, 1920, SPLIT)
        assert (picture.x, picture.y, picture.width, picture.height) == (0, 0, 1080, 960)
        assert (speaker.x, speaker.y, speaker.width, speaker.height) == (0, 960, 1080, 960)

    def test_landscape_puts_them_side_by_side(self) -> None:
        picture, speaker = split_panes(1920, 1080, SPLIT)
        assert (picture.x, picture.width, picture.height) == (0, 960, 1080)
        assert (speaker.x, speaker.width) == (960, 960)

    def test_the_speaker_can_go_first(self) -> None:
        layout = SPLIT.model_copy(update={"speaker_first": True, "split": 0.6})
        picture, speaker = split_panes(1080, 1920, layout)
        assert speaker.y == 0 and picture.y == speaker.height
        # The picture keeps its share wherever it is.
        assert picture.height == pytest.approx(1920 * 0.6, abs=2)

    @pytest.mark.parametrize("share", [0.25, 0.4, 0.5, 0.63, 0.75])
    @pytest.mark.parametrize("size", [(1080, 1920), (720, 1280), (1920, 1080), (1080, 1080)])
    def test_the_parts_fill_the_frame_on_the_420_grid(self, share, size) -> None:  # type: ignore[no-untyped-def]
        width, height = size
        picture, speaker = split_panes(width, height, SPLIT.model_copy(update={"split": share}))
        assert picture.width * picture.height + speaker.width * speaker.height == width * height
        for pane in (picture, speaker):
            assert pane.width % 2 == 0 and pane.height % 2 == 0
            assert pane.x % 2 == 0 and pane.y % 2 == 0

    def test_the_divider_is_where_the_parts_meet(self) -> None:
        layout = SPLIT.model_copy(update={"split": 0.4})
        assert divider_position(1080, 1920, layout) == pytest.approx(0.4, abs=0.002)
        swapped = layout.model_copy(update={"speaker_first": True})
        assert divider_position(1080, 1920, swapped) == pytest.approx(0.6, abs=0.002)


class TestInset:
    def test_a_circle_is_round(self) -> None:
        pane = inset_pane(1080, 1920, SceneLayout(kind=LayoutKind.INSET))
        assert pane.width == pane.height

    def test_a_rounded_inset_is_upright_in_a_vertical_frame(self) -> None:
        layout = SceneLayout(kind=LayoutKind.INSET, inset_shape=InsetShape.ROUNDED)
        pane = inset_pane(1080, 1920, layout)
        assert pane.height > pane.width

    @pytest.mark.parametrize("x", [0.0, 0.5, 1.0])
    @pytest.mark.parametrize("y", [0.0, 0.5, 1.0])
    def test_it_stays_inside_the_frame(self, x: float, y: float) -> None:
        layout = SceneLayout(kind=LayoutKind.INSET, inset_x=x, inset_y=y, inset_size=0.6)
        pane = inset_pane(1080, 1920, layout)
        assert pane.x >= 0 and pane.y >= 0
        assert pane.x + pane.width <= 1080 and pane.y + pane.height <= 1920
        assert pane.x % 2 == 0 and pane.y % 2 == 0

    def test_its_mask_is_the_shape_with_smooth_edges(self, tmp_path: Path) -> None:
        pane = inset_pane(1080, 1920, SceneLayout(kind=LayoutKind.INSET))
        mask, shadow, ring, reach = inset_images(pane, InsetShape.CIRCLE, tmp_path)
        image = Image.open(mask)
        assert image.size == (pane.width, pane.height)
        assert image.getpixel((pane.width // 2, pane.height // 2)) == 255
        assert image.getpixel((0, 0)) == 0
        # Antialiased: the edge has values between in and out.
        values = set(image.getdata())
        assert any(0 < v < 255 for v in values)
        assert Image.open(ring).size == (pane.width + 2 * reach, pane.height + 2 * reach)
        assert Image.open(shadow).mode == "RGBA"


class TestCaptionsOnTheDivider:
    def _plan(self, layout: SceneLayout, **changes: object):  # type: ignore[no-untyped-def]
        from voxframe.config.settings import AspectRatio
        from voxframe.plan.scene_plan import Footage, PlannedScene, PlanWord, ScenePlan

        words = (PlanWord(text="Floods", start=0.1, end=0.5), PlanWord(text="hit", start=0.6, end=0.9))
        return ScenePlan(
            audio_path="a.wav", audio_sha256="0" * 64, audio_duration=2.0, fps=30.0,
            total_frames=60, aspect=AspectRatio.VERTICAL,
            scenes=(PlannedScene(index=0, start_frame=0, end_frame=60, text="Floods hit",
                                 words=words, footage_start=0.0, layout=layout),),
            footage=Footage(path="talk.mp4", width=1280, height=720, fps=30.0, duration=2.0),
            **changes,  # type: ignore[arg-type]
        )

    def test_a_split_puts_its_captions_on_the_divider(self) -> None:
        from voxframe.config.style import get_template
        from voxframe.render.compose.from_plan import plan_captions

        ass = plan_captions(self._plan(SPLIT.model_copy(update={"split": 0.4})),
                            get_template(), 1080, 1920)
        assert "\\an5\\pos(540,768)" in ass

    def test_captions_a_person_placed_stay_where_they_are(self) -> None:
        from voxframe.config.style import get_template
        from voxframe.plan.caption_choice import CaptionChoice
        from voxframe.render.compose.from_plan import plan_captions

        plan = self._plan(SPLIT, captions=CaptionChoice(anchor_y=0.8))
        assert "\\an5\\pos(540,1536)" in plan_captions(plan, get_template(), 1080, 1920)

    def test_a_full_scene_keeps_them_where_they_were(self) -> None:
        from voxframe.config.style import get_template
        from voxframe.render.compose.from_plan import plan_captions

        assert "\\pos" not in plan_captions(self._plan(SceneLayout()), get_template(), 1080, 1920)
