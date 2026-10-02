"""The owner's blind listening test for the music director (D-170).

Stage 1 ends here: the owner listens, blind, and decides. The plan's test:
one-minute clips from two talks, each in three versions -- today's looped bed,
the director's bed, and no music -- in a random order per clip, rated 0-100 on
three questions. Stage 1 passes if it beats today's bed on the first question
without losing on the other two.

    python scripts/music_listening_test.py prepare --talk a.mp3 --talk b.mp3 \\
        --track x.mp3 --track y.opus --out folder
    python scripts/music_listening_test.py score --out folder --ratings ratings.csv

``prepare`` writes the clips, a page to listen and rate on (offline, it saves a
CSV), and the answer key in a separate file. ``score`` reads the ratings and
the key.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import random
import statistics
import sys
from itertools import pairwise
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

QUESTIONS = (
    ("follows", "The music follows the speaker"),
    ("clear", "The speech is always clear"),
    ("edit", "I heard an edit in the music (0 = none, 100 = obvious)"),
)
VERSIONS = ("today", "director", "none")
CLIP_SECONDS = 60.0
FPS = 30.0
#: The director must not lose by more than this on speech clarity or edits.
TOLERANCE = 5.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--talk", type=Path, action="append", required=True)
    prepare.add_argument("--track", type=Path, action="append", required=True)
    prepare.add_argument("--out", type=Path, required=True)
    prepare.add_argument("--clips", type=int, default=6)
    prepare.add_argument("--language", default=None, help="Force a language code.")
    prepare.add_argument("--seed", type=int, default=None)
    score = commands.add_parser("score")
    score.add_argument("--out", type=Path, required=True)
    score.add_argument("--ratings", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.command == "prepare":
        return _prepare(arguments)
    return _score(arguments.out, arguments.ratings)


def _prepare(arguments: argparse.Namespace) -> int:
    from voxframe.config.settings import get_settings
    from voxframe.render.encode.probe import probe_capabilities
    from voxframe.transcribe.whisper import Transcriber

    settings = get_settings()
    caps = probe_capabilities()
    out: Path = arguments.out
    clips_dir = out / "clips"
    work = out / ".work"
    clips_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    chooser = random.Random(arguments.seed)

    per_talk = max(1, arguments.clips // len(arguments.talk))
    windows: list[tuple[Path, float, list[tuple[float, float]]]] = []
    for talk in arguments.talk:
        print(f"transcribing {talk.name} (cached after the first time)", flush=True)
        transcriber = Transcriber(
            settings.resolved_transcribe_model,
            cache_dir=settings.cache_path,
            use_gpu=settings.use_gpu,
            language=arguments.language,
            allowed_languages=settings.allowed_languages,
        )
        transcript = transcriber.transcribe(talk)
        words = [(w.start, w.end) for w in transcript.words]
        for start, chosen in _windows(words, per_talk):
            windows.append((talk, start, chosen))

    key: dict[str, dict[str, str]] = {}
    notes: dict[str, str] = {}
    for number, (talk, start, words) in enumerate(windows, 1):
        name = f"clip{number:02d}"
        track = arguments.track[(number - 1) % len(arguments.track)]
        print(f"{name}: {talk.name} from {start:.0f}s, music {track.name}", flush=True)
        versions, note = _render_clip(name, talk, start, words, track, caps, work)
        if note:
            notes[name] = note
        labels = ["1", "2", "3"]
        chooser.shuffle(labels)
        key[name] = {}
        for label, (version, path) in zip(labels, versions.items(), strict=True):
            target = clips_dir / f"{name}_{label}.m4a"
            path.replace(target)
            key[name][label] = version
        key[name]["_source"] = f"{talk.name} @ {start:.1f}s, music: {track.name}"

    (work / "key.json").write_text(json.dumps({"clips": key, "notes": notes}, indent=2), encoding="utf-8")
    (out / "listening_test.html").write_text(_page(sorted(key)), encoding="utf-8")
    (out / "README.txt").write_text(_readme(), encoding="utf-8")
    print(f"\nready: open {out / 'listening_test.html'}")
    print(f"the answer key is in {work / 'key.json'} -- do not open it until you have rated")
    for name, note in notes.items():
        print(f"note: {name}: {note}")
    return 0


def _windows(
    words: list[tuple[float, float]], count: int
) -> list[tuple[float, list[tuple[float, float]]]]:
    """Clips of about a minute, each with a long pause, spread through the talk.

    A clip starts where speech starts after a pause and ends at the end of a
    word, so no clip begins or ends mid-sentence.
    """
    from voxframe.music.director import SWELL_MIN_GAP

    starts = [words[0][0]] + [b[0] for a, b in pairwise(words) if b[0] - a[1] >= 0.6]
    candidates = []
    for start in starts:
        inside = [w for w in words if start <= w[0] and w[1] <= start + CLIP_SECONDS]
        if len(inside) < 20:
            continue
        gaps = [b[0] - a[1] for a, b in pairwise(inside)]
        if max(gaps, default=0) >= SWELL_MIN_GAP:
            candidates.append((start, inside))
    if not candidates:
        raise SystemExit("no minute of this talk has a pause long enough to test a swell")
    step = max(1, len(candidates) // count)
    return candidates[::step][:count]


def _render_clip(
    name: str,
    talk: Path,
    start: float,
    words: list[tuple[float, float]],
    track: Path,
    caps: object,
    work: Path,
) -> tuple[dict[str, Path], str]:
    from voxframe.music.director import NotDirectable, render_bed
    from voxframe.render.audio.music import MusicSettings, directed_mix_chain, music_filter_chain
    from voxframe.render.ffpath import run_ffmpeg

    ffmpeg = caps.ffmpeg_path  # type: ignore[attr-defined]
    local = [(a - start, b - start) for a, b in words]
    end = round((local[-1][1] + 3.0) * FPS) / FPS
    voice = work / f"{name}_voice.wav"
    run_ffmpeg(ffmpeg, ["-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{end:.3f}",
                        "-i", str(talk.resolve()), "-ac", "1", "-y", str(voice.resolve())])

    def encode(inputs: list[str], graph: str, target: Path) -> Path:
        run_ffmpeg(ffmpeg, ["-loglevel", "error", *inputs, "-filter_complex", graph,
                            "-map", "[aout]", "-t", f"{end:.3f}", "-c:a", "aac", "-b:a", "192k",
                            "-y", str(target.resolve())])
        return target

    narration = "[0:a]apad[narr];"
    settings = MusicSettings(path=track)
    from voxframe.render.compose.from_plan import _music_duration

    today = encode(
        ["-i", str(voice.resolve()), "-i", str(track.resolve())],
        narration + music_filter_chain(
            settings, end, _music_duration(track, caps), narration_label="narr", music_label="1:a"  # type: ignore[arg-type]
        ),
        work / f"{name}_today.m4a",
    )
    note = ""
    try:
        bed = render_bed(
            video_words=local, source_words=local, video_end=end, fps=FPS,
            narration=voice, track_file=track, gain=settings.gain, caps=caps,  # type: ignore[arg-type]
            cache_dir=work / "cache", source_end=end,
        )
        director = encode(
            ["-i", str(voice.resolve()), "-i", str(bed.path.resolve())],
            narration + directed_mix_chain(narration_label="narr", music_label="1:a"),
            work / f"{name}_director.m4a",
        )
    except NotDirectable as exc:
        note = f"the director fell back to today's bed: {exc}"
        director = work / f"{name}_director.m4a"
        director.write_bytes(today.read_bytes())
    none = encode(["-i", str(voice.resolve())], "[0:a]apad[aout]", work / f"{name}_none.m4a")
    return {"today": today, "director": director, "none": none}, note


def _page(clips: list[str]) -> str:
    rows = []
    for clip in clips:
        players = []
        for label in ("1", "2", "3"):
            sliders = "".join(
                f'<label>{html.escape(text)} <input type="range" min="0" max="100" value="50" '
                f'data-clip="{clip}" data-version="{label}" data-question="{q}" '
                f'oninput="this.nextElementSibling.textContent=this.value"><output>50</output></label>'
                for q, text in QUESTIONS
            )
            players.append(
                f'<div class="version"><h3>Version {label}</h3>'
                f'<audio controls preload="none" src="clips/{clip}_{label}.m4a"></audio>{sliders}</div>'
            )
        rows.append(f'<section><h2>{clip}</h2><div class="versions">{"".join(players)}</div></section>')
    return f"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Music listening test</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#1d1d1b; --muted:#6b6b66; --line:#dcd9d0; --accent:#2f5d8a; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16181b; --fg:#e8e6e1; --muted:#9a9890; --line:#34373c; --accent:#7fb0e0; }} }}
body {{ background:var(--bg); color:var(--fg); font:16px/1.5 system-ui, sans-serif; margin:0 auto; max-width:1100px; padding:24px 16px; }}
h1 {{ font-size:1.5rem; }} h2 {{ font-size:1.1rem; border-top:1px solid var(--line); padding-top:16px; }}
h3 {{ font-size:1rem; margin:0 0 8px; }} p {{ color:var(--muted); }}
.versions {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:16px; }}
.version {{ border:1px solid var(--line); border-radius:8px; padding:12px; }}
audio {{ width:100%; margin-bottom:8px; }} label {{ display:block; font-size:.9rem; margin:6px 0; }}
input[type=range] {{ width:75%; vertical-align:middle; accent-color:var(--accent); }} output {{ margin-left:6px; }}
button {{ font:inherit; padding:10px 18px; border-radius:8px; border:1px solid var(--accent); background:var(--accent); color:#fff; cursor:pointer; }}
</style>
<h1>Music listening test</h1>
<p>Each clip comes in three versions, in a different random order every time.
Listen to all three, then rate each. Headphones help. When you are done, save
your ratings and send the file; do not open the answer key until then.</p>
{"".join(rows)}
<p><button onclick="save()">Save my ratings</button></p>
<script>
function save() {{
  const rows = [["clip","version","question","rating"]];
  document.querySelectorAll("input[type=range]").forEach(s =>
    rows.push([s.dataset.clip, s.dataset.version, s.dataset.question, s.value]));
  const blob = new Blob([rows.map(r => r.join(",")).join("\\n") + "\\n"], {{type: "text/csv"}});
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob); link.download = "ratings.csv"; link.click();
}}
</script>
"""


