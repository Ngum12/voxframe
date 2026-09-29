"""Model profiles and language configuration (D-067, D-068).

The profile exists so one setting covers every model download. The language
settings exist so a user whose audio detects badly does not have to pass
``--language`` on every invocation.

The load-bearing property tested here is that the transcription model is a
property of the *plan*, not of the render: a re-render must never change it,
because new word timings would invalidate caption corrections made against the
old ones.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from voxframe.config.settings import ModelProfile, QualityPreset, Settings
from voxframe.plan import PlannedScene, PlanWord, ScenePlan
from voxframe.transcribe import Transcriber, TranscriptionError


class TestModelProfile:
    def test_standard_selects_the_accurate_models(self) -> None:
        settings = Settings(profile=ModelProfile.STANDARD)

        assert settings.resolved_transcribe_model == "large-v3-turbo"
        assert settings.resolved_embed_model == "default"

    def test_lite_selects_the_small_models_together(self) -> None:
        """One setting, both downloads — that is the point of the profile."""
        settings = Settings(profile=ModelProfile.LITE)

        assert settings.resolved_transcribe_model == "base"
        assert settings.resolved_embed_model == "lite"

    def test_lite_is_substantially_smaller(self) -> None:
        assert (
            ModelProfile.LITE.approximate_download_mb
            < ModelProfile.STANDARD.approximate_download_mb / 3
        )

    def test_default_profile_is_standard(self) -> None:
        assert Settings().profile is ModelProfile.STANDARD

    @pytest.mark.parametrize(
        ("field", "value", "accessor"),
        [
            ("transcribe_model", "small", "resolved_transcribe_model"),
            ("embed_model", "default", "resolved_embed_model"),
        ],
    )
    def test_explicit_setting_overrides_the_profile(
        self, field: str, value: str, accessor: str
    ) -> None:
        settings = Settings(profile=ModelProfile.LITE, **{field: value})
        assert getattr(settings, accessor) == value

    def test_one_override_leaves_the_other_on_the_profile(self) -> None:
        settings = Settings(profile=ModelProfile.LITE, transcribe_model="medium")

        assert settings.resolved_transcribe_model == "medium"
        assert settings.resolved_embed_model == "lite"

    def test_profile_is_independent_of_quality(self) -> None:
        """Render quality must not imply a transcription model (D-067).

        A draft-then-final workflow would otherwise re-transcribe on the final
        render, discarding caption corrections made against the draft.
        """
        for quality in QualityPreset:
            settings = Settings(quality=quality)
            assert settings.resolved_transcribe_model == "large-v3-turbo"


class TestLanguageSettings:
    def test_language_defaults_to_detection(self) -> None:
        assert Settings().language is None

    def test_language_can_be_configured(self) -> None:
        assert Settings(language="en").language == "en"

    def test_languages_parses_a_candidate_set(self) -> None:
        assert Settings(languages="en,fr").allowed_languages == ("en", "fr")

    def test_languages_tolerates_spacing_and_case(self) -> None:
        """A config file written by hand should not need to be tidy."""
        assert Settings(languages=" EN , fr ,").allowed_languages == ("en", "fr")

    def test_no_languages_means_unrestricted(self) -> None:
        assert Settings().allowed_languages == ()

    def test_environment_variable_is_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The point of the setting is not passing --language every time."""
        monkeypatch.setenv("VOXFRAME_LANGUAGE", "fr")
        monkeypatch.setenv("VOXFRAME_LANGUAGES", "fr,en")

        settings = Settings()
        assert settings.language == "fr"
        assert settings.allowed_languages == ("fr", "en")


