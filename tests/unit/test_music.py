"""The music bed: level, ducking, looping and credits (D-091, D-100)."""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.plan import PlannedScene, PlanWord, ScenePlan
from voxframe.render.audio import MusicSettings, music_filter_chain
from voxframe.render.audio.music import DEFAULT_MUSIC_GAIN, DUCK_THRESHOLD


def _chain(
    video: float = 45.0, music: float = 120.0, **settings: object
) -> str:
    return music_filter_chain(
        MusicSettings(path=Path("track.mp3"), **settings),  # type: ignore[arg-type]
        video,
        music,
    )


class TestDucking:
    def test_the_narration_is_split_for_the_sidechain(self) -> None:
        """A stream consumed by sidechaincompress is not available again."""
        chain = _chain()

        assert "asplit=2[speech][key]" in chain

    def test_the_bed_is_compressed_against_the_speech(self) -> None:
        chain = _chain()

        assert "sidechaincompress" in chain
        assert "[bed][key]sidechaincompress" in chain

    def test_the_threshold_sits_below_speech_level(self) -> None:
        """Speech averages about -25 dB, linear 0.054 (D-100)."""
        assert DUCK_THRESHOLD < 0.054

    def test_ducking_can_be_disabled(self) -> None:
        chain = _chain(duck=False)

        assert "sidechaincompress" not in chain
        assert "amix" in chain

    def test_amix_does_not_halve_the_narration(self) -> None:
        """Without normalize=0, amix divides by the input count."""
        assert "normalize=0" in _chain()


class TestLevel:
    def test_the_default_gain_is_audible_under_speech(self) -> None:
        """0.18 was 22 dB down once ducked — inaudible (D-100)."""
        assert DEFAULT_MUSIC_GAIN > 0.2

    def test_the_default_gain_does_not_compete(self) -> None:
        assert DEFAULT_MUSIC_GAIN < 0.5

    def test_the_gain_reaches_the_chain(self) -> None:
        assert f"volume={0.42:.3f}" in _chain(gain=0.42)

    def test_the_bed_fades_in_and_out(self) -> None:
        chain = _chain()

        assert "afade=t=in" in chain
        assert "afade=t=out" in chain

    def test_the_fade_out_ends_with_the_video(self) -> None:
        chain = _chain(video=45.0, fade_out=2.5)

        assert "afade=t=out:st=42.50" in chain


class TestLength:
    def test_a_long_track_is_trimmed(self) -> None:
        chain = _chain(video=45.0, music=120.0)

        assert "atrim=0:45.000" in chain
        assert "acrossfade" not in chain

    def test_a_short_track_loops_with_a_crossfade(self) -> None:
        """A butt-joined loop has an audible seam (D-085, D-100)."""
        chain = _chain(video=45.0, music=20.0)

        assert "acrossfade" in chain
        assert "aloop" not in chain

    def test_looping_produces_enough_passes(self) -> None:
        chain = _chain(video=45.0, music=10.0)

        # Each pass contributes less than its full length because of the
        # overlap, so there must be more than video/music copies.
        assert chain.count("acrossfade") >= 4

    def test_an_unknown_length_trims_and_pads(self) -> None:
        """Correct whether the track turns out long or short."""
        chain = _chain(video=45.0, music=0.0)

        assert "atrim" in chain
        assert "apad" in chain

    def test_the_bed_always_matches_the_video(self) -> None:
        for music in (5.0, 20.0, 45.0, 200.0):
            assert "atrim=0:45.000" in _chain(video=45.0, music=music)


class TestCredits:
    def _plan(self, **music: str) -> ScenePlan:
        return ScenePlan(
            audio_path="a.wav",
            audio_sha256="0" * 64,
            audio_duration=5.0,
            fps=30.0,
            total_frames=150,
            scenes=(
                PlannedScene(
                    index=0,
                    start_frame=0,
                    end_frame=150,
                    words=(PlanWord(text="hi", start=0.0, end=0.5),),
                ),
            ),
            **music,  # type: ignore[arg-type]
        )

    def test_no_music_means_no_music_credit(self) -> None:
        assert not any("Music:" in line for line in self._plan().credits())

    def test_a_supplied_credit_is_used(self) -> None:
        plan = self._plan(
            music_path="track.mp3", music_credit="Artist - Title (CC BY 4.0)"
        )

        assert plan.credits()[-1] == "Music: Artist - Title (CC BY 4.0)"

    def test_an_unattributed_track_is_recorded_honestly(self) -> None:
        """Voxframe does not invent an attribution (D-091)."""
        plan = self._plan(music_path="/some/where/my-song.mp3")

        assert plan.credits()[-1] == "Music: my-song.mp3 (supplied by the user)"

    def test_the_music_credit_comes_last(self) -> None:
        plan = self._plan(music_path="track.mp3")

        assert "Music:" in plan.credits()[-1]

    @pytest.mark.parametrize("credit", ["", "   "])
    def test_blank_credit_falls_back_to_the_filename(self, credit: str) -> None:
        plan = self._plan(music_path="track.mp3", music_credit=credit)

        assert "supplied by the user" in plan.credits()[-1]


class TestMusicSettings:
    def test_attribution_prefers_the_supplied_credit(self) -> None:
        settings = MusicSettings(path=Path("x.mp3"), credit="Someone")

        assert settings.attribution() == "Music: Someone"

    def test_attribution_falls_back_to_the_filename(self) -> None:
        settings = MusicSettings(path=Path("/a/b/x.mp3"))

        assert "x.mp3" in settings.attribution()
        assert "user" in settings.attribution()