def _readme() -> str:
    return (
        "Music director, Stage 1: blind listening test\n\n"
        "1. Open listening_test.html in your browser.\n"
        "2. For every clip, listen to versions 1, 2 and 3 and rate each on the three questions.\n"
        "3. Click 'Save my ratings'. It downloads ratings.csv.\n"
        "4. Score it:\n"
        "   python scripts/music_listening_test.py score --out <this folder> --ratings ratings.csv\n\n"
        "The versions are today's looped bed, the director's bed, and no music, shuffled per clip.\n"
        "The answer key is in .work/key.json. Please do not open it before rating.\n"
    )


def _score(out: Path, ratings: Path) -> int:
    key = json.loads((out / ".work" / "key.json").read_text(encoding="utf-8"))["clips"]
    scores: dict[tuple[str, str], list[float]] = {}
    with ratings.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            version = key[row["clip"]][row["version"]]
            scores.setdefault((version, row["question"]), []).append(float(row["rating"]))

    def mean(version: str, question: str) -> float:
        values = scores.get((version, question), [])
        return statistics.mean(values) if values else float("nan")

    print(f"{'':<34}" + "".join(f"{v:>10}" for v in VERSIONS))
    for question, text in QUESTIONS:
        print(f"{text[:33]:<34}" + "".join(f"{mean(v, question):>10.1f}" for v in VERSIONS))
    follows = mean("director", "follows") > mean("today", "follows")
    clear = mean("director", "clear") >= mean("today", "clear") - TOLERANCE
    edits = mean("director", "edit") <= mean("today", "edit") + TOLERANCE
    print()
    print(f"follows the speaker better than today: {'yes' if follows else 'no'}")
    print(f"speech at least as clear (within {TOLERANCE:.0f}): {'yes' if clear else 'no'}")
    print(f"edits no more audible (within {TOLERANCE:.0f}): {'yes' if edits else 'no'}")
    print(f"\nStage 1 {'PASSES' if follows and clear and edits else 'does not pass'} on these ratings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
