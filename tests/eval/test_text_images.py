"""The printed-text check, measured on real images (D-133).

Re-runs the measurement behind the thresholds, so a change to the prompts, the
scale or the model cannot quietly break it. It fails if:

- the three images that prompted the check -- "100%", "ACHIEVE", "EARTH" -- are
  no longer flagged, or
- any ordinary photograph is flagged (the measured rate is 0 of 88), or
- fewer text images are caught than were at measurement.

The labelled images live in gitignored Phase 4 libraries, so this skips on a
machine without them rather than failing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.needs_models]

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

#: The images that prompted the check, by their row in the labels file.
NAMED = {"100%": 10, "EARTH": 18, "ACHIEVE": 30}

#: Text images caught at measurement, per model. A drop is a regression.
MEASURED_RECALL = {"default": 12, "lite": 9}


@pytest.fixture(scope="module", params=["default", "lite"])
def measured(request: pytest.FixtureRequest):
    eval_text_images = pytest.importorskip("eval_text_images")
    if not eval_text_images.LIBRARIES.is_dir():
        pytest.skip("the labelled Phase 4 libraries are not on this machine")
    pytest.importorskip("open_clip", reason="library extra not installed")

    labels = eval_text_images.load_labels()
    try:
        scores = eval_text_images.text_scores(request.param)
    except Exception as exc:
        pytest.skip(f"could not score images: {str(exc)[:80]}")
    if len(scores) < len(labels) * 0.9:
        pytest.skip("too few labelled images are present to measure")
    return request.param, labels, scores


def test_the_named_images_are_flagged(measured) -> None:
    from voxframe.match.text_detection import TEXT_THRESHOLDS

    model, labels, scores = measured
    threshold = TEXT_THRESHOLDS[model]
    text_ids = [row[0] for row in labels if row[3] == "text"]

    # The CSV's row order follows the contact sheets the labels were made from.
    ordered = [row[0] for row in labels]
    for name, position in NAMED.items():
        asset_id = ordered[position]
        assert asset_id in text_ids, f"{name} is not labelled as text"
        assert scores[asset_id] >= threshold, (
            f"{model}: {name} scored {scores[asset_id]:.3f}, below {threshold}"
        )


def test_no_ordinary_photograph_is_flagged(measured) -> None:
    from voxframe.match.text_detection import TEXT_THRESHOLDS

    model, labels, scores = measured
    threshold = TEXT_THRESHOLDS[model]
    flagged = [
        row[2] for row in labels
        if row[3] == "none" and row[0] in scores and scores[row[0]] >= threshold
    ]

    assert not flagged, f"{model}: ordinary photographs flagged: {flagged}"


def test_recall_has_not_dropped(measured) -> None:
    from voxframe.match.text_detection import TEXT_THRESHOLDS

    model, labels, scores = measured
    threshold = TEXT_THRESHOLDS[model]
    caught = sum(
        1 for row in labels
        if row[3] == "text" and row[0] in scores and scores[row[0]] >= threshold
    )

    assert caught >= MEASURED_RECALL[model]
