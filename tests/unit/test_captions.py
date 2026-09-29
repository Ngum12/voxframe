"""Tests for caption generation.

The ASS format fails quietly: a field-count mismatch makes libass misassign
fields rather than error, so text ends up in the wrong place or vanishes. These
tests check structure explicitly for that reason.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from voxframe.config.style import CaptionPosition, CaptionStyle
from voxframe.models.scene import Scene
from voxframe.models.transcript import Word
from voxframe.render.captions import (
    build_ass,
    build_srt,
    build_vtt,
    format_timestamp,
    write_ass,
)


def _words(*specs: tuple[str, float, float]) -> tuple[Word, ...]:
    return tuple(Word(text=t, start=s, end=e) for t, s, e in specs)


@pytest.fixture
def scene() -> Scene:
    return Scene(
        index=0,
        start_frame=0,
        end_frame=90,
        words=_words(("Hello", 0.0, 0.5), ("world", 0.6, 1.1), ("again", 1.2, 1.8)),
    )


class TestFormatTimestamp:
    def test_basic(self) -> None:
        assert format_timestamp(0.0) == "0:00:00.00"
        assert format_timestamp(1.5) == "0:00:01.50"
        assert format_timestamp(61.25) == "0:01:01.25"
        assert format_timestamp(3661.0) == "1:01:01.00"

    def test_centisecond_rounding_carries(self) -> None:
        """0.999 must become 1.00, not 0.100."""
        assert format_timestamp(0.999) == "0:00:01.00"
        assert format_timestamp(59.999) == "0:01:00.00"
        assert format_timestamp(3599.999) == "1:00:00.00"

    def test_negative_clamped(self) -> None:
        assert format_timestamp(-5.0) == "0:00:00.00"


class TestAssStructure:
    def test_required_sections(self, scene: Scene) -> None:
        ass = build_ass((scene,), CaptionStyle(), 1920, 1080, 30.0)
        assert "[Script Info]" in ass
        assert "[V4+ Styles]" in ass
        assert "[Events]" in ass

    def test_playres_matches_frame_size(self, scene: Scene) -> None:
        """PlayRes must match the video, or libass scales everything.

        Captions built for 1080p and burned into 540p come out half-size with
        no warning, which is how the Phase 2 sizing bug appeared.
        """
        ass = build_ass((scene,), CaptionStyle(), 1280, 720, 30.0)
        assert "PlayResX: 1280" in ass
        assert "PlayResY: 720" in ass

    def test_style_field_count_matches_format(self, scene: Scene) -> None:
        """A mismatch here makes libass misassign fields silently."""
        ass = build_ass((scene,), CaptionStyle(), 1920, 1080, 30.0)

        format_line = next(
            line for line in ass.splitlines()
            if line.startswith("Format:") and "Fontname" in line
        )
        style_line = next(line for line in ass.splitlines() if line.startswith("Style:"))

        expected = len(format_line.removeprefix("Format:").split(","))
        actual = len(style_line.removeprefix("Style:").split(","))
        assert actual == expected, f"Style has {actual} fields, Format declares {expected}"

    def test_dialogue_field_count_matches_format(self, scene: Scene) -> None:
        ass = build_ass((scene,), CaptionStyle(), 1920, 1080, 30.0)

        format_line = next(
            line for line in ass.splitlines()
            if line.startswith("Format:") and "Layer" in line
        )
        expected = len(format_line.removeprefix("Format:").split(","))

        for line in ass.splitlines():
            if line.startswith("Dialogue:"):
                # Text is the last field and may itself contain commas.
                fields = line.removeprefix("Dialogue:").split(",", expected - 1)
                assert len(fields) == expected


class TestHighlighting:
    def test_one_dialogue_per_word(self, scene: Scene) -> None:
        ass = build_ass((scene,), CaptionStyle(highlight_enabled=True), 1920, 1080, 30.0)
        assert ass.count("Dialogue:") == len(scene.words)

    def test_highlight_disabled_gives_one_line(self, scene: Scene) -> None:
        ass = build_ass((scene,), CaptionStyle(highlight_enabled=False), 1920, 1080, 30.0)
        assert ass.count("Dialogue:") == 1

    def test_each_word_highlighted_once(self, scene: Scene) -> None:
        style = CaptionStyle()
        ass = build_ass((scene,), style, 1920, 1080, 30.0)

        dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        for position, word in enumerate(scene.words):
            marker = f"{{\\c{style.highlight_color}}}{word.text}"
            assert marker in dialogues[position]

    def test_captions_do_not_overlap(self, scene: Scene) -> None:
        """Overlapping lines would stack two captions on screen at once."""
        ass = build_ass((scene,), CaptionStyle(), 1920, 1080, 30.0)

        spans = []
        for line in ass.splitlines():
            if line.startswith("Dialogue:"):
                fields = line.split(",")
                spans.append((fields[1], fields[2]))

        for (_, first_end), (second_start, _) in pairwise(spans):
            assert first_end <= second_start

    def test_first_caption_starts_with_scene(self) -> None:
        """A scene must never open with no caption on screen."""
        scene = Scene(
            index=0,
            start_frame=0,
            end_frame=90,
            words=_words(("Late", 1.5, 2.0)),  # speech starts well after the scene
        )
        ass = build_ass((scene,), CaptionStyle(), 1920, 1080, 30.0)
        first = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        assert first.split(",")[1] == "0:00:00.00"


class TestLayout:
    def test_long_text_wraps(self) -> None:
        words = _words(*[(f"word{i}", i * 0.3, i * 0.3 + 0.25) for i in range(20)])
        scene = Scene(index=0, start_frame=0, end_frame=300, words=words)

        ass = build_ass((scene,), CaptionStyle(max_chars_per_line=20), 1920, 1080, 30.0)
        dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        assert "\\N" in dialogue

    def test_max_lines_respected(self) -> None:
        words = _words(*[(f"word{i}", i * 0.3, i * 0.3 + 0.25) for i in range(40)])
        scene = Scene(index=0, start_frame=0, end_frame=600, words=words)

        ass = build_ass(
            (scene,), CaptionStyle(max_lines=2, max_chars_per_line=20), 1920, 1080, 30.0
        )
        for line in ass.splitlines():
            if line.startswith("Dialogue:"):
                assert line.count("\\N") <= 1  # two lines means one break

    def test_vertical_uses_larger_bottom_margin(self) -> None:
        """Vertical video must clear platform UI overlays."""
        style = CaptionStyle()
        assert style.margin_vertical_px(1080, 1920) > style.margin_vertical_px(1920, 1080)

    def test_alignment_follows_position(self) -> None:
        scene = Scene(index=0, start_frame=0, end_frame=90, words=_words(("Hi", 0.0, 0.5)))

        for position, code in (
            (CaptionPosition.BOTTOM, "2"),
            (CaptionPosition.CENTER, "5"),
            (CaptionPosition.TOP, "8"),
        ):
            ass = build_ass((scene,), CaptionStyle(position=position), 1920, 1080, 30.0)
            style_line = next(line for line in ass.splitlines() if line.startswith("Style:"))
            assert style_line.split(",")[18] == code


class TestEscaping:
    def test_braces_escaped(self) -> None:
        """Unescaped braces would be read as malformed override tags."""
        scene = Scene(
            index=0, start_frame=0, end_frame=90, words=_words(("{weird}", 0.0, 0.5))
        )
        ass = build_ass((scene,), CaptionStyle(), 1920, 1080, 30.0)
        dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        assert "\\{weird\\}" in dialogue

    def test_apostrophes_pass_through(self) -> None:
        """ASS file *contents* are fine with apostrophes; only paths are not."""
        scene = Scene(
            index=0, start_frame=0, end_frame=90, words=_words(("Ngum's", 0.0, 0.5))
        )
        ass = build_ass((scene,), CaptionStyle(), 1920, 1080, 30.0)
        assert "Ngum's" in ass

    def test_non_ascii_preserved(self) -> None:
        scene = Scene(
            index=0, start_frame=0, end_frame=90, words=_words(("leçon", 0.0, 0.5))
        )
        ass = build_ass((scene,), CaptionStyle(), 1920, 1080, 30.0)
        assert "leçon" in ass


class TestSilentScenes:
    def test_silent_scene_produces_no_caption(self) -> None:
        scene = Scene(index=0, start_frame=0, end_frame=90, words=())
        ass = build_ass((scene,), CaptionStyle(), 1920, 1080, 30.0)
        assert ass.count("Dialogue:") == 0


class TestSidecarFormats:
    def test_srt_structure(self, scene: Scene) -> None:
        srt = build_srt((scene,), 30.0)
        assert srt.startswith("1\n")
        assert "-->" in srt
        assert "," in srt.split("-->")[0]  # SRT uses comma for milliseconds

    def test_vtt_structure(self, scene: Scene) -> None:
        vtt = build_vtt((scene,), 30.0)
        assert vtt.startswith("WEBVTT")
        assert "-->" in vtt
        assert "." in vtt.split("-->")[1].split("\n")[0]  # VTT uses a dot

    def test_silent_scenes_omitted(self) -> None:
        scenes = (
            Scene(index=0, start_frame=0, end_frame=90, words=()),
            Scene(
                index=1, start_frame=90, end_frame=180, words=_words(("Hi", 3.1, 3.5))
            ),
        )
        assert build_srt(scenes, 30.0).count("-->") == 1


class TestWriteAss:
    def test_written_as_utf8_without_bom(self, tmp_path: Path, scene: Scene) -> None:
        """A BOM can render as a stray glyph in the first caption."""
        path = write_ass(tmp_path / "out.ass", (scene,), CaptionStyle(), 1920, 1080, 30.0)
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert raw.decode("utf-8").startswith("[Script Info]")


class TestLongScenesArePaged:
    """A scene longer than one caption box must not lose words (D-063).

    The previous implementation returned only the first ``max_lines`` lines and
    discarded the rest, so roughly half of a real 23-word scene was never
    captioned. Every existing caption test used scenes short enough to fit,
    which is exactly why the bug survived them.
    """

    def _long_scene(self, word_count: int = 24) -> Scene:
        # Half a second per word, starting at zero.
        specs = tuple(
            (f"word{index:02d}", index * 0.5, index * 0.5 + 0.45)
            for index in range(word_count)
        )
        return Scene(
            index=0,
            start_frame=0,
            end_frame=int(word_count * 0.5 * 30),
            words=_words(*specs),
        )

    def test_every_word_appears(self) -> None:
        scene = self._long_scene()
        ass = build_ass((scene,), CaptionStyle(), 1920, 1080, 30.0)

        missing = [w.text for w in scene.words if w.text not in ass]
        assert not missing, f"words dropped from the caption file: {missing}"

    def test_pages_cover_the_scene_without_gaps(self) -> None:
        """Every frame of a captioned scene must have a caption on it."""
        scene = self._long_scene()
        ass = build_ass((scene,), CaptionStyle(), 1920, 1080, 30.0)

        spans = []
        for line in ass.splitlines():
            if not line.startswith("Dialogue:"):
                continue
            fields = line.split(",", 3)
            spans.append((fields[1], fields[2]))

        assert spans
        # Consecutive dialogue lines abut exactly: no gap, no overlap.
        for (_, end), (start, _) in pairwise(spans):
            assert end == start

    def test_a_short_scene_is_still_one_page(self) -> None:
        """Paging must not change behaviour for scenes that already fit."""
        scene = Scene(
            index=0,
            start_frame=0,
            end_frame=90,
            words=_words(("Hello", 0.0, 0.5), ("world", 0.6, 1.1)),
        )
        ass = build_ass((scene,), CaptionStyle(), 1920, 1080, 30.0)

        # Both words are on screen together throughout.
        for line in ass.splitlines():
            if line.startswith("Dialogue:"):
                assert "Hello" in line and "world" in line

    def test_no_caption_runs_past_its_scene(self) -> None:
        scene = self._long_scene()
        ass = build_ass((scene,), CaptionStyle(), 1920, 1080, 30.0)

        scene_end = format_timestamp(scene.end_seconds(30.0))
        for line in ass.splitlines():
            if line.startswith("Dialogue:"):
                assert line.split(",", 3)[2] <= scene_end
