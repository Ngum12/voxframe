"""The generated score (D-176): styles as data, seeded variation, rendering, finishing.

Runs on a tiny synthetic sample folder (``score_kit``), so it needs neither
the real sample pack nor a recording.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pytest

soundfile = pytest.importorskip("soundfile")
pytest.importorskip("scipy")

from tests.unit.score_kit import make_samples, make_speech  # noqa: E402
from voxframe.music.score import ScoreUnavailable, events_for, make_score, styles  # noqa: E402
from voxframe.music.score.styles import StyleError, load_style  # noqa: E402

DURATION = 40.0


@pytest.fixture(scope="module")
def kit(tmp_path_factory: pytest.TempPathFactory) -> dict:  # type: ignore[type-arg]
    root = tmp_path_factory.mktemp("score")
    voice = root / "voice.wav"
    words = make_speech(voice, DURATION)
    return {"samples": make_samples(root / "samples"), "voice": voice, "words": words, "root": root}


def _score(kit: dict, style: str, seed: int, name: str) -> Path:  # type: ignore[type-arg]
    output = kit["root"] / f"{name}.flac"
    make_score(
        words=kit["words"],
        voice=kit["voice"],
        duration=DURATION,
        style_name=style,
        seed=seed,
        output=output,
        work_dir=kit["root"] / f"work_{name}",
        samples=kit["samples"],
    )
    return output


# --- styles ------------------------------------------------------------------------


class TestStyles:
    def test_every_shipped_style_loads(self) -> None:
        loaded = styles()

        assert {"inspiring", "calm", "reflective", "cinematic"} <= set(loaded)
        assert list(loaded) == sorted(loaded, key=lambda n: (loaded[n].order, n))
        for style in loaded.values():
            assert style.label and style.progressions and style.tonics

    def test_a_new_file_is_a_new_style(self, tmp_path: Path) -> None:
        """Styles are data: a file added, a style added, no code changed."""
        from voxframe.music.score.styles import STYLES_DIR

        text = (STYLES_DIR / "calm.toml").read_text(encoding="utf-8")
        (tmp_path / "gentle.toml").write_text(
            text.replace('name = "calm"', 'name = "gentle"').replace(
                'label = "Calm"', 'label = "Gentle"'
            ),
            encoding="utf-8",
        )

        assert load_style(tmp_path / "gentle.toml").label == "Gentle"

    @pytest.mark.parametrize(
        "change,message",
        [
            (
                ('["I", "IVmaj7", "vi7", "IV"]', '["I", "IVsus9", "vi7", "IV"]'),
                "unknown chord 'IVsus9'",
            ),
            (('name = "calm"', 'name = "quiet"'), "name must be the file's name"),
            (("pad = 0", "kazoo = 0"), "layers.kazoo is not a part"),
            (("max = 2", "max = 7"), "energy min and max"),
            (('tonics = ["F"', 'tonics = ["H"'), "'H' is not a note name"),
        ],
    )
    def test_a_mistake_says_where(
        self, tmp_path: Path, change: tuple[str, str], message: str
    ) -> None:
        from voxframe.music.score.styles import STYLES_DIR

        text = (STYLES_DIR / "calm.toml").read_text(encoding="utf-8")
        assert change[0] in text
        path = tmp_path / "calm.toml"
        path.write_text(text.replace(change[0], change[1], 1), encoding="utf-8")

        with pytest.raises(StyleError, match=r"calm\.toml") as caught:
            load_style(path)
        assert message in str(caught.value)


# --- composing -----------------------------------------------------------------------


class TestComposition:
    def _events(self, kit: dict, style: str, seed: int):  # type: ignore[no-untyped-def,type-arg]
        return events_for(kit["words"], kit["voice"], DURATION, styles()[style], seed)

    def test_the_same_seed_gives_the_same_piece(self, kit: dict) -> None:  # type: ignore[type-arg]
        _, first, events = self._events(kit, "inspiring", 7)
        _, again, events_again = self._events(kit, "inspiring", 7)

        assert first == again and events == events_again

    def test_seeds_vary_key_tempo_and_notes(self, kit: dict) -> None:  # type: ignore[type-arg]
        pieces = [self._events(kit, "inspiring", seed) for seed in range(12)]

        assert len({p[1].tonic for p in pieces}) >= 3
        assert len({p[1].tempo for p in pieces}) >= 6
        assert len({p[2] for p in pieces}) == 12

    def test_it_lands_home_on_the_last_word(self, kit: dict) -> None:  # type: ignore[type-arg]
        speech, composition, _ = self._events(kit, "reflective", 3)

        last = composition.chords[-1]
        assert last.start == speech.landing and last.symbol == "vi"
        assert composition.chords[-2].symbol == "IV"

    def test_layers_enter_one_level_at_a_time(self, kit: dict) -> None:  # type: ignore[type-arg]
        for seed in range(6):
            _, composition, _ = self._events(kit, "cinematic", seed)
            levels = [c.level for c in composition.chords[:-1]]
            assert all(b - a <= 1 for a, b in itertools.pairwise(levels))

    def test_a_style_plays_only_its_own_parts(self, kit: dict) -> None:  # type: ignore[type-arg]
        _, _, calm = self._events(kit, "calm", 1)
        _, _, inspiring = self._events(kit, "inspiring", 1)

        calm_instruments = {e.instrument for e in calm}
        assert "timpani" not in calm_instruments and "violins_spic" not in calm_instruments
        assert "harp" in calm_instruments
        assert {"cellos_spic", "timpani"} <= {e.instrument for e in inspiring}

    def test_long_pauses_swell(self, kit: dict) -> None:  # type: ignore[type-arg]
        speech, _, events = self._events(kit, "inspiring", 2)

        assert len(speech.pauses) >= 2
        for start, _ in speech.pauses:
            assert any(
                e.instrument == "cymbal" and abs(e.at - (start - 1.2)) < 0.01 for e in events
            )

    def test_sections_start_at_the_speech(self, kit: dict) -> None:  # type: ignore[type-arg]
        speech, composition, _ = self._events(kit, "inspiring", 4)

        starts = {round(s, 3) for s, _ in speech.spans}
        for section in composition.sections[1:]:
            assert round(section.start, 3) in starts


# --- rendering and finishing ---------------------------------------------------------


class TestRendering:
    def test_the_same_seed_renders_the_same_audio(self, kit: dict) -> None:  # type: ignore[type-arg]
        first = _score(kit, "inspiring", 11, "a")
        again = _score(kit, "inspiring", 11, "b")
        other = _score(kit, "inspiring", 12, "c")

        # The audio, not the file: libsndfile stamps the time into a float WAV's header.
        def samples(path: Path) -> np.ndarray:
            return soundfile.read(str(path), dtype="float32")[0]

        assert np.array_equal(samples(first), samples(again))
        assert not np.array_equal(samples(first), samples(other))
        assert soundfile.info(str(first)).frames == round(DURATION * 48000)

    def test_blocks_do_not_change_the_sound(
        self, kit: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[type-arg]
        from voxframe.music.score import render

        whole = _score(kit, "cinematic", 5, "whole")
        monkeypatch.setattr(render, "BLOCK_SECONDS", 3.0)
        blocked = _score(kit, "cinematic", 5, "blocked")

        a, _ = soundfile.read(str(whole), dtype="float32")
        b, _ = soundfile.read(str(blocked), dtype="float32")
        assert float(np.max(np.abs(a - b))) < 1e-4

    def test_pauses_stay_under_the_voice(self, kit: dict) -> None:  # type: ignore[type-arg]
        from voxframe.music.score import render

        result = make_score(
            words=kit["words"],
            voice=kit["voice"],
            duration=DURATION,
            style_name="inspiring",
            seed=21,
            output=kit["root"] / "p.flac",
            work_dir=kit["root"] / "work_p",
            samples=kit["samples"],
        )

        assert result.pause_under_voice_db is not None
        assert result.pause_under_voice_db >= render.PAUSE_CEILING_DB - 0.2

    def test_the_speech_band_dips_under_the_words(
        self, kit: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[type-arg]
        from voxframe.music.score import render

        dipped = _score(kit, "calm", 6, "dipped")
        monkeypatch.setattr(render, "SPEECH_BAND_DIP_DB", 0.0)
        plain = _score(kit, "calm", 6, "plain")

        def band_db(path: Path, start: float, end: float) -> float:
            audio, rate = soundfile.read(str(path), dtype="float32")
            part = audio[int(start * rate) : int(end * rate)].mean(axis=1)
            spectrum = np.abs(np.fft.rfft(part * np.hanning(len(part)))) ** 2
            freqs = np.fft.rfftfreq(len(part), 1 / rate)
            return float(10 * np.log10(np.sum(spectrum[(freqs > 700) & (freqs < 1500)]) + 1e-12))

        speaking = (2.0, 6.0)  # inside the first phrase
        assert band_db(plain, *speaking) - band_db(dipped, *speaking) > 5.0

    def test_missing_sounds_are_said(self, kit: dict, tmp_path: Path) -> None:  # type: ignore[type-arg]
        with pytest.raises(ScoreUnavailable, match="not installed"):
            make_score(
                words=kit["words"],
                voice=kit["voice"],
                duration=DURATION,
                style_name="calm",
                seed=1,
                output=tmp_path / "x.flac",
                work_dir=tmp_path,
                samples=tmp_path / "nothing",
            )

    def test_an_unknown_style_is_said(self, kit: dict, tmp_path: Path) -> None:  # type: ignore[type-arg]
        with pytest.raises(ScoreUnavailable, match="no score style called 'polka'"):
            make_score(
                words=kit["words"],
                voice=kit["voice"],
                duration=DURATION,
                style_name="polka",
                seed=1,
                output=tmp_path / "x.flac",
                work_dir=tmp_path,
                samples=kit["samples"],
            )


# --- the plan, the pipeline and the API -------------------------------------------------


class TestThePlan:
    def _plan(self, **music: object):  # type: ignore[no-untyped-def]
        from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan

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

    def test_a_score_is_credited(self) -> None:
        from voxframe.plan.score_choice import ScoreChoice

        plan = self._plan(score=ScoreChoice(style="calm", seed=4))

        assert "generated by Voxframe" in plan.credits()[-1]
        assert "Versilian" in plan.credits()[-1]

    def test_a_track_and_a_score_are_never_both(self) -> None:
        from voxframe.plan.score_choice import ScoreChoice

        with pytest.raises(ValueError, match="either a music track or a generated score"):
            self._plan(score=ScoreChoice(style="calm", seed=4), music_path="track.mp3")

    def test_the_choice_survives_saving(self, tmp_path: Path) -> None:
        from voxframe.plan.scene_plan import ScenePlan
        from voxframe.plan.score_choice import ScoreChoice

        self._plan(score=ScoreChoice(style="reflective", seed=99)).save(tmp_path / "p.json")

        assert ScenePlan.load(tmp_path / "p.json").score == ScoreChoice(style="reflective", seed=99)


class TestTheTools:
    """The score's SciPy comes with the music download (D-172, D-176)."""

    def test_a_score_fetches_them_the_first_time(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from voxframe import components
        from voxframe.components import Manifest, Wheel
        from voxframe.jobs import pipeline

        manifest = Manifest(
            name="music",
            wheels=(Wheel("x.whl", "u", "0" * 64, 91_000_000),),
            path=tmp_path / "m.json",
        )
        monkeypatch.setattr(components, "music_ready", lambda: False)
        monkeypatch.setattr(components, "music_manifest", lambda python=None: manifest)
        installed: list[bool] = []
        monkeypatch.setattr(components, "install_music", lambda progress: installed.append(True))
        messages: list[str] = []

        warnings = pipeline._prepare_music(
            None, lambda stage, message, f: messages.append(message), score=True
        )

        assert installed and warnings == []
        assert messages[0].startswith(
            "Preparing the music score (a one-time download of about 91 MB"
        )

    def test_a_failed_download_leaves_the_video_without_music(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from voxframe import components
        from voxframe.components import ComponentError, Manifest, Wheel
        from voxframe.jobs import pipeline

        monkeypatch.setattr(components, "music_ready", lambda: False)
        monkeypatch.setattr(
            components,
            "music_manifest",
            lambda python=None: Manifest(
                name="music", wheels=(Wheel("x", "u", "0", 1),), path=tmp_path / "m.json"
            ),
        )

        def failing(progress):  # type: ignore[no-untyped-def]
            raise ComponentError("offline")

        monkeypatch.setattr(components, "install_music", failing)

        warnings = pipeline._prepare_music(None, lambda *a: None, score=True)

        assert len(warnings) == 1 and "has no music" in warnings[0] and "offline" in warnings[0]


class TestTheRequest:
    @pytest.fixture
    def context(self, tmp_path: Path):  # type: ignore[no-untyped-def]
        pytest.importorskip("fastapi")
        from voxframe.api.app import ApiContext
        from voxframe.api.security import SessionToken
        from voxframe.config.settings import Settings
        from voxframe.jobs.store import JobStore

        root = tmp_path / "web"
        return ApiContext(
            settings=Settings(
                library_path=tmp_path / "l", cache_path=tmp_path / "c", output_path=tmp_path / "o"
            ),
            store=JobStore(root, reap_interval=None),
            token=SessionToken("t"),
            allowed_paths=(root.resolve(),),
        )

    def _voice(self, context) -> Path:  # type: ignore[no-untyped-def]
        path = context.store.root / "uploads" / "voice" / "source.wav"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"audio")
        return path

    def test_a_style_becomes_a_seeded_choice(self, context) -> None:  # type: ignore[no-untyped-def]
        from voxframe.api.app import RenderRequest, _job_options

        options = _job_options(
            RenderRequest(upload_id="voice", score_style="calm"), self._voice(context), context
        )
        again = _job_options(
            RenderRequest(upload_id="voice", score_style="calm"),
            context.store.root / "uploads" / "voice" / "source.wav",
            context,
        )

        assert options.score is not None and options.score.style == "calm"
        assert (
            again.score is not None and again.score.seed != options.score.seed
        )  # each video its own variation
        assert options.music is None

    def test_an_unknown_style_is_refused(self, context) -> None:  # type: ignore[no-untyped-def]
        from fastapi import HTTPException

        from voxframe.api.app import RenderRequest, _job_options

        with pytest.raises(HTTPException) as caught:
            _job_options(
                RenderRequest(upload_id="voice", score_style="polka"), self._voice(context), context
            )
        assert caught.value.status_code == 422
