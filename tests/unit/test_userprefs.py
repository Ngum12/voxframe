"""User settings: consent and API keys (D-116).

The CLI reads keys from the environment; a browser user has no `.env` and no
way to make one. These tests pin where those keys go, what the API gives back,
and the rule that matters most: **a key never lands in the repository**, and a
stored key is never sent back to the browser in full.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxframe.config.settings import Settings
from voxframe.config.userprefs import (
    KEY_FIELDS,
    UserPreferences,
    apply_to_settings,
    config_path,
    load_preferences,
    save_preferences,
)


class TestConfigLocation:
    def test_the_config_is_outside_the_repository(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A key must never sit in a file that could be committed (D-113)."""
        monkeypatch.delenv("VOXFRAME_CONFIG_DIR", raising=False)
        repository = Path(__file__).resolve().parents[2]

        resolved = config_path().resolve()

        assert repository not in resolved.parents
        assert resolved != repository

    def test_an_override_is_honoured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXFRAME_CONFIG_DIR", str(tmp_path))

        assert config_path() == tmp_path / "config.json"

    def test_the_file_is_named_config_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VOXFRAME_CONFIG_DIR", raising=False)

        assert config_path().name == "config.json"


class TestRoundTrip:
    def test_nothing_stored_gives_defaults(self, tmp_path: Path) -> None:
        preferences = load_preferences(tmp_path / "absent.json")

        assert preferences.api_keys == {}
        assert preferences.sourcing_consent is None

    def test_keys_survive_a_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        save_preferences(
            UserPreferences(api_keys={"pexels": "abc123"}, sourcing_consent=True), path
        )

        restored = load_preferences(path)

        assert restored.api_keys == {"pexels": "abc123"}
        assert restored.sourcing_consent is True

    def test_a_corrupt_file_yields_defaults(self, tmp_path: Path) -> None:
        """The app must still start; the user can set things again."""
        path = tmp_path / "config.json"
        path.write_text("{not json", encoding="utf-8")

        assert load_preferences(path).api_keys == {}

    def test_unknown_adapters_are_dropped(self, tmp_path: Path) -> None:
        """A hand-edited file must not inject other configuration."""
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({"api_keys": {"pexels": "ok", "evil": "x"}}), encoding="utf-8"
        )

        assert set(load_preferences(path).api_keys) == {"pexels"}

    def test_non_string_values_are_dropped(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({"api_keys": {"pexels": {"nested": "object"}}}), encoding="utf-8"
        )

        assert load_preferences(path).api_keys == {}


class TestConsentStates:
    """"Not yet asked" must be distinguishable from "said no"."""

    def test_unasked_is_none(self) -> None:
        assert UserPreferences().sourcing_consent is None
        assert not UserPreferences().has_been_asked

    def test_declining_is_recorded_as_false(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        save_preferences(UserPreferences(sourcing_consent=False), path)

        restored = load_preferences(path)

        assert restored.sourcing_consent is False
        assert restored.has_been_asked  # so the screen does not reappear

    def test_consent_without_a_key_is_not_enabled(self) -> None:
        """Openverse alone was measured as insufficient (D-077)."""
        assert not UserPreferences(sourcing_consent=True).sourcing_enabled

    def test_consent_with_a_key_is_enabled(self) -> None:
        preferences = UserPreferences(
            sourcing_consent=True, api_keys={"pexels": "abc"}
        )

        assert preferences.sourcing_enabled

    def test_a_key_without_consent_is_not_enabled(self) -> None:
        """Having a key is not permission to use it."""
        preferences = UserPreferences(api_keys={"pexels": "abc"})

        assert not preferences.sourcing_enabled


class TestMasking:
    def test_a_stored_key_is_never_returned_in_full(self) -> None:
        secret = "pexels-secret-key-value-1234"
        preferences = UserPreferences(api_keys={"pexels": secret})

        masked = preferences.masked_keys()["pexels"]

        assert secret not in masked
        assert masked.endswith("1234")

    def test_an_empty_key_is_omitted(self) -> None:
        assert UserPreferences(api_keys={"pexels": ""}).masked_keys() == {}


class TestSettingsLayering:
    def test_a_stored_key_reaches_settings(self) -> None:
        settings = Settings()
        preferences = UserPreferences(api_keys={"pexels": "from-browser"})

        applied = apply_to_settings(settings, preferences)

        assert applied.pexels_api_key == "from-browser"

    def test_the_environment_wins(self) -> None:
        """A developer's .env must not be silently overridden by a browser."""
        settings = Settings(pexels_api_key="from-environment")
        preferences = UserPreferences(api_keys={"pexels": "from-browser"})

        applied = apply_to_settings(settings, preferences)

        assert applied.pexels_api_key == "from-environment"

    def test_the_original_settings_are_unchanged(self) -> None:
        """apply_to_settings returns a copy; the caller's object is untouched."""
        settings = Settings(pexels_api_key=None)
        before = settings.pexels_api_key

        applied = apply_to_settings(settings, UserPreferences(api_keys={"pexels": "x"}))

        assert settings.pexels_api_key == before
        assert applied is not settings
        assert applied.pexels_api_key == "x"

    def test_every_key_field_names_a_real_setting(self) -> None:
        """A typo here would silently drop a key the user entered."""
        settings = Settings()

        for field_name in KEY_FIELDS.values():
            assert hasattr(settings, field_name), field_name
