"""Voice polish (D-173): its decisions, its switch, and its cache.

The switch's promise is exact: with polish off, the voice in the mix is the
recording's own stem, sample for sample, with nothing applied.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

soundfile = pytest.importorskip("soundfile")

from voxframe.plan.audio_mix import AudioMix  # noqa: E402
from voxframe.render.audio import voice as voice_module  # noqa: E402
from voxframe.render.audio.mixdown import (  # noqa: E402
    RATE,
    MixReport,
    SpeechSpan,
    Stems,
    _mix_block,
    _music_envelope,
)
from voxframe.render.audio.voice import (  # noqa: E402
    MAX_REDUCTION_DB,
    MIN_REDUCTION_DB,
    _reduction,
    background,
    music_underneath,
)


def _stems(tmp_path: Path, *, polished: bool = True) -> Stems:
    rng = np.random.default_rng(7)
    raw = (rng.standard_normal(RATE * 3) * 0.1).astype(np.float32)
    soundfile.write(str(tmp_path / "voice.wav"), raw, RATE, subtype="FLOAT")
    extra: dict[str, object] = {}
    if polished:
        soundfile.write(str(tmp_path / "polished.wav"), raw * 0.5, RATE, subtype="FLOAT")
        extra = {
            "voice_polished": tmp_path / "polished.wav",
            "spans_polished": (SpeechSpan(0.5, 2.5, -26.0, -90.0),),
            "voice_lufs_polished": -26.0,
            "polish": {"problems": ["Polishing took 7.0 dB from the voice."],
                       "music_in_recording": False},
        }
    return Stems(
        voice=tmp_path / "voice.wav",
        music=None,
        spans=(SpeechSpan(0.5, 2.5, -20.0, -90.0),),
        landing=3.0,
        video_end=3.0,
        fps=30.0,
        voice_lufs=-20.0,
        **extra,  # type: ignore[arg-type]
    )


class TestTheSwitch:
    def test_polish_off_is_exactly_the_recording(self, tmp_path: Path) -> None:
        stems = _stems(tmp_path)
        mix = AudioMix(voice_polish=False)
        chosen = stems.for_mix(mix)

        block = _mix_block(chosen, mix, 0, RATE * 3, _music_envelope(chosen, mix), soundfile)

        raw, _ = soundfile.read(str(tmp_path / "voice.wav"), dtype="float32")
        assert np.array_equal(block[:, 0], raw) and np.array_equal(block[:, 1], raw)

    def test_polish_on_uses_the_polished_stem_and_its_levels(self, tmp_path: Path) -> None:
        stems = _stems(tmp_path)

        chosen = stems.for_mix(AudioMix())

        assert chosen.voice == tmp_path / "polished.wav"
        assert chosen.voice_lufs == -26.0 and chosen.spans[0].voice_db == -26.0

    def test_without_a_polished_stem_the_original_is_used(self, tmp_path: Path) -> None:
        stems = _stems(tmp_path, polished=False)

        assert stems.for_mix(AudioMix()) == stems

    def test_the_polish_check_counts_only_when_polish_was_used(self) -> None:
        base = {
            "destination": "youtube", "target_lufs": -14.0, "target_true_peak": -1.0,
            "integrated_lufs": -14.0, "true_peak": -2.0, "min_speech_margin_db": None,
            "speech_margin_setting_db": 15.0, "polish": {"problems": ["dull"]},
        }

        assert MixReport(**base, voice_polished=True).problems == ["dull"]  # type: ignore[arg-type]
        assert MixReport(**base, voice_polished=False).passed  # type: ignore[arg-type]


class TestKeptStems:
    def test_the_polished_fields_survive_saving(self, tmp_path: Path) -> None:
        stems = _stems(tmp_path)
        stems.save(tmp_path / "stems.json")

        assert Stems.load(tmp_path / "stems.json") == stems

    def test_stems_kept_before_polish_still_load(self, tmp_path: Path) -> None:
        stems = _stems(tmp_path, polished=False)
        stems.save(tmp_path / "stems.json")
        data = json.loads((tmp_path / "stems.json").read_text(encoding="utf-8"))
        for key in ("voice_polished", "spans_polished", "voice_lufs_polished", "polish"):
            data.pop(key)
        (tmp_path / "stems.json").write_text(json.dumps(data), encoding="utf-8")

        loaded = Stems.load(tmp_path / "stems.json")

        assert loaded.voice_polished is None and loaded.for_mix(AudioMix()).voice == stems.voice

    def test_the_polished_voice_is_made_once(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from voxframe.render.compose import from_plan

        calls: list[list[tuple[float, float]] | None] = []

        def fake_polish(raw, output, caps, pauses=None):  # type: ignore[no-untyped-def]
            calls.append(pauses)
            output.write_bytes(b"polished")
            return SimpleNamespace(as_dict=lambda: {"problems": [], "music_in_recording": True})

        monkeypatch.setattr(voice_module, "polish_voice", fake_polish)
        voice = tmp_path / "voice_abc.wav"
        voice.write_bytes(b"raw")
        words = [(0.0, 1.0), (1.1, 2.0), (2.6, 3.0)]

        first = from_plan._polished_voice(voice, words, caps=None)  # type: ignore[arg-type]
        second = from_plan._polished_voice(voice, words, caps=None)  # type: ignore[arg-type]

        assert first == second and first[1] == {"problems": [], "music_in_recording": True}
        assert calls == [[(2.0, 2.6)]]  # once; only the gap long enough to be a pause

    def test_a_failed_polish_leaves_the_original(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from voxframe.render.compose import from_plan

        def failing(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("ffmpeg failed")

        monkeypatch.setattr(voice_module, "polish_voice", failing)
        voice = tmp_path / "voice_abc.wav"
        voice.write_bytes(b"raw")

        assert from_plan._polished_voice(voice, [], caps=None) == (None, None)  # type: ignore[arg-type]


class TestDecisions:
    def test_music_under_the_voice_turns_reduction_off(self) -> None:
        reduction, note = _reduction(floor=-24.0, flatness=0.0, speech=-18.0)

        assert reduction == 0 and "Music" in note
        assert music_underneath(-24.0, 0.0, -18.0)

    def test_a_quiet_room_is_left_alone(self) -> None:
        reduction, note = _reduction(floor=-70.0, flatness=0.5, speech=-20.0)

        assert reduction == 0 and "already quiet" in note
        assert not music_underneath(-70.0, 0.5, -20.0)

    def test_plainly_tonal_music_counts_whatever_its_level(self) -> None:
        """The talk's music read 20.1 dB under its speech through loose gaps."""
        assert music_underneath(floor=-28.5, flatness=0.0, speech=-8.4)
        assert not music_underneath(floor=-70.0, flatness=0.0, speech=-8.4)  # inaudible

    def test_words_in_loose_gaps_do_not_raise_the_floor(self) -> None:
        """Gaps that hold the ends of words still find the quiet room under them."""
        rng = np.random.default_rng(5)
        seconds = np.arange(RATE * 12) / RATE
        room = rng.standard_normal(len(seconds)) * 10 ** (-50 / 20)
        talking = (np.sin(2 * np.pi * 0.25 * seconds) > -0.3).astype(float)
        speech = rng.standard_normal(len(seconds)) * 0.1 * talking
        # "Gaps" laid over speech, as word timings that run short would give.
        gaps = [(start, start + 0.6) for start in np.arange(0.5, 11.0, 1.0)]

        floor, _, _ = background((room + speech).astype(np.float32), RATE, gaps)

        assert floor == pytest.approx(-50.0, abs=3.0)

    def test_a_resampled_recording_is_not_heard_as_tonal(self) -> None:
        """A 16 kHz recording at 48 kHz is empty above 8 kHz: that is not music."""
        rng = np.random.default_rng(9)
        seconds = np.arange(RATE * 6) / RATE
        hiss = rng.standard_normal(len(seconds)) * 10 ** (-50 / 20)
        speech = rng.standard_normal(len(seconds)) * 0.1 * (np.sin(2 * np.pi * 0.5 * seconds) > 0)
        spectrum = np.fft.rfft(hiss + speech)
        spectrum[np.fft.rfftfreq(len(seconds), 1 / RATE) > 8000] = 0  # as a 16 kHz recording
        recording = np.fft.irfft(spectrum, n=len(seconds))

        measured = background(recording.astype(np.float32), RATE, [(1.1, 1.9), (3.1, 3.9)])

        assert measured[1] > 0.3 and not music_underneath(*measured)

    @pytest.mark.parametrize("floor", [-64.0, -58.0, -45.0, -30.0])
    def test_noise_is_reduced_within_natural_limits(self, floor: float) -> None:
        reduction, note = _reduction(floor=floor, flatness=0.5, speech=-20.0)

        assert MIN_REDUCTION_DB <= reduction <= MAX_REDUCTION_DB and note == ""

    def test_a_tone_between_words_is_heard_as_music_and_hiss_is_not(self) -> None:
        seconds = np.arange(RATE * 6) / RATE
        speech = np.sin(2 * np.pi * 180 * seconds) * 0.3 * (np.sin(2 * np.pi * 0.5 * seconds) > 0)
        pauses = [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
        tone = np.sin(2 * np.pi * 440 * seconds) * 0.05
        hiss = np.random.default_rng(3).standard_normal(len(seconds)) * 0.002

        tonal = background((speech + tone).astype(np.float32), RATE, pauses)
        noisy = background((speech + hiss).astype(np.float32), RATE, pauses)

        assert music_underneath(*tonal)
        assert not music_underneath(*noisy)


class TestClashWarning:
    def test_music_on_music_is_warned_about(self) -> None:
        from voxframe.jobs.pipeline import MUSIC_CLASH_WARNING, sound_warnings

        sound = {"problems": [], "min_speech_margin_db": 15.0,
                 "polish": {"music_in_recording": True}}

        assert sound_warnings(SimpleNamespace(sound=sound)) == [MUSIC_CLASH_WARNING]  # type: ignore[arg-type]

    def test_no_added_music_no_warning(self) -> None:
        from voxframe.jobs.pipeline import sound_warnings

        sound = {"problems": [], "min_speech_margin_db": None,
                 "polish": {"music_in_recording": True}}

        assert sound_warnings(SimpleNamespace(sound=sound)) == []  # type: ignore[arg-type]
