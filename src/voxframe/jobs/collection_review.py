"""Read-only finishing cues and output-bound acknowledgements for collections."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from voxframe.api.plan_history import PlanHistory
from voxframe.plan.finish_review import review
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import revision


@dataclass(frozen=True)
class ReviewClip:
    job_id: str
    name: str
    plan: ScenePlan
    plan_path: Path
    files: tuple[tuple[str, Path], ...]
    height: int
    summary: dict


def report(clips: list[ReviewClip]) -> dict:
    rows, stamps = [], []
    for clip in clips:
        cues = review(clip.plan, clip.height)
        issues = [{**cue, "id": f"{clip.job_id}:{cue['id']}"} for cue in cues["issues"]]
        blockers = []

        def add(
            code: str,
            category: str,
            title: str,
            detail: str,
            action: str,
            *,
            target: list = issues,
            job_id: str = clip.job_id,
        ) -> None:
            target.append(
                {
                    "id": f"{job_id}:{code}",
                    "category": category,
                    "title": title,
                    "detail": detail,
                    "action": action,
                    "scene": None,
                    "at": 0,
                }
            )

        pending = (
            int(clip.summary.get("pending_edits", 0)) or PlanHistory(clip.plan_path).state().pending
        )
        if pending:
            blockers.append(
                "Saved changes need Update video before this finished file can be reviewed."
            )
        rendered = clip.summary.get("rendered_revision")
        history = PlanHistory(clip.plan_path).rendered_version()
        if rendered and rendered != revision(clip.plan):
            blockers.append(
                "The saved edit differs from the rendered video. Use Update video first."
            )
        elif not rendered and history and ScenePlan.load(history) != clip.plan:
            blockers.append(
                "The saved edit differs from the rendered video. Use Update video first."
            )
        file_map = dict(clip.files)
        video = file_map.get("video")
        if not video:
            blockers.append("The finished video is missing. Update this clip first.")
        for kind, path in (("plan", clip.plan_path), *clip.files):
            if not path.is_file():
                blockers.append(f"The saved {kind} file is missing. Update this clip first.")
                stamps.append([clip.job_id, kind, str(path.resolve()), None])
            else:
                info = path.stat()
                stamps.append(
                    [clip.job_id, kind, str(path.resolve()), info.st_size, info.st_mtime_ns]
                )
        if (
            not rendered
            and not history
            and video
            and video.is_file()
            and clip.plan_path.stat().st_mtime_ns > video.stat().st_mtime_ns
        ):
            blockers.append("The saved edit is newer than its video. Use Update video first.")
        sound = clip.summary.get("sound")
        measured = None
        if isinstance(sound, dict):
            measured = {key: sound.get(key) for key in ("integrated_lufs", "true_peak")}
            for key, value in measured.items():
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    measured[key] = None
            problems = sound.get("problems")
            if isinstance(problems, list):
                for index, problem in enumerate(problems):
                    if isinstance(problem, str):
                        add(
                            f"mix-{index}",
                            "sound",
                            "Rendered sound needs a listen",
                            problem,
                            "sound",
                        )
            if not isinstance(problems, list) or (sound.get("passed") is False and not problems):
                add(
                    "mix-check",
                    "sound",
                    "Rendered sound check needs attention",
                    "The recorded sound check did not report a clear pass. "
                    "Listen and inspect Sound.",
                    "sound",
                )
        if measured is None or any(value is None for value in measured.values()):
            add(
                "sound-unmeasured",
                "sound",
                "Finished sound measurements are unavailable",
                "Listen to the finished video. Update video to record fresh sound measurements.",
                "sound",
            )
        if not {"srt", "vtt"}.intersection(file_map):
            add(
                "subtitles-absent",
                "captions",
                "No subtitle companion is recorded",
                "The ZIP will include the video without SRT/VTT companions. "
                "Check whether that is intentional.",
                "export",
            )
        rows.append(
            {
                "job_id": clip.job_id,
                "name": clip.name,
                "revision": cues["revision"],
                "width": cues["width"],
                "height": cues["height"],
                "seconds": cues["seconds"],
                "issues": issues,
                "blockers": list(dict.fromkeys(blockers)),
                "sound": measured,
            }
        )
    public = {
        "version": 1,
        "clips": rows,
        "counts": {
            category: sum(cue["category"] == category for row in rows for cue in row["issues"])
            for category in ("captions", "framing", "timing", "sound")
        },
        "note": "Cues use saved timings, framing metadata and recorded render sound checks. "
        "They do not inspect video pixels or meaning. Watch and listen to every finished clip.",
    }
    fingerprint = hashlib.sha256(
        json.dumps({"report": public, "files": stamps}, sort_keys=True).encode()
    ).hexdigest()[:24]
    return {**public, "fingerprint": fingerprint}


def validate(report: dict, checked: list[str], watched: list[str]) -> None:
    if any(row["blockers"] for row in report["clips"]):
        raise ValueError("Update the blocked clips and refresh the collection review first.")
    issues = {cue["id"] for row in report["clips"] for cue in row["issues"]}
    clips = {row["job_id"] for row in report["clips"]}
    if len(set(checked)) != len(checked) or set(checked) != issues:
        raise ValueError(
            "Review and acknowledge every current cue before approving this collection."
        )
    if len(set(watched)) != len(watched) or set(watched) != clips:
        raise ValueError("Confirm watching and listening to every selected finished clip.")


def approve(
    directory: Path, current: dict, fingerprint: str, checked: list[str], watched: list[str]
) -> dict:
    if current["fingerprint"] != fingerprint:
        raise ValueError("The selected clips changed. Refresh and review the collection again.")
    validate(current, checked, watched)
    receipt = {
        "report": current,
        "checked": checked,
        "watched": watched,
        "approved_at": datetime.now(UTC).isoformat(),
    }
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"{uuid4().hex}.partial"
    try:
        temporary.write_text(json.dumps(receipt), encoding="utf-8")
        temporary.replace(directory / f"{fingerprint}.json")
    finally:
        temporary.unlink(missing_ok=True)
    return receipt


def approved(directory: Path, key: str, current: dict) -> dict:
    if re.fullmatch(r"[a-f0-9]{24}", key) is None or current["fingerprint"] != key:
        raise ValueError("This review expired. Refresh and review the collection again.")
    receipt = json.loads((directory / f"{key}.json").read_text(encoding="utf-8"))
    if receipt["report"] != current or not isinstance(receipt["approved_at"], str):
        raise ValueError("This collection review changed. Review it again.")
    validate(current, receipt["checked"], receipt["watched"])
    return {
        "method": "saved-metadata-and-recorded-render-sound",
        "review_id": key,
        "approved_at": receipt["approved_at"],
        "watched_clips": receipt["watched"],
        "acknowledged_cues": [cue for row in current["clips"] for cue in row["issues"]],
        "note": current["note"],
    }
