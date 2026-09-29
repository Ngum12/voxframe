"""Sourcing: fetch permissively-licensed media so scenes are not left empty.

The functional goal of Phase 4 is that a typical narration produces a video
with no empty gradient scenes (D-069). Adapters search open sources, the
fetcher downloads with per-asset provenance, and the registry drives both from
a scene plan.

No adapter requires an API key. Openverse works anonymously and is the default,
so the core pipeline runs with no account anywhere.
"""

from voxframe.sourcing.base import (
    Adapter,
    AdapterError,
    Candidate,
    SearchRequest,
)
from voxframe.sourcing.fetcher import (
    PROVENANCE_SUFFIX,
    FetchResult,
    download_candidates,
    read_provenance,
    search_adapters,
)
from voxframe.sourcing.licenses import (
    DEFAULT_POLICY,
    PERMISSIVE_POLICY,
    LicensePolicy,
    LicenseTerms,
    Obligation,
    parse_license,
)
from voxframe.sourcing.openverse import OpenverseAdapter
from voxframe.sourcing.registry import (
    SourcingPlan,
    build_adapters,
    source_for_plan,
)

__all__ = [
    "DEFAULT_POLICY",
    "PERMISSIVE_POLICY",
    "PROVENANCE_SUFFIX",
    "Adapter",
    "AdapterError",
    "Candidate",
    "FetchResult",
    "LicensePolicy",
    "LicenseTerms",
    "Obligation",
    "OpenverseAdapter",
    "SearchRequest",
    "SourcingPlan",
    "build_adapters",
    "download_candidates",
    "parse_license",
    "read_provenance",
    "search_adapters",
    "source_for_plan",
]
