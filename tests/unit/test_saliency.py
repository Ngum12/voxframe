"""Tests for saliency-based subject detection.

Ken Burns aimed at the geometric centre of a landscape photograph usually
zooms into sky. These tests check that the estimate finds an off-centre subject
when there is one, and degrades safely when there is not.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from voxframe.render.motion import find_subject_center


def _write(array: np.ndarray, path: Path) -> Path:
    Image.fromarray(array.astype(np.uint8)).save(path)
    return path


class TestSubjectDetection:
    def test_finds_an_off_centre_subject(self, tmp_path: Path) -> None:
        """A bright detailed patch should pull the aim point toward it."""
        size = 256
        array = np.full((size, size, 3), 40, dtype=np.uint8)

        # High-frequency detail in the upper left: the only thing that stands
        # out in an otherwise flat frame.
        for y in range(40, 90, 4):
            for x in range(40, 90, 4):
                array[y : y + 2, x : x + 2] = 240

        result = find_subject_center(_write(array, tmp_path / "corner.png"))

        assert result.center[0] < 0.5, "aim point did not move left"
        assert result.center[1] < 0.5, "aim point did not move up"

    def test_flat_image_reports_low_confidence(self, tmp_path: Path) -> None:
        """Nothing stands out, so the estimate must not be trusted."""
        array = np.full((256, 256, 3), 128, dtype=np.uint8)
        result = find_subject_center(_write(array, tmp_path / "flat.png"))

        assert not result.is_confident

    def test_aim_point_stays_inside_the_frame(self, tmp_path: Path) -> None:
        """A subject at the very edge would stall the move against it."""
        size = 256
        array = np.full((size, size, 3), 20, dtype=np.uint8)
        # Detail crammed into the extreme corner.
        for y in range(0, 24, 3):
            for x in range(0, 24, 3):
                array[y : y + 1, x : x + 1] = 255

        result = find_subject_center(_write(array, tmp_path / "edge.png"))

        assert 0.15 <= result.center[0] <= 0.85
        assert 0.15 <= result.center[1] <= 0.85

    def test_missing_file_degrades_to_centre(self, tmp_path: Path) -> None:
        """A bad aim point must degrade the motion, not fail the render."""
        result = find_subject_center(tmp_path / "does_not_exist.png")

        assert result.center == (0.5, 0.5)
        assert result.confidence == 0.0
        assert not result.is_confident

    def test_unreadable_file_degrades_to_centre(self, tmp_path: Path) -> None:
        path = tmp_path / "not_an_image.png"
        path.write_bytes(b"this is not a PNG")

        result = find_subject_center(path)
        assert result.center == (0.5, 0.5)

    def test_greyscale_image_handled(self, tmp_path: Path) -> None:
        """Greyscale JPEGs appear in real libraries."""
        array = np.random.default_rng(0).integers(0, 255, (128, 128), dtype=np.uint8)
        path = tmp_path / "grey.png"
        Image.fromarray(array).convert("L").save(path)

        result = find_subject_center(path)
        assert 0.0 <= result.center[0] <= 1.0

    @pytest.mark.parametrize("size", [(64, 200), (200, 64), (128, 128)])
    def test_non_square_images(self, tmp_path: Path, size: tuple[int, int]) -> None:
        """Portrait, landscape and square must all work."""
        array = np.random.default_rng(1).integers(
            0, 255, (size[1], size[0], 3), dtype=np.uint8
        )
        result = find_subject_center(_write(array, tmp_path / f"s{size[0]}.png"))

        assert 0.0 <= result.center[0] <= 1.0
        assert 0.0 <= result.center[1] <= 1.0


class TestDeterminism:
    def test_same_image_gives_same_result(self, tmp_path: Path) -> None:
        """A re-render of one plan must produce identical output."""
        array = np.random.default_rng(2).integers(0, 255, (128, 128, 3), dtype=np.uint8)
        path = _write(array, tmp_path / "stable.png")

        first = find_subject_center(path)
        second = find_subject_center(path)

        assert first.center == second.center
        assert first.confidence == second.confidence