class TestAllowedLanguageDetection:
    """Candidate-set detection picks the best *allowed* language."""

    def _transcriber(self, allowed: tuple[str, ...]) -> Transcriber:
        return Transcriber("base", cache_dir=None, allowed_languages=allowed)

    def _mock_model(self, probabilities: list[tuple[str, float]]) -> mock.Mock:
        model = mock.Mock()
        model.detect_language.return_value = ("xx", 0.0, probabilities)
        return model

    def test_best_allowed_language_wins_over_best_overall(self) -> None:
        """The whole point: rule out an implausible answer without forcing one."""
        transcriber = self._transcriber(("en", "fr"))
        model = self._mock_model([("yo", 0.37), ("en", 0.31), ("fr", 0.02)])

        with (
            mock.patch.object(transcriber, "_load_model", return_value=model),
            mock.patch(
                "faster_whisper.audio.decode_audio", return_value=object()
            ),
        ):
            language, probability = transcriber._detect_allowed_language(
                Path("audio.wav")
            )

        assert language == "en"
        assert probability == pytest.approx(0.31)

    def test_probability_is_not_renormalised(self) -> None:
        """Reporting a renormalised 0.99 would hide real uncertainty.

        The low-confidence warning exists to say "detection was shaky"; making
        the number look confident because the alternatives were excluded would
        defeat it.
        """
        transcriber = self._transcriber(("en", "fr"))
        model = self._mock_model([("yo", 0.9), ("en", 0.05), ("fr", 0.05)])

        with (
            mock.patch.object(transcriber, "_load_model", return_value=model),
            mock.patch(
                "faster_whisper.audio.decode_audio", return_value=object()
            ),
        ):
            _, probability = transcriber._detect_allowed_language(Path("a.wav"))

        assert probability == pytest.approx(0.05)

    def test_unrestricted_winner_inside_the_set_is_kept(self) -> None:
        transcriber = self._transcriber(("en", "fr"))
        model = self._mock_model([("en", 0.88), ("fr", 0.10), ("yo", 0.02)])

        with (
            mock.patch.object(transcriber, "_load_model", return_value=model),
            mock.patch(
                "faster_whisper.audio.decode_audio", return_value=object()
            ),
        ):
            language, probability = transcriber._detect_allowed_language(
                Path("a.wav")
            )

        assert language == "en"
        assert probability == pytest.approx(0.88)

    def test_unknown_codes_raise_rather_than_falling_back(self) -> None:
        """Silently using an excluded language would be worse than an error."""
        transcriber = self._transcriber(("zz", "qq"))
        model = self._mock_model([("en", 0.9), ("fr", 0.1)])

        with (
            mock.patch.object(transcriber, "_load_model", return_value=model),
            mock.patch(
                "faster_whisper.audio.decode_audio", return_value=object()
            ),
            pytest.raises(TranscriptionError) as caught,
        ):
            transcriber._detect_allowed_language(Path("a.wav"))

        assert "zz" in str(caught.value)

    def test_allowed_languages_are_normalised(self) -> None:
        assert self._transcriber((" EN ", "Fr")).allowed_languages == ("en", "fr")


class TestCacheKeyIsolation:
    """A restricted run must not return an unrestricted cached transcript."""

    def _path(self, tmp_path: Path, **kwargs: object) -> Path | None:
        return Transcriber(
            "base", cache_dir=tmp_path, **kwargs
        )._cache_path("hash")  # type: ignore[arg-type]

    def test_restricting_languages_changes_the_key(self, tmp_path: Path) -> None:
        unrestricted = self._path(tmp_path)
        restricted = self._path(tmp_path, allowed_languages=("en", "fr"))

        assert unrestricted != restricted

    def test_different_candidate_sets_differ(self, tmp_path: Path) -> None:
        first = self._path(tmp_path, allowed_languages=("en", "fr"))
        second = self._path(tmp_path, allowed_languages=("en", "de"))

        assert first != second

    def test_same_set_is_stable(self, tmp_path: Path) -> None:
        first = self._path(tmp_path, allowed_languages=("en", "fr"))
        second = self._path(tmp_path, allowed_languages=("en", "fr"))

        assert first == second


class TestPlanRecordsTheModel:
    """The transcription model is a property of the plan (D-067)."""

    def _plan(self, **kwargs: object) -> ScenePlan:
        defaults: dict[str, object] = {
            "audio_path": "a.wav",
            "audio_sha256": "0" * 64,
            "audio_duration": 3.0,
            "fps": 30.0,
            "total_frames": 90,
            "transcribe_model": "faster-whisper/large-v3-turbo/int8",
            "scenes": (
                PlannedScene(
                    index=0,
                    start_frame=0,
                    end_frame=90,
                    text="hello world",
                    words=(
                        PlanWord(text="hello", start=0.0, end=0.5),
                        PlanWord(text="world", start=0.5, end=1.0),
                    ),
                ),
            ),
        }
        defaults.update(kwargs)
        return ScenePlan(**defaults)  # type: ignore[arg-type]

    def test_model_survives_a_round_trip(self, tmp_path: Path) -> None:
        plan = self._plan()
        path = plan.save(tmp_path / "p.json")

        assert ScenePlan.load(path).transcribe_model == plan.transcribe_model

    def test_language_provenance_is_recorded(self, tmp_path: Path) -> None:
        """A suspect transcript must be diagnosable from the plan alone."""
        plan = self._plan(language="en", language_probability=0.41)
        loaded = ScenePlan.load(plan.save(tmp_path / "p.json"))

        assert loaded.language == "en"
        assert loaded.language_probability == pytest.approx(0.41)

    def test_rendering_does_not_consult_a_transcriber(self) -> None:
        """Structural check: render_from_plan must not import transcription.

        This is what makes "never re-transcribes on render" a property of the
        code rather than a rule to remember.
        """
        source = Path("src/voxframe/render/compose/from_plan.py").read_text(
            encoding="utf-8"
        )
        assert "Transcriber" not in source
        assert "transcribe" not in source.replace("transcribe_model", "")
