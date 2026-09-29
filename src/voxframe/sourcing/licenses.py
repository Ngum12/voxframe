"""Which licenses Voxframe will source, and what each obliges the user to do.

This is the part of sourcing that matters most. Fetching an image is trivial;
fetching one whose terms the user then unknowingly breaks is a real harm, and
the whole point of the project is that a free tool need not be a careless one.

Three obligations are tracked separately, because they constrain different
things:

**Attribution.** Most CC licenses require crediting the author. Voxframe always
writes a credits file, so this is satisfied by default — but the user has to
actually ship it, and the credits file says so.

**Commercial use.** ``NC`` licenses forbid it. A user making a video for a
client needs to know before publishing, not after.

**ShareAlike.** ``SA`` licenses require derivative works to carry the same
license. A video incorporating a CC BY-SA image arguably has to be released
under CC BY-SA. That is a decision about the user's own work, not a technical
detail, so **ShareAlike is excluded by default** (D-071) rather than silently
imposing a license on whatever they are making.

The default policy sources only public-domain and permissive-attribution
material. Everything else is opt-in and reported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "DEFAULT_POLICY",
    "PERMISSIVE_POLICY",
    "LicensePolicy",
    "LicenseTerms",
    "Obligation",
    "parse_license",
]


class Obligation(StrEnum):
    """What a license requires of the user."""

    #: The author must be credited.
    ATTRIBUTION = "attribution"

    #: Derivative works must carry the same license.
    SHARE_ALIKE = "share_alike"

    #: Commercial use is forbidden.
    NON_COMMERCIAL = "non_commercial"

    #: Modification is forbidden. Fatal for Voxframe: Ken Burns crops and
    #: scales every image, which is a modification.
    NO_DERIVATIVES = "no_derivatives"


@dataclass(frozen=True, slots=True)
class LicenseTerms:
    """A license identifier and what it obliges.

    Attributes:
        code: Canonical identifier, e.g. ``CC0-1.0``, ``CC-BY-4.0``.
        obligations: What using this asset requires.
        url: The license deed, recorded so credits can link to the terms
            rather than just naming them.
    """

    code: str
    obligations: frozenset[Obligation]
    url: str = ""

    @property
    def requires_attribution(self) -> bool:
        return Obligation.ATTRIBUTION in self.obligations

    @property
    def allows_commercial(self) -> bool:
        return Obligation.NON_COMMERCIAL not in self.obligations

    @property
    def allows_modification(self) -> bool:
        """Whether the asset may be cropped, scaled or animated.

        Load-bearing: Ken Burns modifies every image it touches, so an
        ND-licensed asset cannot be used at all, not merely used carefully.
        """
        return Obligation.NO_DERIVATIVES not in self.obligations

    @property
    def is_share_alike(self) -> bool:
        return Obligation.SHARE_ALIKE in self.obligations

    @property
    def is_public_domain(self) -> bool:
        return not self.obligations

    def summary(self) -> str:
        """One line describing what the user must do, for the credits file."""
        if self.is_public_domain:
            return "public domain — no obligations"

        parts: list[str] = []
        if self.requires_attribution:
            parts.append("credit the author")
        if self.is_share_alike:
            parts.append("your video must carry the same license")
        if not self.allows_commercial:
            parts.append("NO COMMERCIAL USE")
        if not self.allows_modification:
            parts.append("NO MODIFICATION (unusable: Ken Burns crops)")
        return "; ".join(parts)


#: Licenses Voxframe understands, by Openverse/Creative Commons short code.
#:
#: Keyed on the short code because that is what the APIs return. Versions are
#: appended by :func:`parse_license` rather than enumerated here, since the
#: obligations do not differ between versions in ways that affect us.
_KNOWN: dict[str, frozenset[Obligation]] = {
    "cc0": frozenset(),
    "pdm": frozenset(),
    "publicdomain": frozenset(),
    "by": frozenset({Obligation.ATTRIBUTION}),
    "by-sa": frozenset({Obligation.ATTRIBUTION, Obligation.SHARE_ALIKE}),
    "by-nc": frozenset({Obligation.ATTRIBUTION, Obligation.NON_COMMERCIAL}),
    "by-nd": frozenset({Obligation.ATTRIBUTION, Obligation.NO_DERIVATIVES}),
    "by-nc-sa": frozenset(
        {Obligation.ATTRIBUTION, Obligation.NON_COMMERCIAL, Obligation.SHARE_ALIKE}
    ),
    "by-nc-nd": frozenset(
        {
            Obligation.ATTRIBUTION,
            Obligation.NON_COMMERCIAL,
            Obligation.NO_DERIVATIVES,
        }
    ),
    # Source-specific licenses that behave like CC0 with a courtesy credit.
    "pexels": frozenset({Obligation.ATTRIBUTION}),
    "pixabay": frozenset({Obligation.ATTRIBUTION}),
    "unsplash": frozenset({Obligation.ATTRIBUTION}),
}

_CC_URL = "https://creativecommons.org/licenses/{code}/{version}/"
_CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
_PDM_URL = "https://creativecommons.org/publicdomain/mark/1.0/"


def parse_license(code: str, version: str = "") -> LicenseTerms | None:
    """Interpret a license code from a source API.

    Args:
        code: Short code such as ``cc0``, ``by-sa``, or a fuller identifier
            such as ``CC-BY-4.0``.
        version: Version string, when the API reports it separately.

    Returns:
        The terms, or ``None`` if the code is not recognised.

    Note:
        Returning ``None`` rather than guessing is deliberate. An unrecognised
        license is dropped by the fetcher; assuming it is permissive would
        put the user in breach of terms nobody checked.
    """
    normalised = code.strip().lower()
    if not normalised:
        return None

    # Accept "CC-BY-SA-4.0" as well as the bare "by-sa" the APIs return.
    normalised = re.sub(r"^cc[-_]", "", normalised)
    if match := re.match(r"^([a-z0-9-]*?)[-_]?(\d+\.\d+)$", normalised):
        normalised, version = match.group(1), version or match.group(2)

    obligations = _KNOWN.get(normalised)
    if obligations is None:
        return None

    return LicenseTerms(
        code=_canonical_code(normalised, version),
        obligations=obligations,
        url=_deed_url(normalised, version),
    )


def _canonical_code(code: str, version: str) -> str:
    """A stable, human-readable identifier for the credits file."""
    if code in {"pexels", "pixabay", "unsplash"}:
        return code.capitalize()
    if code == "cc0":
        return f"CC0-{version or '1.0'}"
    if code in {"pdm", "publicdomain"}:
        return "Public Domain Mark 1.0"
    return f"CC-{code.upper()}-{version or '4.0'}"


def _deed_url(code: str, version: str) -> str:
    """Link to the license deed, so credits point at the actual terms."""
    if code == "cc0":
        return _CC0_URL
    if code in {"pdm", "publicdomain"}:
        return _PDM_URL
    if code in {"pexels", "pixabay", "unsplash"}:
        return ""
    return _CC_URL.format(code=code, version=version or "4.0")


@dataclass(frozen=True, slots=True)
class LicensePolicy:
    """Which licenses may be sourced.

    Attributes:
        allow_attribution: Accept licenses requiring a credit. Voxframe always
            writes credits, so this is on by default.
        allow_share_alike: Accept ShareAlike. **Off by default**: it would
            impose a license on the user's own video (D-071).
        allow_non_commercial: Accept NC. Off by default, so a user cannot
            unknowingly build something they may not sell.
    """

    allow_attribution: bool = True
    allow_share_alike: bool = False
    allow_non_commercial: bool = False

    def permits(self, terms: LicenseTerms) -> bool:
        """Whether an asset under these terms may be sourced."""
        # No exception, at any setting: Ken Burns crops and scales every image,
        # so an ND asset cannot be used at all.
        if not terms.allows_modification:
            return False

        if terms.requires_attribution and not self.allow_attribution:
            return False
        if terms.is_share_alike and not self.allow_share_alike:
            return False
        if not terms.allows_commercial and not self.allow_non_commercial:
            return False
        return True

    def rejection_reason(self, terms: LicenseTerms) -> str:
        """Why an asset was rejected, for logging and the sources listing."""
        if not terms.allows_modification:
            return (
                f"{terms.code} forbids modification, and Ken Burns crops and "
                f"scales every image"
            )
        if terms.requires_attribution and not self.allow_attribution:
            return f"{terms.code} requires attribution, which this policy excludes"
        if terms.is_share_alike and not self.allow_share_alike:
            return (
                f"{terms.code} is ShareAlike: using it would require your video "
                f"to carry the same license"
            )
        if not terms.allows_commercial and not self.allow_non_commercial:
            return f"{terms.code} forbids commercial use"
        return ""

    def openverse_codes(self) -> tuple[str, ...]:
        """License codes to request from Openverse.

        Filtering at the API rather than after the fact: asking for licenses
        we will reject wastes the request and buries the usable results.
        """
        codes = ["cc0", "pdm"]
        if self.allow_attribution:
            codes.append("by")
            if self.allow_share_alike:
                codes.append("by-sa")
            if self.allow_non_commercial:
                codes.append("by-nc")
                if self.allow_share_alike:
                    codes.append("by-nc-sa")
        return tuple(codes)


#: Public domain and permissive attribution. Safe for commercial use, and
#: imposes nothing on the user's own work.
DEFAULT_POLICY = LicensePolicy()

#: Public domain only. For a user who would rather not maintain credits at all.
PERMISSIVE_POLICY = LicensePolicy(allow_attribution=False)
