"""Tests for FFmpeg capability probing (D-017) and configuration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from voxframe.config.settings import (
    AspectRatio,
    IntermediateFormat,
    QualityPreset,
    Settings,
    get_settings,
)
from voxframe.render.encode.probe import (
    ENCODER_PREFERENCE,
    FFmpegCapabilities,
    FFmpegNotFound,
    find_ffmpeg,
)


def _caps(encoders: set[str], filters: set[str] | None = None) -> FFmpegCapabilities:
    return FFmpegCapabilities(
        ffmpeg_path="/usr/bin/ffmpeg",
        ffprobe_path="/usr/bin/ffprobe",
        version="8.1",
        encoders=frozenset(encoders),
        filters=frozenset(filters or set()),
    )


class TestEncoderSelection:
    def test_prefers_hardware_first(self) -> None:
        caps = _caps({"libx264", "h264_nvenc", "h264_qsv"})
        assert caps.best_encoder() == "h264_nvenc"

    def test_falls_back_through_chain(self) -> None:
        caps = _caps({"libx264", "libvpx-vp9"})
        assert caps.best_encoder() == "libx264"

    def test_royalty_free_fallback(self) -> None:
        """A build with no H.264 encoder still works via VP9."""
        caps = _caps({"libvpx-vp9", "libaom-av1"})
        assert caps.best_encoder() == "libvpx-vp9"

    def test_no_encoder_raises_with_hint(self) -> None:
        caps = _caps({"mjpeg"})
        with pytest.raises(FFmpegNotFound, match="No usable video encoder"):
            caps.best_encoder()

    def test_missing_from_chain_reported(self) -> None:
        """Mirrors this machine: has vp9/aom, lacks openh264/svtav1."""
        caps = _caps({"libx264", "libvpx-vp9", "libaom-av1"})
        missing = caps.missing_from_chain()
        assert "libopenh264" in missing
        assert "libsvtav1" in missing
        assert "libx264" not in missing

    def test_preference_chain_order(self) -> None:
        assert ENCODER_PREFERENCE.index("h264_nvenc") < ENCODER_PREFERENCE.index("libx264")
        assert ENCODER_PREFERENCE.index("libx264") < ENCODER_PREFERENCE.index("libvpx-vp9")


class TestCapabilityFlags:
    def test_libass_via_either_filter(self) -> None:
        assert _caps(set(), {"ass"}).has_libass
        assert _caps(set(), {"subtitles"}).has_libass
        assert not _caps(set(), {"scale"}).has_libass

    def test_motion_and_transition_flags(self) -> None:
        caps = _caps(set(), {"zoompan", "xfade"})
        assert caps.has_zoompan
        assert caps.has_xfade
        assert not _caps(set(), {"scale"}).has_xfade


class TestFindFFmpeg:
    def test_bad_explicit_path_names_the_variable(self) -> None:
        """The error must tell the user which setting to fix."""
        with pytest.raises(FFmpegNotFound, match="VOXFRAME_FFMPEG_PATH"):
            find_ffmpeg("/nonexistent/ffmpeg-xyz")

    def test_missing_binary_gives_install_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda _: None)
        with pytest.raises(FFmpegNotFound) as exc:
            find_ffmpeg()
        message = str(exc.value)
        assert "winget install" in message
        assert "brew install" in message
        assert "apt install" in message


class TestSettings:
    """Settings behaviour, isolated from the developer's own configuration.

    These tests assert what Voxframe does with *no* configuration, so they must
    not see a real `.env` or exported keys. Without this a developer who has
    configured Pexels fails tests that say nothing about the code — which is
    exactly what happened once the sourcing keys were added.
    """

    @pytest.fixture(autouse=True)
    def _isolate_environment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        for name in list(os.environ):
            if name.startswith("VOXFRAME_"):
                monkeypatch.delenv(name, raising=False)

        # Point pydantic-settings at an empty file in a temp directory rather
        # than the repository's real .env.
        monkeypatch.chdir(tmp_path)

    def test_defaults_work_with_no_config(self) -> None:
        """The core requirement: runs with no .env and no API keys."""
        s = Settings()
        assert s.fps == 30.0
        assert s.aspect is AspectRatio.HORIZONTAL
        assert s.quality is QualityPreset.STANDARD
        assert s.intermediate is IntermediateFormat.X264_CRF16
        assert not s.has_any_sourcing_key

    def test_aspect_dimensions(self) -> None:
        assert AspectRatio.VERTICAL.dimensions_1080 == (1080, 1920)
        assert AspectRatio.HORIZONTAL.dimensions_1080 == (1920, 1080)
        assert AspectRatio.SQUARE.dimensions_1080 == (1080, 1080)

    def test_inverted_pacing_rejected(self) -> None:
        with pytest.raises(ValueError, match="must exceed"):
            Settings(scene_min_seconds=8.0, scene_max_seconds=4.0)

    def test_parallax_displacement_bounded(self) -> None:
        """A large cap would create disocclusions too big to inpaint (D-007)."""
        with pytest.raises(ValueError):
            Settings(parallax_max_displacement=0.9)

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VOXFRAME_FPS", "60")
        assert Settings().fps == 60.0

    def test_none_overrides_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unset CLI flag must not clobber an environment value."""
        monkeypatch.setenv("VOXFRAME_FPS", "25")
        assert get_settings(fps=None).fps == 25.0
        assert get_settings(fps=50.0).fps == 50.0

    def test_api_keys_hidden_from_repr(self) -> None:
        """Secrets must not leak into logs or tracebacks."""
        s = Settings(pexels_api_key="secret-value-123")
        assert "secret-value-123" not in repr(s)

    def test_sourcing_key_detection(self) -> None:
        assert not Settings().has_any_sourcing_key
        assert Settings(pexels_api_key="k").has_any_sourcing_key
