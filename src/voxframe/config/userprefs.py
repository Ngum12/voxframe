"""Settings a user enters in the web app, stored in their own config directory.

The CLI reads keys from the environment and ``.env`` (D-035), which is right for
a terminal but useless in a browser: a non-developer has no ``.env`` and no way
to make one. So the web app writes to a per-user config file instead, and
:func:`apply_to_settings` layers it under the environment — an environment
variable still wins, so a developer's ``.env`` is never silently overridden by
something typed into a browser months ago.

**Where it lives.** The platform's normal config location, never the repository:

- Windows: ``%APPDATA%/voxframe/config.json``
- macOS: ``~/Library/Application Support/voxframe/config.json``
- Linux: ``$XDG_CONFIG_HOME/voxframe/config.json``, else ``~/.config``

The repository must never hold a key, in any file, at any point (D-113). A test
asserts this path resolves outside the working tree.

**What is stored.** API keys the user entered, and their sourcing consent. The
file is written with owner-only permissions where the platform supports it. It
is not encrypted, and does not pretend to be: a local file readable by the user
is exactly as protected as the ``.env`` it replaces, and claiming more would be
worse than claiming nothing.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

__all__ = [
    "KEY_FIELDS",
    "THEMES",
    "UserPreferences",
    "config_path",
    "load_preferences",
    "save_preferences",
]

log = structlog.get_logger(__name__)

#: Keys the web app can set, mapped to the settings field each one feeds.
#: Anything not listed here is refused, so a crafted request cannot write
#: arbitrary configuration into the user's file.
#: The looks the app can take (D-185).
THEMES = ("system", "dark", "light")

KEY_FIELDS: dict[str, str] = {
    "pexels": "pexels_api_key",
    "pixabay": "pixabay_api_key",
}


def config_path() -> Path:
    """Where this user's Voxframe configuration lives.

    Deliberately outside the repository: a key must never land in a file that
    could be committed, packaged or shipped in an installer (D-113).
    """
    override = os.environ.get("VOXFRAME_CONFIG_DIR")
    if override:
        return Path(override).expanduser() / "config.json"

    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        )

    return base / "voxframe" / "config.json"


@dataclass(slots=True)
class UserPreferences:
    """What the user set in the web app.

    Attributes:
        api_keys: Adapter name to key, for the adapters in :data:`KEY_FIELDS`.
        sourcing_consent: ``True`` once the user has agreed, ``False`` once they
            have declined, ``None`` while they have not been asked. The third
            state is the point: "not yet asked" must be distinguishable from
            "said no", or the consent screen either never appears or appears
            forever (D-116).
        consent_version: Which wording they agreed to. Re-asking is possible if
            what is sent ever changes.
    """

    api_keys: dict[str, str] = field(default_factory=dict)
    sourcing_consent: bool | None = None
    consent_version: int = 0
    #: The name the person credits their own uploads to, remembered so the
    #: Library's upload form is filled in next time (D-146).
    library_author: str = ""
    #: A library folder chosen in Settings, used at the next start unless the
    #: environment names one (D-156). Empty for the default.
    library_path: str = ""
    #: The model profile chosen on the Getting-ready screen: ``standard`` or
    #: ``lite`` (D-067, D-157). Empty until chosen; the environment wins.
    model_profile: str = ""
    #: The app's look (D-185): ``system`` follows the computer's light or dark
    #: setting; ``dark`` (Ember) and ``light`` (Paper) fix it.
    theme: str = "system"

    @property
    def has_been_asked(self) -> bool:
        return self.sourcing_consent is not None

    @property
    def sourcing_enabled(self) -> bool:
        """Whether imagery sourcing may run.

        Consent alone is not enough: without a key the only adapter is the
        keyless one, which Phase 4 measured as insufficient on its own (D-077).
        """
        return bool(self.sourcing_consent) and bool(self.api_keys)

    def masked_keys(self) -> dict[str, str]:
        """Keys as the UI shows them: present or absent, and a short tail.

        A key is never sent back to the browser in full. Once stored it can be
        replaced but not read, which is the same contract a password field has.
        """
        from voxframe.sourcing.secrets import mask

        return {
            name: mask(value) for name, value in sorted(self.api_keys.items()) if value
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "api_keys": self.api_keys,
            "sourcing_consent": self.sourcing_consent,
            "consent_version": self.consent_version,
            "library_author": self.library_author,
            "library_path": self.library_path,
            "model_profile": self.model_profile,
            "theme": self.theme,
        }


def load_preferences(path: Path | None = None) -> UserPreferences:
    """Read the user's preferences, or defaults if there are none.

    An unreadable or corrupt file yields defaults rather than an exception: the
    app must still start, and the user can set things again.
    """
    target = path or config_path()
    if not target.is_file():
        return UserPreferences()

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("userprefs.unreadable", path=str(target))
        return UserPreferences()

    keys = payload.get("api_keys") or {}
    return UserPreferences(
        # Only known adapters, and only strings: a hand-edited file must not be
        # able to inject other configuration.
        api_keys={
            name: value
            for name, value in keys.items()
            if name in KEY_FIELDS and isinstance(value, str) and value
        },
        sourcing_consent=payload.get("sourcing_consent"),
        consent_version=int(payload.get("consent_version") or 0),
        model_profile=(
            payload["model_profile"]
            if payload.get("model_profile") in ("standard", "lite")
            else ""
        ),
        library_path=(
            payload["library_path"]
            if isinstance(payload.get("library_path"), str)
            else ""
        ),
        theme=payload["theme"] if payload.get("theme") in THEMES else "system",
        library_author=(
            payload["library_author"][:120]
            if isinstance(payload.get("library_author"), str)
            else ""
        ),
    )


def save_preferences(
    preferences: UserPreferences, path: Path | None = None
) -> Path:
    """Write preferences, owner-readable only where the platform allows.

    Written to a temporary file and renamed, so an interrupted write cannot
    leave a half-file that loses the user's keys.
    """
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    temporary = target.with_suffix(".json.partial")
    temporary.write_text(
        json.dumps(preferences.as_dict(), indent=2), encoding="utf-8"
    )

    # Owner read/write only. POSIX honours this; on Windows it is a no-op, and
    # the file sits in the user's own roaming profile.
    try:
        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    temporary.replace(target)
    log.info("userprefs.saved", keys=sorted(preferences.api_keys))
    return target


def sourcing_active(settings: Any, preferences: UserPreferences) -> bool:
    """Whether a render should search online for imagery.

    Consent **and** at least one keyed source (D-116): Openverse alone was
    measured as insufficient (D-077), so consent without a key would search,
    find clipart, and look broken.

    The key may come from the app's own settings *or* the environment. An
    earlier rule counted only keys saved in the app, so a person whose keys are
    in ``.env`` -- the owner, and any developer -- could switch sourcing on and
    have nothing happen (D-132).
    """
    if not preferences.sourcing_consent:
        return False
    effective = apply_to_settings(settings, preferences)
    return any(getattr(effective, field, None) for field in KEY_FIELDS.values())


def apply_to_settings(settings: Any, preferences: UserPreferences) -> Any:
    """Return settings with the user's keys layered in.

    **The environment wins.** A key already set through the environment or
    ``.env`` is left alone, so a developer's configuration is never silently
    replaced by something typed into a browser months earlier.

    Returns:
        A copy of ``settings``. The original is unchanged.
    """
    updates: dict[str, str] = {}

    for name, value in preferences.api_keys.items():
        field_name = KEY_FIELDS.get(name)
        if field_name and value and not getattr(settings, field_name, None):
            updates[field_name] = value

    # The profile chosen on the Getting-ready screen, unless the environment
    # names one (D-157).
    profile_update: dict[str, Any] = {}
    if preferences.model_profile and "VOXFRAME_PROFILE" not in os.environ:
        from voxframe.config.settings import ModelProfile

        profile_update["profile"] = ModelProfile(preferences.model_profile)

    merged = {**updates, **profile_update}
    return settings.model_copy(update=merged) if merged else settings
