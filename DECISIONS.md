# Decisions

Important choices and the reasoning behind them. Each entry records what was
decided, why, and what it rules out.

**Status key:** `APPROVED` — signed off by project owner. `AMENDED` — approved
with modifications, recorded inline. `CORRECTED` — an earlier entry was wrong
and has been fixed; the error is left visible rather than silently rewritten.

---

## Phase 0 — Planning

### D-001 · Project name: Voxframe — APPROVED
Kept the working name. Pronounceable, descriptive (voice -> frames), and
unclaimed on PyPI as far as a search shows. PyPI and GitHub availability to be
confirmed before first release.

### D-002 · Python 3.13 as the target — APPROVED
Decided by project owner; matches the shell default on the development machine.

Initial concern was that ML wheels lag new Python releases. Verified by
downloading the actual wheels rather than assuming:

- `open_clip_torch` 3.3.0 — `py3-none-any`, pure Python
- `sqlite-vec` 0.1.9 — `py3-none-win_amd64`, no C-extension ABI tie
- `opencv-python-headless` 5.0.0.93 — `cp37-abi3`, stable ABI covers 3.13
- `pysbd` 0.3.4 — `py3-none-any`

All resolve on 3.13. Concern withdrawn. Declared support 3.11–3.13 so
contributors on older interpreters are not excluded.

Residual risk: CUDA torch wheels for 3.13 may lag. Affects only the optional GPU
path (D-004), so it blocks nothing.

### D-003 · License: Apache 2.0, FFmpeg invoked as external binary — APPROVED, reasoning CORRECTED

**The decision stands: Apache 2.0.** The justification originally given was
wrong and is corrected here.

**What was wrong.** The first draft justified Apache 2.0 over MIT by citing "the
explicit patent grant, which matters for a codec-adjacent project." That
conflates two unrelated things and is incorrect.

**Correct position.** Apache 2.0's patent grant covers only patents held by
*contributors to this project*, granted to *users of this project*. It protects
users from a contributor later asserting their own patents. It provides no
protection against third-party codec patents. H.264/H.265 are covered by patent
pools (MPEG LA / Via LA, Access Advance) whose licensing is wholly independent
of Voxframe's license and FFmpeg's. No open-source license can grant rights the
licensor does not hold.

**Why Apache 2.0 is still the right choice:** the contributor patent grant, the
explicit trademark clause, and the NOTICE mechanism — all relevant to a project
expecting outside contributors. Not codec patents, which it does not address.

**FFmpeg relationship unchanged.** Voxframe never bundles, links against, or
statically incorporates FFmpeg; it invokes the installed binary as a subprocess.
This arm's-length invocation does not trigger GPL copyleft on Voxframe's source.
The Docker image bundles FFmpeg and *is* a GPL composite distribution —
documented with a source offer. The PyPI package carries no GPL obligation.

Codec patent position is documented in plain language in the README Licensing
section, labelled informational and not legal advice. See also D-017.

Rules out: vendoring FFmpeg into the Python package; PyAV or anything that
statically embeds GPL FFmpeg.

### D-004 · GPU: auto-detect, never require, dev environment untouched — APPROVED
The dev machine has an NVIDIA T550 (4 GB VRAM) but a CPU-only torch build
(`2.7.1+cpu`), so the GPU is currently unused. The engine detects CUDA at
runtime and falls back to CPU cleanly. CPU is the tested default.
`docs/install.md` documents the CUDA reinstall; the dev environment is not
changed without explicit instruction.

4 GB VRAM requires careful batching for depth and CLIP even when a GPU is
present, so that work is needed regardless.

### D-005 · Review format: chat summaries plus versioned docs — APPROVED
Plan and phase reports live as markdown in the repo and are summarised in chat.
Extended by D-019 (session continuity).

### D-006 · Style template *schema* moves to Phase 2 — APPROVED
Captions are the first visible output and land in Phase 2. If the template
abstraction arrives only at Phase 5, every caption, font and pacing constant
from Phases 2–4 needs refactoring into it. Schema moves to Phase 2 (~1 hour);
the templates themselves are still authored in Phase 5.

### D-007 · Depth model: Depth-Anything-V2 Small only — APPROVED, AMENDED with quality gate

**Licensing basis.** Base and Large checkpoints are **CC-BY-NC**
(non-commercial), failing the requirement that all dependencies permit free
commercial use. Only **Small** is Apache-2.0. Small is the default and the only
auto-downloaded checkpoint; non-commercial variants are never fetched
automatically.

**Amendment (project owner): parallax must appear only where it looks good.**
Small produces coarser depth maps, raising the difficulty of the artifact-free
bar. Four mechanisms, rather than hoping the model is good enough:

1. **Displacement cap** — maximum parallax offset capped as a fraction of image
   width, keeping disocclusions small enough to inpaint invisibly. A
   style-template parameter with a conservative default.
2. **Depth quality scoring** — every image's depth map scored on
   foreground/background separation (depth histogram bimodality), edge agreement
   with the RGB image, and confidence. Produces a `DepthQuality` record.
3. **Automatic fallback** — below threshold, the scene silently uses Ken Burns.
4. **Recorded and overridable** — the plan stores `motion_reason` (e.g.
   `parallax_rejected: depth_separation 0.21 < 0.45`) so the user sees why and
   can force parallax by editing the plan. The plan is authoritative.

Enforced by `test_depth_quality_fallback` (Phase 5).

### D-008 · CLIP weights: LAION, not OpenAI — APPROVED
OpenAI CLIP weights carry usage terms we do not want to propagate downstream.
LAION-trained ViT-B-32 (`laion2b`) is MIT and performs comparably for semantic
image search.

### D-009 · Build FFmpeg graphs directly; no MoviePy — APPROVED
MoviePy shells to FFmpeg anyway while adding a frame-level Python abstraction
that holds clips in memory, conflicting with the 60-minute audio requirement.
Cost: more FFmpeg expertise in `render/compose`. Benefit: flat memory profile
and exact filter-graph control.

### D-010 · Remotion excluded — APPROVED
License restricts use by companies above a size threshold, failing the
redistribution requirement. Not used anywhere, including the Phase 8 web app.

### D-011 · Per-scene render, concat, audio laid in once — APPROVED IN PRINCIPLE, corrected by D-013

Each scene renders to an independent segment; segments are concatenated; the
original audio is laid in once at the end, never sliced, never re-encoded per
segment. Every scene becomes independently cacheable, which is what makes
Phase 6 resume work.

**Correction from project owner:** the original claim that this makes drift
"structurally impossible" was premature. Per-scene rendering alone does not
prevent drift — rounding each scene's duration independently accumulates error
across hundreds of scenes. The claim is only valid with an explicit global frame
grid, specified in D-013, and is not asserted until
`test_frame_grid_no_drift` passes.

### D-012 · Provenance mandatory at ingest — APPROVED
An asset cannot enter the library without `source`, `author`, `license` and
`source_url`. Makes the credits file a pure projection of the library rather
than separate bookkeeping that can drift from what a render used.

---

## Phase 0 — Corrections round (project owner review)

### D-013 · Global frame grid — NEW, required before any no-drift claim

All time-to-frame conversion goes through one authority, `timeline/`:

```
start_frame = round(t * fps)
duration_frames = next.start_frame - this.start_frame
```

- Scene boundaries stored in the plan as **frames**, not floats; seconds derived
  for display only.
- A segment's duration is defined by the *next* scene's start frame, never its
  own rounded duration. Each boundary is absolute, so rounding error cannot
  accumulate.
- Final scene ends at `round(audio_duration * fps)`, pinning video length to the
  audio rather than to the sum of the parts.
- No scene shorter than 1 frame; the segmenter merges anything smaller.

**Enforcing test `test_frame_grid_no_drift`:** on a 30+ minute sample, total
frames == `round(audio_duration * fps)` within one frame, and segment durations
sum exactly. Until it passes, no drift claim is made.

### D-014 · Transitions via dedicated transition segments — NEW

Crossfades need frames from two scenes at once, which independent segments do
not provide. For a transition of `D` frames between A and B:

1. A renders full length, trimmed to end `D` frames early.
2. B renders full length, trimmed to start `D` frames late.
3. A separate transition segment renders A's last `D` frames against B's first
   `D` frames via `xfade` (confirmed present in the target FFmpeg build).
4. Concat: `A_trimmed, AB_transition, B_trimmed, ...`

Consequences handled explicitly: transition frames come from the scenes' own
allocations so **total frame count is unchanged** (D-013 preserved); transition
length is clamped so no scene falls below minimum visible duration, with
clamping recorded in the plan; motion continues through the transition at the
correct phase so a crossfade does not freeze; a hard cut is `D = 0`; transitions
align to speech pauses and never begin mid-word.

Enforced by `test_transition_frame_accounting` (Phase 5). Specified now,
implemented Phase 5, per the instruction to settle it before Phase 3.

### D-015 · zoompan smoothness is a Phase 3 gate, not an assumption — NEW

`zoompan` computes pan offsets with integer pixel rounding, producing visible
stepping on slow zooms. Treated as an open risk with an explicit gate rather
than a solved problem.

**Gate `test_zoom_smoothness` (Phase 3):** render a slow zoom (1.0 -> 1.08 over
8 s), extract consecutive frames, measure per-frame centroid delta of a
registered feature, assert monotonic sub-pixel progression with no repeated
offsets. Visual inspection as well.

Fallback order if the gate fails at acceptable speed, with the reason recorded
here: (1) oversample — render at 2x, animate on the larger grid, downscale, so
sub-pixel motion becomes integer motion; (2) `scale` + `crop` with
frame-indexed expressions; (3) numpy compositing with Lanczos resampling
(slowest, but the code path already exists for parallax).

### D-016 · Intermediate format: libx264 CRF 16 yuv444p, FFV1 opt-in — NEW

Measured on this machine, 3 s of 1080p30 synthetic `testsrc2`:

| Codec | Size | ~10 min | Encode |
|---|---|---|---|
| FFV1 lossless | 9.6 MB | ~19 GB | 536 ms |
| libx264rgb lossless | 26.6 MB | ~53 GB | 559 ms |
| **libx264 CRF 16** | **3.9 MB** | **~7.6 GB** | 718 ms |
| libx264 CRF 18 | 3.7 MB | ~7.2 GB | 1683 ms |
| UT Video lossless | 43.8 MB | ~87 GB | 1275 ms |

Synthetic content compresses differently from photographic content, so these are
ratios not absolutes.

**Default `libx264 -crf 16 -preset veryfast`, yuv444p.** One subsequent encode
(the caption burn) will not expose CRF 16 artifacts, and true lossless costs
~2.5x the disk. `--intermediate=ffv1` available for a mathematically lossless
path. yuv444p because caption edges and fine text suffer under chroma
subsampling; conversion to yuv420p happens once at final output.

**Enforcing test `test_intermediate_generation_loss`:** two-pass path vs
single-pass reference, VMAF >= 95. If CRF 16 fails, escalate to FFV1 by default
and record the change here.

### D-017 · Encoder fallback chain and royalty-free output — NEW

Encoders are **probed at runtime, never assumed.** Verified on this machine:
`libx264`, `libx265`, `libvpx-vp9`, `libaom-av1`, `h264_nvenc`, `h264_qsv`,
`h264_amf` present; **`libopenh264` and `libsvtav1` absent** — precisely why the
chain probes rather than assumes.

Chain, each step logged clearly:
`h264_nvenc -> h264_qsv -> libx264 -> libopenh264 -> libvpx-vp9 -> libsvtav1 -> libaom-av1`

Draft presets prefer hardware; final-quality presets default to libx264 CRF
because NVENC at 4 GB VRAM gives up meaningful quality per bit.

**Royalty-free output is first-class, not a fallback:** `--codec vp9` (WebM) and
`--codec av1` are explicit options for users avoiding H.264/H.265 patent-pool
exposure.

Ships `THIRD_PARTY_LICENSES` (every runtime dependency and model checkpoint with
license text, generated and verified in CI) and a plain-language README
**Licensing** section covering the Apache 2.0 terms, the FFmpeg/GPL
relationship, what the patent grant does and does not cover (D-003), codec
patent basics, and the royalty-free options.

### D-018 · FFmpeg filter-path escaping is centralised — NEW, verified experimentally

Tested before specifying, because the failure mode is actively misleading:

| Form | Result |
|---|---|
| `ass=C:\path\to\sub.ass` | **FAILS** — colon parsed as option separator, backslashes stripped |
| `ass=C\:/path/to/sub.ass` | **FAILS** — escaping the colon alone is not sufficient |
| `ass=filename='C\:/path/to/sub.ass'` | **WORKS** |

All three elements are required: single-quoted `filename=`, escaped drive colon,
forward slashes. The naive form reports a bogus
`Unable to parse "original_size" option value` error pointing nowhere near the
real cause — a contributor would lose hours to it.

Therefore **all** FFmpeg filter-argument path construction goes through
`render/ffpath/`. No other module builds filter paths by hand. Unit tests
(`test_ass_path_escaping`) cover drive letters, spaces, apostrophes and
non-ASCII characters on all three platforms.

### D-019 · Session continuity artifacts — NEW

Each session assumes no memory of previous ones.

- `docs/PROGRESS.md` — updated at the end of every session and phase: done, in
  progress, known issues, exact next step.
- `docs/phases/phase-N.md` — demo commands, test results, render-time
  measurements relative to audio length, honest limitations.
- `docs/phases/phase-N-contacts.png` — frame contact sheet for every visual demo.
- `demo_output/` gitignored; no rendered video or large media ever committed.
  `samples/` inputs stay small.
- Each phase ends in chat with a 5-line summary, the demo command, and any
  decision needed from the owner.

### D-020 · Dependency audit extended to model-loading and inpainting — NEW

Per the instruction to audit *every* runtime dependency:

| Purpose | Library | License |
|---|---|---|
| Depth model loading | transformers | Apache-2.0 |
| Model hub / weights format | huggingface-hub, safetensors | Apache-2.0 |
| Image preprocessing | torchvision | BSD-3-Clause |
| Inpainting, saliency, CV ops | opencv-python-headless | Apache-2.0 |
| Audio I/O probe | soundfile (libsndfile) | BSD-3 (LGPL-2.1 lib) |

**Inpainting:** starts with OpenCV `INPAINT_TELEA` / `INPAINT_NS` — Apache-2.0,
already a dependency, no extra model download. Adequate for the small
disocclusions the D-007 displacement cap guarantees. If insufficient, evaluate
LaMa (Apache-2.0, license-compatible but adds a model download); decision
deferred to Phase 5 with measurements.

---

## Phase 1 — Implementation

### D-021 · Apostrophe paths handled by cwd, not escaping — NEW, found by a failing test

D-018 specified `ass=filename='C\:/path/sub.ass'` as the universal fix. Building
the integration test proved that incomplete: it works for drive letters and
spaces but **not** for apostrophes.

Measured against FFmpeg 8.1 on Windows 11, with `Ngum's project` in the path:

| Attempt | Result |
|---|---|
| `filename='...Ngum'\''s project/s.ass'` (shell convention) | FAILS |
| `filename='...Ngum\'s project/s.ass'` | FAILS |
| `filename="...Ngum's project/s.ass"` | FAILS |
| `filename=...Ngum\'s project/s.ass` | FAILS |

Every form fails. FFmpeg's error reports the path back as `Ngums` — apostrophe
deleted. Isolating further: a **relative** path with no drive letter fails the
same way, which proves the filter-argument tokenizer is responsible, not the
colon handling. No escaping sequence reaches that layer.

**Resolution: stop escaping, change directory.** `filter_path_context()` detects
unescapable characters and returns a bare filename plus the directory FFmpeg
must run from (`cwd=`). Awkward characters never reach the parser.

Verified: renders correctly from `Ngum's video project/captions.ass`. A Windows
8.3 short-path fallback was also tested and rejected — Windows preserves the
apostrophe in the short name, so it does not help.

Consequences:

- `ass_filter()` and `subtitles_filter()` return `(filter_string, cwd)`. Callers
  **must** pass `cwd` to `subprocess.run`. The tuple return makes this hard to
  forget, which is deliberate.
- `escape_filter_path()` now raises `UnescapablePath` rather than returning a
  string that fails later with a misleading error.
- Only one `cwd` is available per FFmpeg invocation, and the subtitle claims it,
  so a `fontsdir` containing an apostrophe raises. Bundled fonts live in the
  package directory, so this is not expected in practice.

Why this matters beyond one character: `C:\Users\Ngum's laptop\...` is an
ordinary Windows home directory. Users would have hit an error naming a file
they never typed, with no indication of the cause.

**This is why the brief requires running the pipeline rather than trusting the
code.** The unit tests passed against a wrong implementation; the integration
test against real FFmpeg caught it in under a minute.

### D-022 · FFmpeg invocation centralised; os.chdir banned — NEW

Confirming the D-021 follow-up: `ass_filter`'s `cwd` is passed to
`subprocess.run(cwd=...)`, which sets the **child** process's directory. No
`os.chdir` exists anywhere in the codebase, verified by AST scan.

This matters from Phase 8, where the API runs concurrent render jobs in one
process. `os.chdir` is process-global and not thread-safe: one job changing
directory would silently break another's relative paths, producing
timing-dependent failures that are effectively unreproducible.

Enforcement, since a future contributor will find `os.chdir` the obvious
shortcut:

- `render/ffpath/runner.py` owns FFmpeg invocation. `run_ffmpeg()` is the only
  general entry point; `probe.py` keeps its own for capability detection.
- `test_no_chdir_in_render_code` parses the package AST and fails on any
  `os.chdir` call. AST rather than grep, so comments and docstrings mentioning
  chdir do not trip it and `from os import chdir` cannot evade it.
- `test_subprocess_calls_are_centralised` fails if `subprocess` is used outside
  the two approved modules, preventing a new call site from bypassing the
  contract.
- `test_runner_passes_cwd_to_child` asserts `subprocess.run` receives `cwd`.
- `test_concurrent_calls_do_not_interfere` runs four jobs in a thread pool with
  different working directories and asserts each child sees its own — the
  scenario `os.chdir` would break.

### D-023 · Complete inventory of path-bearing filters — NEW

D-021 was found in the subtitles filter, but the cause is FFmpeg's filter
argument tokenizer, which affects **every** filter option holding a path. Fixing
only subtitles would leave the same bug waiting in image overlays, background
music and font paths.

`render/ffpath/filters.py` is the complete inventory:

| Filter | Path options | Used for |
|---|---|---|
| `ass` | `filename`, `fontsdir` | captions (step 9) |
| `subtitles` | `filename`, `fontsdir` | SRT/VTT input |
| `movie` | `filename` | image and video overlays (steps 4, 8) |
| `amovie` | `filename` | background music (step 10) |
| `drawtext` | `fontfile`, `textfile` | title cards, progress elements |

All verified against real FFmpeg from a directory named `Ngum's projet é`,
combining the three hazards: an apostrophe (stripped by the tokenizer), a space
(needs quoting), and a non-ASCII character (needs correct encoding end to end).

**One-directory constraint.** A filter graph runs in one process with one
working directory, so at most one awkward path per invocation can be rescued by
`cwd`. `needs_staging()` detects the conflict and `stage_for_filters()` copies
files into a safe directory. Not a practical limitation: the uncontrollable path
is the user's project directory, while bundled fonts live inside the package.

Also covered: literal text in `drawtext` cannot contain apostrophes either, so
`drawtext_filter` rejects them and directs callers to `textfile`. Caption text
with apostrophes is ordinary, so this would otherwise have been a live bug.

### D-024 · License audit covers the transitive closure — NEW, correcting a Phase 1 gap

**The Phase 1 audit was insufficient.** It checked the 7 packages declared in
`pyproject.toml`. The actual runtime closure is **19 packages**, so 12
transitive dependencies shipped to users unexamined.

This mattered in principle regardless of the outcome: a permissively licensed
package can pull in a copyleft one, and that dependency reaches users just the
same. The promise is about what users receive, not what we declare.

Outcome after auditing all 19: **all permissive.** `click` BSD-3, `pydantic-core`
MIT, `pygments` BSD-2, `markdown-it-py` MIT, `mdurl` MIT, `typing-extensions`
PSF-2.0, `shellingham` ISC, `python-dotenv` BSD-3, `colorama` BSD-3,
`annotated-types` MIT, `annotated-doc` MIT, `typing-inspection` MIT. All now
recorded in THIRD_PARTY_LICENSES.

The tests now walk the dependency graph from `voxframe` rather than reading the
declared list, so a new transitive dependency fails the build until documented.

Two earlier bugs in the audit, both fixed:

1. **Environment scanning gave false positives.** An early version read `pip
   list` and reported six GPL packages — `PyQt6`, `PyMuPDF` and others that have
   no connection to Voxframe and merely happened to be installed. The audit now
   walks package metadata from the root.
2. **Free-text license fields contain license *text*.** numpy embeds a passage
   discussing the GNU GPL in its `License` field, so a substring search reported
   numpy — a BSD package — as GPL. Detection now prefers `License ::`
   classifiers and `License-Expression`, falling back to free text only when
   short enough to be an identifier.

`VERIFIED_BY_HAND` exists for packages whose upstream metadata omits a license,
and requires a substantive reason per entry. It is currently empty.

---

## Phase 2 — Transcription, segmentation, captions

### D-025 · `-shortest` removed: it silently broke the frame-grid guarantee — NEW, found by rendering

The first end-to-end render produced **342 frames where the grid demanded 343**.
FFmpeg reported success throughout.

Cause: `-shortest` and `-frames:v` were both present and disagreed. Audio rarely
lands on a whole frame — 11.4226 s at 30 fps is 342.69 frames — so the grid
rounds up to 343 while `-shortest` truncates at the audio's last full frame,
342. `-shortest` wins.

This is precisely the drift D-013 exists to prevent, arriving through an FFmpeg
argument rather than through arithmetic. The frame grid governed the *plan*;
nothing checked the *output*.

**Resolution.** `-shortest` removed. The video is pinned by `-frames:v` and the
audio ends naturally. The final frame holds for the sub-frame remainder, under
33 ms and invisible, whereas a truncated video breaks the invariant everything
downstream relies on.

**Verification added at two levels**, because one was evidently not enough:

- `_verify_frame_count()` runs ffprobe after every render and raises if the file
  differs from the grid by more than one frame. A render can no longer *claim*
  success while producing the wrong length.
- `test_rendered_frame_count_matches_grid` asserts the same against real files
  at 24, 25 and 30 fps, using a 6.37 s duration that is not a whole number of
  frames at any of them.

This closes the Phase 1 limitation that the no-drift test covered arithmetic
only. The claim is now earned for rendered output, not just the plan.

### D-026 · Word-by-word highlighting via per-word Dialogue lines — NEW

ASS offers `\k` karaoke tags, which are the obvious choice and are wrong here.
`\k` distributes highlighting across a single line's duration, so it cannot
express a gap between words: any pause mid-sentence makes the highlight drift
ahead of the speaker for the rest of the line.

Instead each scene emits one Dialogue line per word, each showing the full
caption with a different word coloured, timed to that word's own timestamps.
Lines abut exactly, so there is no flicker.

Two behaviours chosen deliberately after watching output:

- The first caption starts with the **scene**, not the first word, so a scene
  never opens on an empty frame when speech begins slightly late.
- Each caption holds until the **next word begins**, not until its own word
  ends, so a pause keeps the caption on screen rather than blanking it.

Cost: ~6 Dialogue lines per scene instead of one. For a 60-minute video that is
a few thousand lines — trivial for libass, which indexes events by time.

### D-027 · Caption margins are aspect-aware — NEW, found by looking at output

One `margin_vertical_ratio` cannot serve both orientations, which only became
obvious on screen:

- Vertical needs ~12% to clear the caption bar and action buttons that TikTok,
  Reels and Shorts overlay on the lower third.
- Landscape with 12% of a 1080-high frame puts captions a third of the way up
  the screen, looking like a mistake.

`margin_vertical_ratio` now applies to portrait and `margin_vertical_ratio_wide`
(5.5%) to landscape and square, selected by comparing frame width and height.

Also raised: the horizontal margin from 8% of height to 6% of **width**. It was
being computed against the wrong dimension, so vertical video had text nearly
touching both edges.

Neither was caught by tests. Both were caught by rendering a frame and looking
at it, which is what the brief asks for.

### D-028 · ASS `PlayRes` must match the real output dimensions — NEW

`PlayResX`/`PlayResY` define the coordinate space libass interprets font sizes
and margins in. If they disagree with the video, libass scales everything by the
ratio — captions built for 1080p and burned into 540p come out half-size, with
no error.

`render_captioned_video` therefore computes dimensions once and passes the same
values to both the encoder and the ASS header. `build_ass` documents the
contract, and `test_playres_matches_frame_size` enforces it.

### D-029 · Logging is quiet by default — NEW

structlog writes to stderr at `warning` by default, raised by `--verbose` or
`VOXFRAME_LOG_LEVEL`. A person running `voxframe make` wants progress, not a log
stream; a long job being debugged wants the opposite.

stderr specifically, so `voxframe ... | something` stays usable.

Calling `configure_logging()` is the *application's* choice. Library users
configure structlog themselves, as a library should not seize global logging
state on import.

### D-030 · Sample audio: LibriVox public domain, private samples gitignored — NEW

Per the project owner's instruction:

- **Public:** two short LibriVox recordings, English and French, with full
  provenance recorded exactly like any library asset (D-012).
  - EN: "January" from *A Calendar of Sonnets*, Helen Hunt Jackson (1830-1885),
    LibriVox #139, 1:20.
  - FR: "La Cigale et la Fourmi" from *Fables de La Fontaine, livre 01*, Jean de
    La Fontaine (1621-1695), LibriVox #80, 1:11.
  - Both trimmed to 45 s and converted to 16 kHz mono, the format Whisper
    consumes.
- **Private:** `samples/private/` holds the owner's own recordings for accent
  testing. Its own `.gitignore` excludes everything but itself and a README, so
  nothing can be committed accidentally. Voice recordings are personal data, and
  results from them are reported separately rather than becoming fixtures.
- **No synthetic TTS samples**, per instruction: their pacing is too regular to
  test segmentation meaningfully.

**Blocker, reported rather than worked around:** LibriVox audio is hosted on
archive.org, which is TLS-blocked in this environment (curl exit 35 on the
domain, timeout on the CDN nodes). The librivox.org API is reachable, so exact
URLs and metadata were retrieved and verified, but the audio cannot be
downloaded here.

`scripts/fetch_samples.py` holds the verified URLs and provenance and fails with
the exact destination path when the host is unreachable. Fixtures skip cleanly
when samples are absent, so the suite passes on a fresh clone either way.

### D-031 · Contact sheets are built by a script, not ad hoc — NEW

Two false bug reports during Phases 1 and 2 came from the *inspection method*,
not the code:

1. **A caption "ghost".** Seeking with `-ss` after `-i` on a generated lavfi
   source returned a blended frame showing two captions at once. The ASS
   timings were correct and non-overlapping; the seek was not.
2. **A caption "margin bug".** Stacking six 16:9 frames vertically made each
   caption appear a third of the way up its panel. The ASS file said
   `MarginV=40, Alignment=2`, and a single unscaled frame confirmed the
   captions were correctly placed ~40 px from the bottom. The sheet's aspect
   ratio was the problem.

Both cost real time chasing bugs that did not exist. An inspection tool that
misleads is worse than none.

`scripts/contact_sheet.py` fixes both: `-ss` precedes `-i` so seeking is
accurate, and panels are tiled in a **grid with visible gutters** so each frame
keeps its own proportions.

The wider lesson, recorded because it will recur: when output looks wrong,
check the verification artefact before the renderer. The evidence for a real
bug should be a single unscaled frame, or a value read out of the generated
file, not a composite.

---

## Pre-Phase-3 (project owner's instructions)

### D-032 · End-of-audio policy: pad with silence, never truncate — CONFIRMED and corrected

**Policy, as instructed:** when audio ends mid-frame, pad the audio with silence
to the frame boundary. Nothing is ever truncated.

Audio almost never ends exactly on a frame. Three ways to resolve the mismatch,
and only one is acceptable:

| Option | Consequence |
|---|---|
| Truncate the video | Breaks the frame grid — this was D-025 |
| Truncate the audio | Clips the speaker, possibly mid-word. Worst outcome |
| **Pad the audio** | Under 33 ms of inaudible silence; both invariants hold |

**Implementation and a correction found while implementing it.** Adding `apad`
plus `-t` was not sufficient on its own. Measured on a real render: source audio
22.7410 s, output audio 22.7330 s — **8 ms had been trimmed**, which is the
truncation the policy forbids.

Cause: `FrameGrid.total_frames` used `round()`, which can round *down*. For
22.7410 s at 30 fps that gives 682 frames (22.7333 s), a video marginally
shorter than the audio, so `-t` trimmed the tail.

`total_frames` now uses `ceil()`. The video always covers the audio, so padding
is the only adjustment ever needed and truncation cannot arise. Cost: at most
one extra frame, under 33 ms, holding the final image.

After the fix: source 22.7410 s, output audio 22.7670 s, video 22.7667 s — audio
now *longer* than source, as padding implies, and nothing lost.

Residual: output audio and video differ by ~0.3 ms, down from 24 ms. AAC encodes
in fixed 1024-sample frames and cannot subdivide further. This is a container
limit, not truncation.

Enforced by `test_audio_is_never_shortened` (three durations, none landing on a
frame boundary), `test_video_covers_the_whole_audio`, and
`test_total_frames_rounds_up`.

### D-033 · Bundle Inter as static TTFs, not the variable font — NEW

Bundled in Phase 2 rather than Phase 5, per instruction, so Phase 3's
golden-frame tests compare against machine-independent output. Without bundled
fonts libass falls back to whatever the system provides, and a golden frame then
records the test machine's font rather than the code's behaviour.

**Static weights, not the Google Fonts variable file.** Google Fonts ships Inter
only as `Inter[opsz,wght].ttf`. Tested against libass in FFmpeg 8.1: the
variable font renders at its **default instance** and ignores the ASS `Bold`
field, so `Bold: -1` produced regular weight with no visible outline. Verified
by rendering and comparing frames.

`Inter-Regular.ttf` and `Inter-Bold.ttf` from the upstream Inter v4.0 release
render at the correct weights. ~800 KB together, which is worth it: bold with a
strong outline is what keeps captions legible over imagery from Phase 3 onward.

License: SIL Open Font License 1.1, bundled as `Inter-LICENSE.txt` and recorded
in THIRD_PARTY_LICENSES. OFL permits bundling and redistribution, including
commercially, provided the font is not sold on its own and the license travels
with it.

Packaging: `pyproject.toml` declares the fonts as wheel artifacts, so they ship
with the installed package rather than only existing in the source tree.

**Interaction with D-023.** An FFmpeg invocation has one working directory, and
both the subtitle file and the font directory are paths. The subtitle usually
claims the `cwd`; when the font directory *also* needs rescuing — a user whose
install path contains an apostrophe — `needs_staging()` detects the conflict and
the subtitle is staged into a safe directory. The staging directory is removed
after the render.

---

## Phase 3 — Library, matching, scene plan, motion

### D-034 · Multilingual matching via a distilled text encoder — NEW

**The gap:** LAION ViT-B-32 (D-008) has an English-trained text encoder. French
narration would embed into a region of the space the model never learned, so
matching would be poor — unusable for one of the two languages the brief
requires.

**Options considered, all license-checked first:**

| Model | License | Size | Verdict |
|---|---|---|---|
| `sentence-transformers/clip-ViT-B-32-multilingual-v1` | Apache-2.0 | 539 MB | **Chosen** |
| `laion/CLIP-ViT-B-32-xlm-roberta-base-laion5B` | MIT | 1.46 GB | Rejected: size, migration |
| `jina-clip-v2` | **CC-BY-NC** | — | **Excluded: non-commercial** |
| Offline FR→EN translation | Apache-2.0 | ~300 MB | Rejected: see below |

**`jina-clip-v2` is excluded on licensing**, not quality. CC-BY-NC forbids
commercial use, which breaks the project's core promise, exactly as with the
Depth-Anything Base/Large checkpoints (D-007). It is the most downloaded of the
candidates, so this needs recording: popularity is not a license.

**Why the distilled encoder over the native multilingual model.** The distilled
model replaces only the *text* encoder and is trained to land in the same
embedding space as LAION ViT-B-32's **image** encoder. Consequences:

- Existing image embeddings stay valid. A user who has ingested a large library
  does not re-embed it. The LAION option replaces both encoders, forcing a full
  re-embed.
- 539 MB rather than 1.46 GB, on a project that must run on an ordinary laptop.
- English quality is unchanged, since the image side is untouched.

Accepted cost: distillation loses some accuracy against a natively-trained
multilingual model. Item 5's evaluation measures this rather than assuming it,
and the result is reported per language.

**Why not offline translation.** Translating queries FR→EN before embedding
keeps one embedding space and is the smallest change, but translation errors
compound with matching errors, each language pair needs its own model, and it
does not scale to the 50+ languages the distilled encoder covers for free.

### D-035 · Provenance cannot be blank — NEW

`NOT NULL` accepts `''` and `'   '`. An asset could therefore satisfy every
constraint while producing a credits entry naming nobody, which quietly breaks
the guarantee D-012 exists to provide.

Enforced at both layers, because they fail differently:

- **Model** (`LicenseInfo`): `min_length=1` rejects the empty string, and a
  validator rejects whitespace-only values with a message naming the field and
  the remedy.
- **Database**: `CHECK (TRIM(license_name) <> '')` and equivalents on `author`
  and `source`. Model validation can be bypassed by a future code path writing
  SQL directly; the constraint cannot.

`source_url` is deliberately exempt. A photograph a user took themselves has no
canonical URL, and requiring a fabricated one would make provenance less
trustworthy rather than more. `author="Unknown"` is likewise accepted: it is an
honest record where a blank is not.

### D-034 · CORRECTED — the distilled encoder aligns to OpenAI CLIP, not LAION

**The original entry was wrong on the fact the decision rested on.**

D-034 claimed `clip-ViT-B-32-multilingual-v1` was "distilled to match the LAION
ViT-B-32 **image** embedding space", concluding that existing image embeddings
stay valid. The model card says otherwise, unambiguously:

> "This is a multi-lingual version of the **OpenAI** CLIP-ViT-B32 model."
> "We use the **original** clip-ViT-B-32 for encoding images."
> "As teacher model, we used the original `clip-ViT-B-32`."

Pairing that text encoder with LAION `laion2b_s34b_b79k` image weights combines
two **different** vector spaces. Retrieval would be near-random.

**Why the error survived my own testing.** The EN/FR alignment figures in D-034
(0.984, 0.951, 0.839) compared *text to text*. Both texts went through the same
encoder, so the numbers were real but measured nothing about the image side.
A text-to-text measurement cannot detect a text↔image mismatch, and I presented
it as if it validated the pairing.

The lesson, which generalises: **measure the operation the system actually
performs.** The pipeline embeds text and retrieves images. Anything that does
not cross that boundary is not evidence about it.

Superseded by D-037 once the retrieval evaluation is complete.

### D-008 · REVISITED — OpenAI CLIP's actual terms

The original entry said OpenAI CLIP weights "carry usage terms we do not want
to inherit" and excluded them. That was directionally right but imprecise, and
the precision matters now that a candidate depends on those weights.

**What is actually true:**

- The CLIP **code** (github.com/openai/CLIP) is **MIT**.
- The **weights** on `openai/clip-vit-base-patch32` carry **no license tag and
  no LICENSE file**. HuggingFace reports the license as unset.
- The **model card** states, in its Out-of-Scope section:

  > "**Any** deployed use case of the model — whether commercial or not — is
  > currently out of scope."

  and separately:

  > "Since the model has not been purposefully trained in or evaluated on any
  > languages other than English, its use should be limited to English language
  > use cases."

**How this bears on the choice.** Voxframe is a deployed tool, so the card
places it out of scope. A model card is not a license and its legal weight is
debatable, but the project's promise to users is that everything required is
clearly free to use commercially. "The authors say not to deploy this, but the
weights have no license attached" does not meet that bar, and asking users to
form their own view of it would offload the problem the project exists to
solve.

The English-only caveat compounds it: the distilled multilingual encoder exists
precisely to work around a limitation its own teacher model disclaims.

Recorded as fact rather than assumption, per instruction. The retrieval
evaluation (D-037) measures all three pairings regardless, so the decision rests
on both accuracy and terms rather than on terms alone.

### D-036 · zoompan passes the D-015 gate with 2x oversampling — RESOLVED

D-015 flagged `zoompan`'s integer pixel rounding as the highest-risk item of
Phase 3, with fallbacks documented in case it could not meet the bar. It can,
with the first of those fallbacks applied.

**Measured** on a slow zoom (1.0 → 1.08 over 120 frames at 720p30) against a
high-frequency test pattern, sampling 24 consecutive frame pairs from the
middle of the move:

| Oversample | Stalled frame pairs | Motion variation |
|---|---|---|
| 1 (plain zoompan) | **3 / 24** | 0.467 |
| **2 (chosen)** | **0 / 24** | **0.113** |
| 3 | 0 / 24 | 0.151 |

A "stalled" pair is one whose frame-to-frame difference falls below a quarter
of the median: the image held still while the move accumulated rounding error,
then jumped. That is the stepping D-015 predicted, and it is present in the
unfixed path.

**The fix.** Scale up by 2, run `zoompan` at the larger size, then downscale
with Lanczos. A half-pixel move at output scale is a whole-pixel move
internally, so the rounding lands between output pixels and the downscale
resolves it into smooth motion.

3x was measured and rejected: no reduction in stalls and slightly *worse*
variation, for 2.25x the pixels. The fallbacks beyond oversampling
(`scale`+`crop` expressions, numpy compositing) are not needed.

Cost: 4x the pixels through the filter chain. Acceptable — Phase 3's timings
are reported in the phase report against the brief's performance target.

**A test-methodology note worth recording.** The gate failed on its first run
with 2 of 24 stalled pairs *with* oversampling enabled, which briefly looked
like the fix not working. The cause was the test: it rendered a 240-frame move
but sampled frames 60-84, a different and slower part of the zoom curve than
the 120-frame characterisation. The renderer was correct throughout.

This is the third time an inspection artefact rather than the code produced a
false signal (see D-031). The pattern is consistent: when a measurement
disagrees with expectation, verify the measurement's conditions match what was
characterised before concluding the code is wrong.

### D-037 · Encoder choice: LAION laion2b, single model — MEASURED

Replaces D-034, whose premise was wrong (the distilled encoder aligns to
OpenAI CLIP, not LAION). All three pairings measured on the eval set: 12
retrieval cases against a 12-image pool, English and French, on CPU.

| Pairing | top-1 EN | top-3 EN | top-1 FR | top-3 FR | FR gap | Download | ms/img | ms/text |
|---|---|---|---|---|---|---|---|---|
| **LAION laion2b (chosen)** | **92%** | 100% | **83%** | 100% | -9pt | **605 MB** | **49** | **32** |
| OpenAI B/32 + multilingual-v1 | 83% | 100% | 75% | 100% | -8pt | 1144 MB | 308 | 31 |
| LAION XLM-RoBERTa | 92% | 100% | 83% | 100% | -9pt | 1465 MB | 233 | 338 |

**The result that changes the decision: LAION laion2b handles French far
better than its "English text tower" description implies** — 83% top-1, equal
to the natively multilingual XLM-RoBERTa model. Its tokenizer and training data
carry enough French that the predicted collapse does not occur.

So the multilingual problem that prompted this whole investigation is, at this
scale, already solved by the encoder chosen in D-008.

**Why LAION laion2b:**

- Ties the best measured accuracy in both languages.
- Half the download of the next option, a third of the largest.
- Fastest on CPU by a wide margin: 49 ms/image against 233 and 308. On a
  thousand-image library that is 50 seconds versus four to five minutes.
- MIT weights, MIT code, no usage restrictions (D-008).
- One model rather than two, so no risk of a future change pairing encoders
  that do not share a space — the exact error this investigation corrected.

**Why not OpenAI + multilingual-v1:** lowest accuracy of the three in both
languages, 6x slower per image, twice the download, and its model card places
any deployed use out of scope (D-008 revisited).

**Why not XLM-RoBERTa:** identical accuracy for 2.4x the download, 4.8x the
image time and 10x the text time. It would be the right choice if French
accuracy were materially better; it is not.

**Honest limits of this measurement.** The pool is 12 generated images, not
photographs, so absolute numbers are optimistic and the gap between pairings may
compress on real content. The eval is a comparison and regression instrument,
not a prediction of field accuracy. All three pairings failed the same
`sun → desert_sand` case, where a yellow circle on orange genuinely resembles
a sun over dunes — a limitation of the generated pool rather than the encoders.

**Revisit if:** French top-1 on real libraries falls materially below English,
or a language beyond English and French is required. XLM-RoBERTa remains the
documented upgrade path and, because it replaces both encoders, would require
re-embedding — which is why this choice was made before any ingest.

### D-038 · Speed figures in D-037 were wrong — CORRECTED

D-037 reported 49 / 233 / 308 ms per image for the three pairings and used that
as a main argument for laion2b. **The measurement was invalid.**

laion2b and XLM-RoBERTa share the ViT-B/32 image architecture, so a 5x gap was
not plausible and should have prompted a check rather than a report. It came
from timing model loading, image decoding and preprocessing inside the
"embedding" measurement, with no warm-up.

**Re-measured** with model load, preprocessing and warm-up all excluded, best of
three runs over 48 images on 16 CPU threads:

| Model | Image encoding | Text encoding |
|---|---|---|
| LAION laion2b | **40.2 ms/img** | **86.5 ms/text** |
| LAION XLM-RoBERTa | **41.4 ms/img** | **189.2 ms/text** |
| OpenAI ViT-B/32 | **39.9 ms/img** | (not applicable) |

Image encoding differs by **3%**, not 5x. All three run the same architecture
and perform accordingly.

The genuine difference is on the **text** side, where XLM-RoBERTa's larger
multilingual tokenizer and wider embedding costs **2.2x**. That matters less
than it sounds: a render embeds a few dozen queries, while a library ingest
embeds thousands of images. Text speed is not a deciding factor.

**What this does to D-037.** The speed argument for laion2b is largely
withdrawn. What remains: equal measured accuracy on the small eval, 605 MB
against 1465 MB, and one model rather than two. Download size and simplicity
still favour laion2b, but the choice is now closer than D-037 presented it, and
is recorded as provisional pending the larger eval (D-039).

The general lesson, and the second time this phase: **an implausible measurement
is a bug in the measurement until proven otherwise.** A 5x difference between
models sharing an architecture should have been checked before it was reported.

### D-039 · Embedding model recorded per vector; mixing refused — NEW

Switching embedding models invalidates every stored vector, because different
models occupy unrelated coordinate systems. Comparing across them returns
*plausible-looking but meaningless* rankings — a worse failure than an error,
because the search succeeds and nothing appears broken.

Guarded at four points:

1. **Recorded per row.** `assets.embed_model` stores which model produced each
   vector. Per row rather than once per library, so a partially completed
   re-embed is detectable rather than silently mixed.
2. **Required on write.** `add()` refuses an embedding without a model id. A
   vector whose origin is unknown cannot be safely compared with anything.
3. **Checked before search.** `search()` and the matcher both call
   `check_embedding_model()` and raise `EmbeddingModelMismatch`, which names
   the configured model, the stored models, and the remedy.
4. **Repairable.** `voxframe reembed` recomputes stale vectors.
   `voxframe library-info` reports a mixed library prominently, so it surfaces
   before a render rather than during one.

Schema version bumped to 2. Assets whose files have moved are reported as
failures rather than deleted: removing a user's records because a path broke is
not this tool's decision.

This is what makes D-037 genuinely reversible. Changing models is now a
detected, repairable event instead of silent corruption.

### D-037 · AMENDED — provisional pending a larger evaluation

Two corrections arising from review.

**The eval set cannot separate these models.** Twelve cases against a
twelve-image pool means one case is 8 percentage points: the reported 92% vs
83% is a single image, well inside noise. The three pairings should be recorded
as **tied on the evidence available**, not ranked.

**The speed argument is largely withdrawn** (D-038). Correctly measured, all
three image encoders run at ~40 ms/img; they share the ViT-B/32 architecture.
The only real gap is text encoding, where XLM-RoBERTa costs 2.2x — and a render
embeds a few dozen queries against thousands of images at ingest, so it is not
a deciding factor.

**Current status: LAION laion2b stands, provisionally.** The remaining
justifications are download size (605 MB against 1465 MB), one model rather
than two, and MIT weights with no usage restrictions. Accuracy is *not* among
them, because the eval cannot yet measure it.

Superseded once the expanded evaluation completes: 50+ queries per language
against a 300+ image pool with near-duplicates and hard distractors.

### D-040 · Encoder: LAION XLM-RoBERTa, chosen on the expanded evaluation — SUPERSEDES D-037

The 12-case evaluation could not separate the models, and the conclusion drawn
from it was wrong.

**Expanded evaluation**: 50 concepts x 8 near-duplicate variants = **400
images**, **50 queries per language**. Distractors are deliberately hard:
near-duplicate variants of every concept, plus confusable concept pairs
(sun/moon, forest/farmland, lake/ocean, book/document).

| Pairing | top-1 EN | top-3 EN | top-1 FR | top-3 FR | FR gap | MB |
|---|---|---|---|---|---|---|
| LAION laion2b | 76% | 84% | 68% | 80% | **-8pt** | 605 |
| OpenAI B/32 + multilingual-v1 | 72% | 84% | 68% | 80% | -4pt | 1144 |
| **LAION XLM-RoBERTa (chosen)** | **78%** | **92%** | **80%** | **88%** | **-2pt** | 1465 |

**What the small eval got wrong.** It reported laion2b and XLM-RoBERTa as tied
at 83% French top-1. At 400 images the gap is **12 points** (68% vs 80%). The
earlier "laion2b handles French better than expected" conclusion was an
artefact of a pool too small to discriminate — one case was 8 percentage
points there.

**Why XLM-RoBERTa.** French top-1 of 80% against 68%, and top-3 of 88% against
80%. More importantly its French sits **within 2 points of its English**, where
laion2b's trails by 8. The brief requires English and French as equals; a
model that is materially worse in one of them fails that requirement rather
than trading against it.

Accepted cost: 1465 MB against 605 MB. Per-image encoding is effectively
identical between the two when measured in isolation (D-038: 40.2 vs 41.4
ms/img); the 114 ms/img seen during the eval run reflects contention, not the
model.

**Nothing had been ingested**, so no migration was needed — which is why this
choice was deliberately made before any real library existed. Had one existed,
D-039's per-vector model tracking would have detected the mismatch and
`voxframe reembed` would have repaired it.

**Honest limits.** Generated images are simpler than photographs, so absolute
scores are optimistic. The ranking should hold, since all three models face the
same pool, but the gap may compress on real content. Shared failure modes
across all three (`sunset_glow` → `sun_sky`, `farmland_fields` →
`grass_meadow`) are genuine near-synonyms rather than model weaknesses.

**Revisit if** real libraries show a French deficit, or if the 1465 MB download
proves a barrier for users on slow connections — in which case laion2b remains
a documented, switchable option with known numbers.

### D-041 · Query extraction dropped plural nouns — FIXED, found by matching real scenes

Testing `make --library` end to end exposed a significant bug that the unit
tests missed: **plural nouns were classified as verbs and discarded.**

"Waves rolled across the deep blue ocean" produced `['across', 'deep', 'blue']`.
Both "Waves" and "ocean" — the entire visual subject — were gone.

Two causes, both in the verb heuristic added for D-034:

1. **`-s`/`-es` fires on plural nouns.** English third-person singular and
   plural share the ending, and the verb check ran *before* the noun check, so
   "waves", "trees" and "mountains" all lost. Those are precisely the words
   narration uses for visual subjects. The rule is now: a bare plural is a
   noun. Verbs ending in `-s` are named explicitly in `_COMMON_VERBS` instead,
   which is a shorter list than the plural nouns it was destroying.

2. **A phrase could not restart after a verb.** Once a verb ended a phrase, the
   next word could only rejoin if it matched the noun suffix rules — and those
   recognise only a fraction of English nouns. "ocean", "forest" and "sun" all
   fall outside them, so the remainder of the sentence was abandoned. A content
   word that is neither a recognised noun nor a modifier is now treated as
   probably a noun, which is the correct prior for narration.

Also added the common irregular past tenses (`rose`, `fell`, `stood`, `came`),
which were being read as nouns — "rose" the flower is a real word, but in
narration it is nearly always the verb.

**Measured effect** on a 50-image library:

| | Before | After |
|---|---|---|
| English scenes matched | 1 / 4 | 3 / 4 |
| French scenes matched | — | **4 / 4** |

Every match is now semantically correct: ocean→ocean_waves,
forest→forest_trees, sun→sun_sky, montagnes→mountains_snow.

French scored marginally *higher* than English (0.223–0.259 against
0.211–0.245), which independently corroborates D-040's choice of the
multilingual encoder.

**Why the unit tests missed it.** They asserted that specific verbs were
excluded and specific phrases survived, using sentences I had chosen. None used
a plural noun as the subject. Tests written from the same assumptions as the
code cannot find an error in those assumptions; running the real pipeline can.

### D-042 · Similarity threshold raised to 0.22 — NEW

A library holding nothing relevant will still return its least-wrong image.
With the threshold at 0.15, narration about software matched a road, a key, a
lamp and rain — each at 0.15–0.21, and each confidently wrong on screen.

Measured on the eval and on real matching: correct matches score 0.22–0.36,
arbitrary ones 0.15–0.21. The threshold sits at **0.22**.

The reasoning behind preferring a miss: an unmatched scene renders a clean
background, which reads as a deliberate choice. A wrong image reads as a bug
and undermines confidence in every other choice the tool made.

The plan records the reason ("best similarity 0.21 below threshold 0.22") so a
user can see it and lower the threshold if partial coverage suits them better.

### D-043 · Evaluation images are generated; the bias this introduces — RECORDED

**Source.** All 400 evaluation images are procedurally drawn by
`tests/eval/generate.py` using PIL primitives (circles, triangles, rectangles,
sine-wave bands). Nothing was downloaded, scraped or copied.

**License.** None required. They are output of code in this repository and
carry the project's Apache 2.0 licence. This was deliberate: the eval runs
offline, is deterministic, and raises no licensing question — consistent with
the project's core constraint.

**Bias, stated plainly.** The eval is a *comparison and regression* instrument,
not a predictor of field accuracy:

| Bias | Consequence |
|---|---|
| Flat vector art, not photographs | CLIP is trained on photographs; absolute scores are optimistic |
| One characteristic hue per concept | A colour-only matcher would score well |
| Single visual style throughout | Style variation is untested |
| Concepts *and* queries chosen by the same author as the matcher | Shared blind spots (see D-044) |
| Simple compositions | No occlusion, clutter, unusual crops, or multiple subjects |

**What it can still establish.** All models face the identical pool, so
relative ranking is meaningful. The 12-point French gap in D-040 is far too
large to be an artefact of image style, and the miss patterns corroborate it:
XLM-RoBERTa's errors are near-synonyms while laion2b's French errors are
semantic failures.

**What it cannot establish.** Any absolute accuracy claim. The phase report and
README must not quote these numbers as expected real-world performance.

Mitigations: D-044 adds human-written queries, and real photographic evaluation
should follow once a licensed photo set is available — a Phase 4 item, since
the sourcing adapters will make one obtainable with correct provenance.

### D-044 · Two embedding models: `default` and `lite` — PROJECT OWNER'S DECISION

Decided by the project owner on the 400-image evaluation (D-040).

**`default` — LAION XLM-RoBERTa ViT-B-32**, 1.5 GB, MIT.
**`lite` — LAION ViT-B-32 laion2b**, 605 MB, MIT.

| | default | lite |
|---|---|---|
| English top-1 | 78% | 76% |
| **French top-1** | **80%** | **68%** |
| English top-3 | 92% | 84% |
| French top-3 | 88% | 80% |

`default` is the default because the brief requires English and French as
equals, and `lite`'s 12-point French deficit means roughly a third of French
scenes get the wrong lead image rather than a fifth.

`lite` exists for users on slow or metered connections, and for
English-dominant content where the 2-point English difference does not justify
2.4x the download. The trade-off is stated plainly in `docs/embedding-models.md`
rather than buried.

Selectable by `--embed-model lite` or `VOXFRAME_EMBED_MODEL=lite`. Both are MIT
LAION checkpoints whose towers were trained together, so each is internally
consistent by construction.

**Switching is safe because of D-039.** Each vector records its model, search
refuses to mix them, and `voxframe reembed` repairs a library. When and why to
run it is documented in `docs/embedding-models.md`, including the cases where
re-embedding is *not* needed (adding images, rendering, editing plans), since
the commonest support question about such a command is whether it is required.

### D-045 · Human-written evaluation queries — NEW, at the project owner's request

The generated eval queries were written by the same author as the matcher and
its query extractor, so they share assumptions about how narration is phrased.
Self-written queries cannot expose a blind spot that is shared with the code.

`tests/eval/human_queries.csv` takes independently written queries as
`query,language,expected_concept`, with `«` as the comment marker so a query may
contain `#`. `scripts/run_human_eval.py` reports results **separately** from the
generated set; the two are never averaged, because combining them would conceal
exactly the discrepancy the exercise exists to find.

`expected_concept = none` is supported and reported apart: a query with no right
answer measures whether the threshold correctly declines, not whether retrieval
ranks well.

Already informative during development. One example query I wrote for the
template — "and that is when the whole valley finally came into view below us" —
retrieved at **rank 6, similarity 0.164**. Indirect phrasing with the subject
arriving late is common in real narration and absent from every generated query.

### D-046 · Keep the hand-written extraction rules; do not adopt spaCy — MEASURED

Evaluated at the project owner's request, on real transcript text rather than
hand-picked sentences.

**Licenses differ per language model, as suspected:**

| Component | License |
|---|---|
| `spacy` library | MIT |
| `en_core_web_sm` | **MIT** |
| `fr_core_news_sm` | **LGPL-LR** |

LGPL-LR (Lesser GPL *For Linguistic Resources*) permits commercial use and
redistribution, so it would pass the project's test — the same weak-copyleft
category as libsndfile. It derives from UD French Sequoia; the model also
bundles WikiNER (CC BY 4.0) and spaCy lookups (MIT). Acceptable, but it would
be the only weak-copyleft component in the *core* matching path rather than in
an optional extra.

**Measured comparison.** Corpus: transcripts cached from real renders (English
and a French one, deliberately including transcription errors, since real
transcripts contain them), plus the 100 eval concept sentences.

| | Hand-written rules | spaCy POS tagger |
|---|---|---|
| **Subject retention** (100 sentences, EN+FR) | **46%** | **45%** |
| Speed | **0.91 ms/sentence** | 8.30 ms/sentence |
| Function-word leakage | 0.0% | 0.0% |
| Empty results | 0% | 0% |
| Queries per sentence | 2.88 | 2.62 |

**Recommendation: keep the rules.** The measurement that decides it is subject
retention — 46% against 45%, indistinguishable — while spaCy is **9x slower**.
On a 60-minute transcript that is roughly 5 seconds against 0.5, which is not
itself prohibitive, but paying it for no measurable quality gain is not
justified.

Qualitative differences exist and cut both ways. spaCy correctly drops "jumps"
from "the quick brown fox jumps over the lazy dog"; the rules keep it. The rules
extract "Montagnes" from a badly mis-transcribed French sentence; spaCy returns
"-Lavale -Tranquil", having tagged transcription noise as proper nouns. Neither
is clearly better on degraded input, which is what transcripts are.

**Additional costs avoided:** ~50 MB of models, a weak-copyleft component in the
core path, and a dependency whose per-language licences must be tracked
separately.

**Honest limits of this evidence.** Only 8 real transcript sentences were
available — 7 English, 1 French — because the LibriVox samples are still
undownloaded (archive.org is TLS-blocked here) and no private recording has been
supplied. The 100 eval sentences broaden the corpus but are self-written and
share the biases of D-043.

**Revisit when** the LibriVox excerpts and the owner's recording are available.
`scripts/compare_query_extraction.py` reads whatever transcripts are cached, so
re-running it then needs no changes. If subject retention diverges materially on
that corpus, the decision should be reconsidered — spaCy's licence position is
acceptable, so the barrier is evidence of benefit, not permission.

### D-047 · Saliency-aimed Ken Burns via spectral residual — NEW

Zooming into the geometric centre of a photograph frequently lands on sky,
water or out-of-focus background. Aiming at the subject is most of the
difference between motion that looks composed and motion that looks automatic.

**Method: spectral residual saliency** (Hou & Zhang 2007), implemented in numpy.
FFT the greyscale image, subtract the smoothed log-amplitude from itself, invert
and blur. What remains is the part of the spectrum not predictable from the rest
of the image — loosely, what stands out.

**Why this and not a detector.** No model download, no extra dependency, no
licence to audit, and ~40 ms per image measured on real photographs. A person
or object detector would be more accurate on portraits but would add weights to
download and a licence to track, for a signal used only to nudge a camera move.

**Measured on the CC0 photographs used for the render test:**

| Photo | Aim point | Confidence | Time |
|---|---|---|---|
| mountain ridge | (0.499, 0.744) | 0.96 | 128 ms |
| snowy peaks | (0.479, 0.654) | 0.91 | 48 ms |
| beach | (0.435, 0.719) | 0.98 | 36 ms |
| sunset water | (0.528, 0.583) | 0.98 | 41 ms |
| forest | (0.486, 0.493) | 0.91 | 44 ms |
| autumn lake | (0.463, 0.482) | 0.89 | 42 ms |

The vertical positions are the point: on the four landscape photographs the aim
point sits **below centre** (y = 0.58–0.74), correctly identifying land rather
than sky. On the two enclosed scenes (forest, lake) it stays near centre, which
is also right — the subject fills the frame.

**Degradation is deliberate.** A flat image yields low confidence and the move
falls back to centre; an unreadable file returns centre with zero confidence
rather than raising. A bad aim point should degrade the motion, never fail the
render. The aim point is also clamped away from the frame edge, or the crop
runs into the boundary and the move stalls against it.

First measurement is slower (128 ms) than subsequent ones (~40 ms): numpy FFT
warm-up, not image-dependent.

### D-048 · Captions get a translucent backing box — NEW, found by looking at output

The first render over real photographs showed captions **marginally legible at
best** over bright imagery. White text on snow survived only because of its
outline; on a lighter photograph it would fail outright.

An outline is enough over a controlled background, which is all Phase 2 had. It
is not enough over arbitrary photographs, which is what Phase 3 introduced.

**Fix: ASS `BorderStyle 4`**, a translucent box sized to the text, with
`BackColour = &HA0000000` — roughly 37% opaque black. This is what broadcast and
streaming captions do, for the same reason: it guarantees contrast whatever sits
behind it, rather than hoping the image is dark enough.

Controlled by `caption_box` in the style template, so a template targeting
reliably dark imagery can turn it off for a cleaner look.

Caught by rendering real photographs and looking at the frames, not by any test.
A test could assert the box is present, but only inspection revealed it was
needed.

### D-049 · Render performance with motion: measured — REPORTED

First measurement of the full path on real photographs. 31.2 s of narration,
6 scenes, 3 with imagery, on CPU.

| Output | Time | Relative to audio |
|---|---|---|
| 720p, standard | 85.3 s | **2.73x** |
| 1080p, standard | 149.0 s | **4.77x** |

**Against the brief's target** — "a 1-minute video in a few minutes on a
CPU-only laptop" — 1080p at 4.77x is roughly **4.8 minutes per minute of
audio**. That is at the edge of "a few minutes" rather than comfortably inside
it, and it is worth being plain about that rather than presenting it as met.

**Where the time goes.** Phase 2 rendered at 0.07x because it drew a flat
colour. The increase is the motion path:

- Each scene with imagery renders at **2x oversample** (D-036), so the filter
  chain processes 4x the pixels. This is what makes slow zooms smooth rather
  than stepped, and it is not optional.
- `zoompan` is single-threaded in FFmpeg, so the 16 available threads do not
  help the part of the pipeline that dominates.
- Segments are then concatenated by stream copy and captioned in one pass,
  both of which are fast.

**Not yet optimised, deliberately.** Correctness first: the frame count is
verified, motion is smooth, captions do not drift. Obvious headroom exists —
rendering scene segments in parallel across cores, since each is independent by
construction (D-011), should give a near-linear speedup on a 12-core machine.
That is a Phase 6 concern, where caching and resume are already scheduled.

Transcription remains separate: 98.6 s for 31.2 s of audio on first run, ~1 s
cached.

### D-050 · Unmatched scenes get a moving gradient, not a flat panel — NEW

D-042 established that an unmatched scene should render a background rather
than a confidently wrong image. Seeing it in a finished video showed the
execution was wrong even though the principle was right: **a flat dark panel
between photographs reads as a fault**, not a choice. It looks like a dropped
frame.

Unmatched scenes now render a slow vertical gradient with a gentle brightness
drift, so the frame carries the same sense of movement as its neighbours. The
drift is deliberately subtle — a few percent over the scene — because the aim is
to avoid deadness, not to draw the eye to a scene with nothing to show.

Dark enough that captions read clearly over it; light enough that it does not
look like an encoding failure.

This matters more than it sounds: with a small library, a third or more of
scenes may be unmatched, so what an unmatched scene looks like is a large part
of what the video looks like.

### D-051 · Search on the full sentence, not extracted queries — MEASURED

Subject retention for the extractor measured 46% (D-046), meaning most scenes
were searched without their main subject. Testing the alternatives showed the
problem was not the extractor's accuracy but the premise behind having one.

**Measured** on the 400-image generated pool, XLM-RoBERTa. **n is stated with
every figure**, because the difference between a real effect and two cases is
exactly what small corpora hide.

Direct phrasing, 50 concepts x EN/FR, **n = 100**:

| Strategy | top-1 | top-3 |
|---|---|---|
| extracted (previous default) | 71% (71/100) | 73% (73/100) |
| **full sentence (chosen)** | **79% (79/100)** | **81% (81/100)** |
| hybrid-mean (0.5/0.5) | 78% (78/100) | 79% (79/100) |
| hybrid-max | 77% (77/100) | 79% (79/100) |

Indirect phrasing — subject arriving late or by implication — **n = 28**:

| Strategy | top-1 | top-3 |
|---|---|---|
| extracted | 21% (6/28) | 29% (8/28) |
| full sentence | 29% (8/28) | 32% (9/28) |
| hybrid-mean | 29% (8/28) | 29% (8/28) |
| hybrid-max | 21% (6/28) | 29% (8/28) |

**What can and cannot be concluded.**

- **Direct phrasing: the full sentence is better.** An 8-point gap on n=100 is
  eight cases, consistent across top-1 and top-3, and the same ordering holds
  for both hybrids. This is the finding the decision rests on.
- **Indirect phrasing: no strategy is distinguishable from another.** The
  29%-vs-21% gap is **two cases out of 28** and well inside noise. The honest
  statement is that *all four strategies are weak on indirect phrasing, at
  roughly 25-30% top-1*, and this evaluation cannot rank them.
- **Full sentence is therefore at least as good as extraction everywhere, and
  better on direct phrasing.** That is sufficient to choose it, and it is the
  whole claim.

**Why extraction lost.** D-034 assumed narration was too noisy to embed
directly — that "and so what I want to talk about today is really the way that
coral reefs are changing" would embed as conversational filler. For
XLM-RoBERTa that is false: the encoder handles the filler and finds the
subject, and extraction was discarding context that helped it.

**Neither hybrid helps.** Averaging lands between two vectors, in a region
neither describes. Taking the better of two searches costs a second query for
no measurable gain. Both worth measuring; neither survives measurement.

**Extraction is kept, for a different job.** The extracted queries still go into
the scene plan so a user can see what each scene is searched for and edit it
(D-052). They no longer determine the search vector.

**Two biases that remain, stated plainly.**

1. **Same author.** The 28 indirect sentences were written by me, as were the
   50 concepts and their direct phrasings. Self-written test cases cannot
   expose a blind spot shared with the code. The project owner's queries
   (`tests/eval/human_queries.csv`, D-045) are the check on this and are not
   yet supplied.
2. **Generated images.** The pool is flat vector art (D-043), so absolute
   numbers are optimistic. All strategies face one pool, so the *ordering*
   should hold, but re-run against the photograph pool from
   `scripts/fetch_eval_images.py` before treating any figure as real.

**A false signal worth recording.** At n=8, indirect top-3 appeared to favour
extraction (50% vs 38%). At n=28 it reverses. Eight cases is one case per 12.5
points — the same error as the 12-case encoder eval (D-040). I nearly reported
that as a finding.

**Reporting rule adopted from here on:** every percentage in a decision record
or phase report carries its n. A rate without a denominator is not evidence.

### D-052 · User-edited queries override the search, and survive regeneration

A user who rewrites a scene's query in the plan has made a decision. Two things
must follow, or the plan's editability is a lie:

1. **The edit drives the search.** `Matcher.match_scenes(user_queries={...})`
   embeds the user's queries instead of the scene text for those scenes. The
   full-sentence default (D-051) applies only where nobody has intervened.
2. **Regeneration never overwrites it.** `PlannedScene.query_source` is
   `automatic` or `user`; `ScenePlan.user_edited_scenes` lists the latter so a
   caller rebuilding a plan knows what to leave alone.

`query_source` appears in the JSON as a plain string, so the realistic workflow
— open the file, change the queries, set `"query_source": "user"` — works
without tooling. A round-trip test covers exactly that path.

Blank or whitespace-only overrides fall back to the scene text rather than
searching for nothing: an empty edit is far more likely to be an accident than
an instruction.

### D-053 · Long scene text is chunked, never silently truncated

**Measured, not assumed:** the text encoder's tokenizer enforces a hard
**77-token** limit and discards everything beyond it **without any warning**. A
30x repeated sentence tokenizes to exactly the same length as a short one.

A scene at the default 8-second maximum holds roughly 20-28 spoken words, well
inside the limit, so this is not the common path. It becomes reachable through
a hand-edited plan or a slower pacing template, and a scene searching on its
opening words alone with nothing to indicate it is the kind of silent failure
this project keeps finding.

**Handling:** text over the limit is split into 40-word chunks with 10 words of
overlap, and every chunk is embedded. The overlap matters because a subject
spanning a boundary would otherwise be split across two vectors and appear
weakly in both.

Two consumers, deliberately different:

- `Embedder.embed_text()` averages the chunk vectors. Used where there is no
  query to score against; the mean of a scene's parts is a fair summary.
- `Embedder.embed_text_chunks()` returns them separately, and the **matcher
  searches with each and keeps the best hit**. Averaging would blur a scene
  that changes subject partway through, which is exactly what a long scene
  does.

Chunking is logged at info level with the token count and a text preview, so it
is visible when it happens rather than inferred later.

### D-054 · The `geq` background gradient was the render bottleneck — FIXED

D-050 gave unmatched scenes a gradient with a gentle brightness drift, drawn
with FFmpeg's `geq` filter. `geq` evaluates an expression **per pixel per
frame**. Measured: **over 60 seconds** for 159 frames at 720p — so a scene with
*no image* cost an order of magnitude more than a scene with Ken Burns motion
over a photograph.

Replaced with the native `gradients` source: **0.63 s** for the same 159 frames,
roughly 100x faster. `speed=0` and `nb_colors=2` pin it, since the source
animates its angle by default and produced a rotating wash rather than a still
background.

The brightness drift is dropped with it. It was a nicety, the gradient alone
stops the frame reading as a dropped frame, and it is not worth 60 seconds.

**How it hid.** The D-049 timings were taken while three other jobs ran on the
same machine, so the numbers were inflated across the board and the background
segments did not stand out. Re-measuring on a quiet machine, then timing each
stage, exposed it: segments were 72% of a 720p render and 88% of a 1080p one,
while an isolated Ken Burns segment took only 1.4 s.

**Two lessons, both already recorded in other forms:** benchmark on a quiet
machine, and when a total looks wrong, time the stages rather than reasoning
about which one is slow. See D-038, where an implausible speed figure came from
timing the wrong thing.


### D-049 · SUPERSEDED by D-055 — the original figures were measured under load

The 2.73x / 4.77x figures were taken while three other jobs ran on the same
machine, and the background-gradient bottleneck (D-054) was still present. Both
faults inflated them. See D-055 for figures measured on a quiet machine with
each stage timed.

### D-055 · Render speed, measured cleanly — REPORTED

31.2 s of narration, **n = 6 scenes** (4 with imagery, 2 background), CPU only,
nothing else running, after the D-054 gradient fix.

| Output | Time | x audio length |
|---|---|---|
| 720p draft | 37.9 s | **1.21x** |
| 720p standard | 47.6 s | **1.52x** |
| 1080p standard | 114.0 s | **3.65x** |
| 1080p high | 146.6 s | **4.69x** |

**Against the brief's target** — a 1-minute video in a few minutes on a CPU-only
laptop — 1080p standard at 3.65x is about **3.7 minutes per minute of audio**,
and 720p standard at 1.52x is about **1.5**. Both are inside the target, with
720p comfortably so.

Stage breakdown at 1080p standard: scene segments dominate, since each renders
at 2x oversample (D-036) through a single-threaded `zoompan`. Concatenation is
a stream copy and effectively free; the caption burn and audio mux are one pass.

Transcription is separate and one-off: 98.6 s for 31.2 s of audio on first run,
about 1 s from cache.

**Headroom, not yet taken.** Segments are independent by construction (D-011),
so they parallelise. **Corrected by D-057:** the per-stage profile shows
segments are only 16% of a 1080p render, not the dominant cost assumed here, so
parallelising them buys 16% rather than a near-linear speedup. The real
dominant cost is loading the embedding model at 54%. See D-057.

**Caveat:** n=6 scenes on one 31-second sample. A longer recording with more
matched scenes would shift the balance toward segment rendering, which is the
part that parallelises.

### D-056 · Chunk by token count, not word count

The chunking added in D-053 split text at 40 words, on the assumption that 40
words comfortably fit the encoder's 77-token limit. **That holds only for
English.** Measured against the real tokenizer:

| Text | 40 words | Verdict |
|---|---|---|
| English, ordinary | 48 tokens | inside the limit |
| French, long words | **169 tokens** | 2.2x over |
| French, technical | **192 tokens** | 2.5x over |
| German compounds | **212 tokens** | 2.8x over |

Subword tokenization is far more aggressive outside English, so a word-count
limit guarantees nothing in the second language this project is required to
support. Chunks would have been silently truncated exactly where D-053 set out
to prevent truncation.

**`_chunk_by_tokens()`** now grows each chunk word by word until one more would
exceed **64 tokens**, and steps back by however many words fit a **16-token**
overlap budget. Chunks are still built from whole words — a chunk ending
mid-word would embed a fragment — but the limit is measured in the unit that
matters. The token counter is injected, so the logic is testable without
loading a 1.5 GB model.

Verified against the real tokenizer: the French case that produced 169 tokens
now yields 4 chunks with a maximum of 61. English, French and German all stay
within budget.

**A consequence worth stating:** a French scene at the top of the 8-second
pacing window reaches roughly 119 tokens and *is* chunked routinely, where the
equivalent English scene is not. That is correct behaviour rather than a fault,
and is pinned by a test so it stays a known property.

`_chunk_words()` remains as a conservative fallback for callers without a
tokenizer, with its limit lowered to 24 words. The matcher never uses it.

### D-057 · Per-stage render profile, and which encoder each preset uses

Measured with `scripts/profile_render.py`. **n = 6 scenes** (4 matched, 2
background), 31.2 s audio, 1080p standard, CPU only, quiet machine.

| Stage | Seconds | % of total | x audio |
|---|---|---|---|
| transcription (cached) | 1.31 | 1.9% | 0.04 |
| segmentation | 0.00 | 0.0% | 0.00 |
| **embedder load** | **38.51** | **54.4%** | 1.23 |
| matching | 1.05 | 1.5% | 0.03 |
| plan build | 0.00 | 0.0% | 0.00 |
| scene segments (Ken Burns) | 11.48 | 16.2% | 0.37 |
| concatenation (stream copy) | 0.25 | 0.4% | 0.01 |
| caption file (ASS) | 0.00 | 0.0% | 0.00 |
| caption burn + final encode | 18.18 | 25.7% | 0.58 |
| **TOTAL** | **70.79** | 100% | **2.27** |

**The result contradicts the assumption in D-055.** I expected scene rendering
to dominate. It is 16%. **Loading the 1.5 GB embedding model is 54%** — more
than the entire video pipeline combined.

That changes what optimisation is worth doing:

- **Model load is a fixed cost**, independent of audio length. On this
  31-second sample it is 54% of the render; on a 10-minute recording it would
  be a few percent. It is a *startup* problem, not a throughput problem, and it
  is felt most by exactly the short-clip iteration the scene plan is meant to
  make cheap. Worth attacking directly: keeping the model resident across
  renders, or a persistent process for the Phase 8 API.
- **Parallelising scene segments buys 16% at most**, not the near-linear
  speedup D-055 implied. The claim there was too optimistic and is corrected
  here.
- **The caption burn and final encode at 26% are one full-frame pass** over
  every frame at `preset medium`. Faster presets or hardware encoding are the
  lever, not more cores.

**Encoder per stage.** Worth stating plainly because the probe reports
`h264_nvenc` as available and **nothing uses it**:

| Stage | Encoder |
|---|---|
| scene segments | `libx264`, CRF 16, `preset veryfast`, yuv444p — fixed, not affected by the quality preset |
| concatenation | stream copy, no encode |
| final encode | `libx264`, CRF and preset from the quality setting: draft 30/veryfast, standard 23/medium, high 20/slow, ultra 17/slower |

Segments deliberately ignore the quality preset: they are intermediates that
get re-encoded, so spending time on them is wasted (D-016). That is why 720p
draft and standard differed by only 10 seconds in D-055 — the preset touches
one pass of the two.

NVENC is detected but unused. Quality per bit is meaningfully worse at the
same file size, which matters for a final deliverable, and the 4 GB card on
this machine constrains it further. Revisit if encode time becomes the
bottleneck it currently is not.

**Before Phase 5.** Parallax will add to scene segments, currently 16%. If it
triples that stage the balance shifts to roughly 40/40/20 across model load,
segments and final encode — at which point parallelising segments is worth the
work it is not worth today.

### D-058 · Archive.org CDN fallback, and the Pillow import bug — FIXED from real use

Two failures reported from the project owner's machine, both real.

**1. `archive.org` is blocked network-wide, not only in the sandbox.** The main
host fails at the TLS layer (curl exit 35, `WinError 10054`) on both machines.
Probing further: `www.archive.org` and `archive.org` are blocked, while the
storage nodes — `ia801604.us.archive.org`, `ia601504.us.archive.org` and others
— respond normally.

`scripts/fetch_samples.py` now rewrites a blocked download URL for each known
CDN node and tries them in turn. The node that serves a given item is normally
found through the metadata API, which lives on the blocked host, so the nodes
are tried in order instead.

**Result: both LibriVox samples now download.** Verified end to end — English
"January" at 80 s and French "La Cigale et la Fourmi" at 71 s, trimmed to 45 s
at 16 kHz mono. Transcription confirms both are clean speech, detected at
p=1.00 in their respective languages.

One French download failed transiently and succeeded on retry, so the script
reports which host it used and leaves completed files in place for re-runs.

**2. `fetch_eval_images.py` crashed on a fresh install** with
`ModuleNotFoundError: No module named 'PIL'`. It imports `tests.eval.generate`
only for the `CONCEPTS` list, but that module imported Pillow at the top for
drawing it never performs in this path.

Pillow is now imported inside the drawing functions. The fetcher also degrades
rather than failing when Pillow is genuinely absent: the image size check is
skipped with a note at startup, and downloads are kept rather than discarded
over a check that could not run.

**Both bugs shared a cause:** they only appear on a machine that is not mine.
Neither a test suite nor a sandbox run would have found them, which is what the
owner's instruction to run the scripts himself was for.

### D-059 · Private recordings must not be shadowed by generated audio

The `private_sample` fixture fell back to the first audio file alphabetically,
which selected `demo_tts.wav` — synthetic speech I generated during
development — over the owner's real 60-second recording.

Reporting a synthetic-speech result as an accent test would have been worse
than reporting nothing: it looks like evidence and is not. Synthetic speech has
regular pacing, no accent and no background, which is the opposite of what the
test exists to measure.

The fixture now excludes known generated stems and selects the most recently
added file, on the reasoning that a file just dropped in is the one being
tested.

### D-060 · Caption margins and backing style — PROJECT OWNER'S DECISION

Three faults were visible in the first real-photograph render, all cosmetic and
all worth fixing before Phase 5 rather than after.

**Margins.** Landscape captions sat at 5.5% from the bottom, closer to the edge
than broadcast convention. Raised to **9%**, inside the 8-10% title-safe range.
Vertical raised from 12% to **14%**, keeping clear of platform UI on the lower
third.

**Backing style.** Rendered all three over the hardest case — white text on
snow — and the owner chose **padded box per line**:

| Style | Behaviour |
|---|---|
| **box (chosen)** | Translucent box per line, both lines padded to equal width |
| band | Translucent strip across the frame |
| outline | Outline and drop shadow, no backing |

Box guarantees contrast whatever is behind it, which outline cannot: the sample
image happened to have a darker rock band behind the text, flattering outline
in a way a uniform bright sky would not.

**Padding.** ASS sizes a backing box to its text, so two lines of different
length leave a ragged right edge. Lines are now padded with non-breaking spaces
to a common width — a plain space would be collapsed as leading and trailing
whitespace. The plain text is tracked alongside the tagged text for measuring,
since colour tags would otherwise count as visible characters.

`band` remains selectable but is **not yet correct**: its width comes from a
character-width estimate that is too conservative, so it renders only slightly
wider than `box`. Doing it properly needs real text measurement. Recorded as a
known limitation rather than left to be discovered.

### D-061 · Forcing the language fixes misdetection; a longer window does not

The owner's own 60-second recording — accented English — was transcribed badly:
`small` reported the language as **Yoruba at p=0.37** and dropped roughly half
the audio, while `base` reported English at only p=0.38 and made word-level
errors. Confidence scores were useless here, because `small` was confidently
wrong about which language it was even listening to.

**What was tested.** Whether sampling more of the audio before deciding would
fix it. faster-whisper exposes `language_detection_segments`; the plausible
story was that the recording's opening is atypical and a wider window would
outvote it.

**Result — it does not help.** All three settings returned the identical wrong
answer, and the word counts moved erratically rather than improving:

| detection segments | language | p | words | time |
|---|---|---|---|---|
| 1 | yo | 0.37 | 170 | 113s |
| 3 | yo | 0.37 | 127 | 95s |
| 5 | yo | 0.37 | 143 | 156s |

Identical language and probability at every setting, with 38% more runtime at 5
segments for nothing. The model is not misled by an unrepresentative opening; it
is wrong about the whole recording, and showing it more of the same audio cannot
change that.

**What does work — forcing the language.** With `--language en`:

| model | detection | words | time |
|---|---|---|---|
| small (auto) | yo p=0.37 | 127–170, erratic | 95–156s |
| small (forced en) | **en p=1.0** | **171** | 121s |
| base (forced en) | **en p=1.0** | **171** | 24s |

Both models now agree on 171 words and produce coherent text. `base` reaches
the same word count in a fifth of the time.

**Decisions.**

1. `DETECTION_SEGMENTS` reverts to **1**, faster-whisper's own default. I had
   set it to 3 on the assumption above, before measuring. The assumption was
   wrong and the default is not justified by evidence, so it goes back. The
   parameter stays exposed, since it costs nothing and a caller with genuinely
   atypical opening audio may still want it.
2. A detection probability below **0.7** logs a warning naming the detected
   language and advising `--language`. The threshold is set above the 0.37-0.38
   band seen on this recording and below the 1.0 of a forced run; it is a
   heuristic for "ask a human", not a claim about accuracy.
3. Accuracy is measured by **word error rate against a written reference**, not
   by confidence. `scripts/benchmark_transcription.py` does this. The first run
   of it reported 84.7% WER with 33 insertions — the reference covered 20s of a
   45s clip. The models were right and the reference was wrong, which is itself
   the argument for keeping references under version control with provenance.

### D-062 · The plan stores word timings, and caption text is separately editable

Two problems, one fix.

**The plan was losing timings.** `PlannedScene` stored scene text but no word
timings, so `_scenes_for_captions` reconstructed them by spreading words evenly
across the scene. Rendering the same plan twice therefore gave *worse*
highlighting the second time than the first, because the first render had the
transcript in memory and the re-render did not. Since the plan is meant to be
the renderer's only input (D-011), that made the plan lossy in a way that
contradicted its purpose.

`PlannedScene.words` now carries `PlanWord` entries with real timings. Plan
version bumped to **2**, so an existing v1 plan is refused with an explanation
rather than silently rendered with fake timings.

**Corrections need to preserve those timings.** `caption_text` holds what
captions should display when it differs from what was transcribed. The
transcript's `text` is left intact, so a correction can be reviewed against what
was actually heard, and re-applying one is idempotent.

The difficulty is that corrections change the word count. Three cases:

| case | example | timing |
|---|---|---|
| one-to-one | "leave" → "live" | inherits the original exactly |
| **split** | "cant" → "can not" | original span divided in proportion to word length |
| **merge** | "may be" → "maybe" | spans first word's start to last word's end |

Alignment is by edit distance over the word sequences, not by position: an
insertion near the start would otherwise shift every later word onto the wrong
timing. Comparison normalises case and surrounding punctuation, so adding a
comma is not treated as a word change and does not redistribute anything.

**A bug the tests caught.** My first implementation looked only *forwards* for
the stray insert or delete that accompanies a split or merge. The backtrace
walks the edit-distance table backwards, so for "cant" → "can not" the insert
lands *before* the replace, and every split and merge test failed — corrections
were reported as `1 insert, 1 replace` with an invented span, and one case put a
word at 1.8s inside a scene starting at 2.0s. Absorbing in both directions fixed
it. Worth recording because the unit-level shape looked obviously right and was
not; the invariant tests (ordering, non-overlap, staying inside the original
span) are what found it rather than the case-by-case assertions.

Words inserted by a correction have no true timing — the speaker did not say
them. They take a slice of the preceding word rather than a zero-length span,
which keeps the sequence ordered and non-overlapping as ASS requires.

### D-063 · Long scenes are paged, not truncated — captions were losing half the speech

Found by extracting frames after the caption-correction work, not by a test.

`_wrap_words` grouped a scene's words into display lines and then returned
`lines[:max_lines]`. Everything past the second line was **discarded**. The
docstring described this as deliberate — "overflowing the safe area is worse
than losing a word from an over-long scene" — but the actual effect on a real
60-second recording was that roughly half the speech was never captioned. In
scene 0, `accomplish` was absent from the ASS file entirely while `goal`, the
word immediately after it, appeared 48 times.

Worse, the truncation was invisible: the last surviving word held on screen
until the scene ended, so the caption simply froze mid-sentence and the viewer
had no way to know words were missing.

Scenes are now **paged**. Display lines are grouped into pages of at most
`max_lines`, and each page is shown during the span of its own words: from its
first word until the next page's first word, with the last page running to the
scene end. Pages abut exactly, so there is no gap, no overlap and no flicker,
and the safe area is respected because only one page is ever on screen.

Verified on the owner's recording: **170 of 170 words** now reach the rendered
ASS file, against roughly half before. Contact sheet:
`docs/phases/phase-3-caption-correction.png` (retaken on the public-domain
sonnet for the public repository, D-160).

Two things worth recording about how this was found. It survived 248 passing
tests because every caption test used scenes short enough to fit in two lines —
the tests encoded the same assumption as the code. And it was only visible by
looking at the output, which is what the standing rule about extracting frames
after every rendering change exists for.

### D-064 · Samples come from a verified GitHub release, archive.org as fallback

The sample fetcher pointed straight at archive.org, which is blocked on the
owner's network and on others like it. D-058 worked around that by hardcoding a
list of archive.org storage-node hostnames and trying each in turn — functional,
but it depends on undocumented infrastructure that can change without notice,
and it still downloads a full recording to extract 45 seconds of it.

**Now:** the 45-second excerpts are published as assets on a pinned GitHub
release and fetched from there by default, verified against a SHA-256 recorded
in `scripts/fetch_samples.py`. archive.org remains as a fallback, CDN nodes and
all. `--source release` or `--source archive` forces either path.

Three things this gets right that matter more than the convenience:

**A mismatch never falls back.** A network failure and wrong bytes call for
different responses. Unreachable means try elsewhere; wrong bytes from a
reachable host mean the recorded hash or the published asset is wrong, and
silently fetching from somewhere else would hide exactly what the hash is for.
`ChecksumMismatch` is a distinct exception for that reason, and the message
carries both hashes and the URL so it is diagnosable without re-running.

**No hash means no download.** `excerpt_sha256` is empty until the release is
actually cut, and an empty value makes the release path decline rather than
fetch unverified bytes. Recording a placeholder would look like verification
while providing none. The tests assert the values are well-formed 64-character
hex, so a placeholder fails the suite.

**The tag is pinned, not `latest`.** A fresh clone must get the bytes the
recorded hashes describe. Changing an excerpt means cutting a new tag;
replacing an asset under an existing tag would break every older checkout with
no explanation.

Redistribution is permitted — LibriVox recordings are public domain and the
texts are public domain by age — and `PROVENANCE.md` records the original
archive.org URL either way, so the release is a redistribution with its chain
intact rather than a copy whose origin has been forgotten.

The archive.org source hashes are recorded now and verified against the files
actually fetched. The excerpt hashes are left empty: publishing the release is
the owner's action, and recording a hash for an asset that does not yet exist
would be a claim I cannot stand behind. `docs/RELEASING_SAMPLES.md` has the
steps.

### D-065 · A non-independent reference reversed the model ranking

The first model comparison on the English LibriVox clip produced this, scoring
the whole 45 seconds:

| model | WER | breakdown | MB |
|---|---|---|---|
| tiny | 11.1% | S8 D1 I1 of 90 | 75 |
| base | 7.8% | S4 D2 I1 of 90 | 145 |
| small | 6.7% | S4 D2 I0 of 90 | 484 |
| **medium** | **28.9%** | **S2 D24 I0 of 90** | 1530 |

`medium` looked far worse than `tiny`. That is not a plausible result for a
larger model, and the breakdown said why: **24 deletions against 2
substitutions**. Deletions without substitutions are the signature of missing
audio, not of a model getting words wrong — the same shape as the Yoruba
misdetection in D-061.

Checking the output rather than trusting the number: `medium` covers 100% of
the audio and ends on the same word as `small`. It simply **skips the LibriVox
preamble** ("January. From A Calendar of Sonnets... read for LibriVox dot org
by...") and begins at the poem. All 24 deletions are at the start.

The reference's own README already warned about this: the preamble names a
reader and a website that appear in no published text, so that portion was
taken from `base` model output and is **not independent**. `medium` was being
penalised precisely where the reference is a transcript of a different model.

Scoring from the poem's first word, where the reference is Helen Hunt
Jackson's published text and no model has seen it:

| model | WER (independent text) | breakdown | MB |
|---|---|---|---|
| tiny | 10.6% | S6 D0 I1 of 66 | 75 |
| base | 6.1% | S3 D0 I1 of 66 | 145 |
| small | 4.5% | S3 D0 I0 of 66 | 484 |
| **medium** | **3.0%** | **S2 D0 I0 of 66** | 1530 |

**The ranking reverses.** `medium` goes from worst to best, and the gradient
becomes monotonic in model size, which is what one would expect. Zero deletions
everywhere once the preamble is excluded.

`--score-from WORD` implements this in `scripts/benchmark_transcription.py`.

**A second bias, found the same way.** My first attempt matched the literal
string `"O winter"`. `tiny` and `base` render it differently ("Oh winter",
"O, winter"), so the match failed, their preambles stayed in, and they were
charged ~24 phantom insertions each — 45.5% and 39.4%. Matching on a normalised
word fixed it. Both bugs had the same shape: a measurement artefact large
enough to invert the conclusion, and in both cases the *breakdown* gave it away
while the single WER number did not.

**Consequences.**

1. Every WER table reports the S/D/I breakdown, not just the rate. The
   breakdown is what made both errors visible.
2. A reference records what part of it is independent. The `.ref.README`
   convention is now load-bearing rather than a courtesy.
3. No default model is recommended from this clip alone. It is a 45-second
   public-domain poem read by a clear speaker — not representative of the
   accented narration this tool is meant to handle. The authoritative reference
   is the owner's hand-written transcript of their own recording.

Reported n with every percentage, per D-051.

### D-066 · Model defaults: `base` for draft, `large-v3-turbo` for final

Completing item 1b. English LibriVox clip, forced to `en`, scored from the
poem's first word so the non-independent preamble is excluded (D-065). Timings
measured with weights already downloaded, separating load from transcription:

| model | WER (n=66) | breakdown | download | load | transcribe | × audio |
|---|---|---|---|---|---|---|
| tiny | 10.6% | S6 D0 I1 | 75 MB | 11.8s | 3.9s | 0.09× |
| base | 6.1% | S3 D0 I1 | 145 MB | 1.1s | 5.4s | 0.12× |
| small | 4.5% | S3 D0 I0 | 484 MB | 2.7s | 14.9s | 0.33× |
| medium | 3.0% | S2 D0 I0 | 1530 MB | 8.1s | 35.0s | 0.78× |
| large-v3-turbo | 1.5% | S1 D0 I0 | 1620 MB | 7.3s | 41.6s | 0.92× |

**Every model is faster than real time on CPU** (no GPU, int8). Worth stating
plainly because the benchmark's own timing column suggested otherwise: on a
first run it includes the download, showing `large-v3-turbo` at 943s and
`medium` at 320s against real transcription times of 41.6s and 35.0s. The
script now says so in its output note, but the separated measurement is what
the recommendation rests on.

**Draft: `base`, unchanged.** 6.1% WER, 145 MB, 0.12× audio, 1.1s load — the
fastest to load of the five, and small enough that a first run is not a long
wait. On the owner's accented recording it matched `small`'s 171 words in a
fifth of the time.

**Final: `large-v3-turbo`.** Best accuracy at 1.5%, and at 0.92× audio still
faster than real time on CPU. Against `medium`, the obvious alternative at this
size: 6% more download and 19% more time for half the remaining error.

**`large-v3-turbo` was missing from `AVAILABLE_MODELS` entirely** and failed
with "Unknown model size" when first benchmarked. Added.

Two limits, stated rather than buried:

1. This clip is easier than the real use case — a clear speaker reading a poem,
   not accented narration. The ranking is monotonic and consistent, but the
   margins on harder audio are unmeasured.
2. n=66. `large-v3-turbo` beats `medium` by **one substitution**. Per D-051,
   the honest reading is that the two are close and both are clearly better
   than `small`; the ordering between them is not established by this clip
   alone.

The default stays `base` in code. `large-v3-turbo` is a documented
recommendation rather than a new default, because a 1.6 GB download is not
something to impose on a first run without the owner's decision.

### D-067 · The transcription model belongs to the plan, not the render — PROJECT OWNER'S DECISION

I was about to offer transcription model as a function of `--quality`. The
owner ruled it out, and the reasoning is the deciding one: a draft-then-final
workflow would re-transcribe on the final render, and **new word timings
invalidate every caption correction made against the old ones**. The user would
lose work silently, at the exact moment they were finishing.

**The rule.** The transcription model is chosen once, when the plan is created,
recorded in the plan, and never changed by a re-render.

This is structural rather than a convention to remember: `render_from_plan`
consumes only the plan (D-011) and never constructs a `Transcriber`. A test
asserts the module does not so much as mention one, so a future edit that
reintroduced transcription into the render path fails the build.

**`voxframe retranscribe`** is the one command that changes it. It prints the
old and new model, and when the plan carries caption corrections it lists the
affected scenes with their text, explains that they will be lost, and defaults
the confirmation to **No**.

Corrections are deliberately **not** carried over. A correction is a statement
about specific words — "the word at 4.14s is 'thinks', not 'takes'" — and a
different transcript may not contain those words at those times. Re-applying it
would produce text the user never approved, which is worse than making them
redo it knowingly. The command also warns that image matches are not carried
over, since scene boundaries move.

**Profiles.** `ModelProfile` sets the transcription and embedding models
together: `standard` (~3 GB) or `lite` (~750 MB, `base` + the laion2b
embedder). One setting for users on limited bandwidth, rather than asking them
to discover and match two model settings. Either can still be overridden
individually.

Provisional default is `standard`, so `large-v3-turbo` (D-066). To be confirmed
or revised once the owner's reference transcript gives an accented-audio WER —
this clip's evidence is a clear speaker reading a poem, which is not the target
use case.

### D-068 · Restricting the candidate set fixes the misdetection on its own

The owner asked whether `--languages en,fr` — which keeps detection working —
is enough, rather than forcing a single language. **It is.**

On the owner's accented English recording:

| model | setting | lang | p | words |
|---|---|---|---|---|
| small | auto | **yo** | 0.37 | 138 |
| small | `--languages en,fr` | **en** | 0.18 | **171** |
| small | `--language en` | en | 1.00 | **171** |
| base | auto | en | 0.38 | 171 |
| base | `--languages en,fr` | en | 0.41 | 171 |
| base | `--language en` | en | 1.00 | 171 |

`small` under restriction produces a transcript **identical** to the forced
run, against the garbled Yoruba output it gave unrestricted ("In allay game'
wants to accomplish the goal!" versus "You know when God calls a man..."). The
`base` output is unchanged either way, so the restriction costs nothing when
detection was already right.

This is the better default advice than `--language`: it fixes the failure while
keeping a mixed-language project working.

**The reported probability is deliberately not renormalised.** `small` picks
`en` at **p=0.18** — its own probability for English, not 0.18/(0.18+p(fr))
renormalised to something near 1.0. Renormalising would make a genuinely
uncertain detection look confident and suppress the low-confidence warning,
which is precisely the signal that saved this recording. A restriction narrows
the choice; it does not make the model certain.

**Configuration.** `VOXFRAME_LANGUAGE` and `VOXFRAME_LANGUAGES` (and the `.env`
equivalents) mean a user whose audio detects badly sets it once instead of
passing a flag every time. CLI flags still win.

**A bug found by running it.** `detect_language` takes decoded samples, not a
path; passing a path fails inside the VAD with `AttributeError: 'str' object
has no attribute 'shape'`. Now decoded with `decode_audio` first. The unit
tests mock at that boundary and would not have caught it — the real-audio run
did.

### D-069 · Phase 4's measure is scenes filled with a good match, not retrieval score

Retrieval top-1 on an eval set answers "is the matcher good?". It does not
answer the question Phase 4 exists for, which is "does a narration produce a
video worth watching?". A user with no library gets a gradient for every scene
regardless of how good the matcher is.

`scripts/measure_fill_rate.py` reports, per audio file:

- **filled** — any asset was chosen
- **good** — the match scored at or above a threshold (0.25 by default)

Both, because filling scenes with whatever ranks first is not automatically
progress: a visibly unrelated image is worse than a gradient, so a rise in
*filled* without a matching rise in *good* is a regression dressed as an
improvement.

**Baseline, measured before writing any adapter** (large-v3-turbo,
`--languages en,fr`, no library):

| audio | lang | scenes | filled | good |
|---|---|---|---|---|
| en_sonnet_january_45s.wav | en | 7 | 0/7 (0%) | 0/7 (0%) |
| fr_fable_cigale_45s.wav | fr | 8 | 0/8 (0%) | 0/8 (0%) |
| Recording (3).m4a | en | 10 | 0/10 (0%) | 0/10 (0%) |
| **total** | | **25** | **0/25 (0%)** | **0/25 (0%)** |

Every scene is a gradient. Recorded now so the Phase 4 improvement is measured
against a number that existed beforehand, rather than asserted afterwards.

Incidentally confirms the candidate set does not break multilingual work: the
French clip detects as `fr` under `--languages en,fr`.

### D-070 · Scope frozen at English and French — PROJECT OWNER'S DECISION

No further language features or measurements unless something breaks.

Additional languages are recorded as a possible future contribution. The
groundwork is already there — matching uses a multilingual encoder (D-040) and
the transcriber supports every Whisper language — so adding one is mostly a
matter of evidence rather than code: a reference transcript, a retrieval eval,
and the WER and top-1 numbers to go with them.

Noted in `docs/PROGRESS.md` and the Phase 3 report so a future contributor
finds it without reading this file.

### D-071 · ShareAlike and NonCommercial are excluded by default

Sourcing decides what license the user's own work ends up carrying, which
makes it a decision about their work rather than a technical detail.

**ShareAlike is off by default.** A video incorporating a CC BY-SA image
arguably has to be released under CC BY-SA. Silently imposing a license on
whatever someone is making — a client project, a paid course — is not a default
anyone would choose knowingly. `--allow-share-alike` enables it and prints what
it means.

**NonCommercial is off by default**, so a user cannot unknowingly build
something they may not sell.

**NoDerivatives is excluded always, at every setting.** Ken Burns crops and
scales every image, which is a modification. An ND asset is not usable
carefully; it is not usable at all.

An unrecognised license code returns `None` from `parse_license` and the
candidate is dropped. Guessing that an unknown license is permissive would put
the user in breach of terms nobody checked.

`voxframe sources` prints the active policy so this is visible before anything
is fetched.

### D-072 · Provenance is per asset, not per folder

`ingest_directory` took one `LicenseInfo` for a whole folder, which is right
for a directory the user organised themselves and wrong for one assembled from
several sources: it would credit a NASA photograph and a Flickr snapshot
identically.

Each downloaded file now gets a `.provenance.json` sidecar written at fetch
time, and ingest reads it per asset, falling back to the folder license only
when there is no sidecar. Verified on a real fetch: four files, **two distinct
licenses and four distinct authors**, all preserved through ingest.

The format lives in `models/provenance.py` rather than beside the fetcher,
because `library.ingest` needs to read it and `sourcing` needs `plan` →
`match` → `library`. Putting it in either package created a circular import,
which is how the placement was settled.

### D-073 · Sourcing searches keywords; matching searches sentences

These pull in opposite directions and both are right.

D-051 established that **full-sentence** search beats extracted keywords for
the matcher, because CLIP compares meaning in embedding space. I carried that
over to sourcing, which was wrong: a stock-media API does **literal keyword**
matching, and a 102-character sentence matches nothing.

Measured on the owner's recording, 10 scenes:

| query style | candidates found |
|---|---|
| full sentence (D-051 style) | **1 across all 10 scenes** |
| extracted keywords (2 phrases) | **4 for 9 of 10 scenes** |

`_query_for` now runs `extract_queries(..., max_queries=2)` for sourcing while
the matcher keeps full-sentence embedding. Two phrases rather than one, so a
single misheard word does not sink the whole query.

A user-edited query still overrides both (D-052).

### D-074 · Sourced files live in the library, not deleted staging — BUG

`source` downloaded into a staging directory, ingested, then deleted it. The
library stores a **path** per asset, so all 37 rows pointed at files that no
longer existed and every render would have failed on them.

Found by trying to copy the top match for each scene and getting an empty
directory — not by any test, and not by the command itself, which reported
"37 added" perfectly cheerfully.

Downloads now land in `<library>/sourced/` and stay there. The `--keep-staging`
flag is gone, since there is no longer a staging directory to keep.

### D-075 · Openverse alone does not fill scenes for abstract narration — MEASURED

The honest result, before any threshold was touched.

Sourcing 37 assets for the owner's recording (spiritual narration about
purpose and goals) and re-matching gives **0 of 10 scenes filled**. Every
scene's best similarity falls between 0.12 and 0.21 against the 0.22 threshold
(D-042) — consistently close, consistently under.

The tempting move is to lower the threshold and report 100%. Looking at the
images instead shows why that would be self-deception. The top match per scene:

- a photograph of someone's **Bible-verse tattoo**
- a **peace-sign Earth graphic**, top match for four separate scenes
- a **scripture caption card** ("Man is like a breath... Psalm 144:4")
- a person wearing a **"GOD IS IN FULL CONTROL" badge**
- a labyrinth

These are text-bearing meme graphics and amateur snapshots, not stock
photography. Several repeat across scenes. Burning captions over images that
already contain text would be **worse than a gradient**, which is exactly why
`measure_fill_rate.py` reports "good" separately from "filled" (D-069).

**The 0.22 threshold is doing its job.** The problem is what Openverse returns
for abstract queries: it aggregates Flickr and similar, so a query like
"meaningful life purpose" surfaces devotional memes rather than photographs.

Consequences for the rest of Phase 4:

1. Openverse stays as the keyless default — it is the only source that needs no
   account, and the licensing is clean.
2. A stock-photography adapter is needed for usable coverage. Those need API
   keys, so they are optional extras and the keyless path must degrade
   honestly rather than filling scenes badly.
3. Whether concrete imagery (the LibriVox winter poem) does better than
   abstract narration is being measured separately: it distinguishes "abstract
   content is hard to illustrate" from "Openverse is the wrong source".

### D-076 · Stock APIs AND their terms, so sourcing broadens until it finds something

The matcher and the sourcing search want opposite things, and the second one
needs a fallback chain the first does not.

Measured against Openverse:

| query | candidates |
|---|---|
| `winter frozen pulse heart` | **0** |
| `ice June streams` | 5 |
| `snow` | 5 |
| `winter landscape` | 5 |

A longer phrase is a *narrower* search, not a richer one. My first fix for
D-073 concatenated two extracted phrases, which produced exactly these
over-constrained four-word queries.

`_queries_for` now returns several queries, broadest last — two phrases joined,
then each phrase alone by descending weight — and the caller stops at the first
that returns anything. On the LibriVox winter poem this took the fetch from
**5 assets to 28**.

### D-077 · Openverse cannot fill scenes on its own — MEASURED, and the threshold stays

More supply did not help. With 28 assets sourced for the winter poem,
still **0 of 7 scenes filled**; best similarity per scene 0.15–0.20 against the
0.22 threshold (D-042).

The tempting conclusion was that 0.22 is too strict. Looking at the images
settles it the other way. Top match per scene for a poem about frozen streams
and snow:

- a **clipart web button** reading "PUSH THIS BUTTON TO GET STARTED RIGHT NOW!"
- a **123Greetings Easter card** with Matthew 7:7 printed across it
- a **garden gnome** in a field
- a black-and-white **chapel interior**
- an orange **fire texture**, top match for three separate scenes

Nothing winter-related. The same pattern as the owner's recording (D-075),
where the top matches were a Bible-verse tattoo and a peace-sign Earth graphic.

So this is not an abstract-content problem — the winter poem is as concrete as
narration gets. **Openverse's CC0/CC-BY pool is mostly not stock photography.**
It aggregates Flickr, greeting-card sites and clipart, and the permissive
licence filter selects for exactly the material least likely to be a usable
photograph.

**Decisions.**

1. **The 0.22 threshold stays.** It is correctly rejecting every one of these.
   Lowering it to report "100% filled" would burn captions over images that
   already contain text, which is worse than a gradient — the distinction
   `measure_fill_rate.py` exists to make (D-069).
2. **Openverse stays as the keyless default**, because it is the only source
   needing no account and its licensing is clean. It is not sufficient alone.
3. **A stock-photography adapter is required** for the Phase 4 goal. Openverse,
   Wikimedia and the like do not have the supply. Those APIs need keys, so the
   keyless path must degrade honestly — gradients — rather than filling scenes
   badly.
4. **Recorded as a negative result**, not worked around. Two sessions of
   evidence say the same thing: the fill rate is limited by the source, not by
   the matcher, the threshold or the query extraction.

### D-078 · `.gitignore` was excluding the entire domain-model package — BUG

`.gitignore` carried a bare `models/` under "Caches and model weights",
intended for downloaded ML weights. Git patterns without a leading slash match
at **any** depth, so it also matched `src/voxframe/models/`.

Consequence: `Word`, `Transcript`, `Scene`, `Asset`, `LicenseInfo` and
`provenance` — five files, the domain model the entire codebase is built on —
had **never been committed**. A fresh clone could not import Voxframe at all.

Found incidentally: `git add -A` did not stage a new `models/provenance.py`,
and checking why exposed that none of its siblings were tracked either.

Changed to `/models/`, anchored to the repository root. Audited the rest of
`src/`, `tests/` and `scripts/` for anything else silently ignored: nothing.

Worth recording for two reasons. Every phase so far reported "N tests passing"
truthfully, and every one of those runs used a working tree that a clone could
not reproduce — the suite cannot catch this, because it runs against the
working tree rather than a checkout. And it went unnoticed across three phases
because the failure is invisible from inside the repository.

**Guarded.** `tests/unit/test_repository_completeness.py` asks git what it
would actually hand someone: every source file and test tracked, every package
`__init__.py` tracked, no source path matching a `.gitignore` pattern, and the
specific `models/` pattern anchored. Verified by reverting the fix — two tests
fail, and pass again when restored. The first version of the file also caught
*itself* as untracked, which was a good sign.

Also verified directly: a fresh `git clone` imports `voxframe.models`,
`voxframe.sourcing` and `voxframe.plan`, and runs 299 unit tests green.

**A second consequence, found on the way out.** ruff honours `.gitignore`, so
it had never linted `models/` either. Unignoring the package surfaced two
violations in files that had been reported as "ruff clean" for three phases.
Both fixed; the claim is now true rather than vacuous.

### D-079 · Pexels and Pixabay are optional adapters; no key is ever required

Both activate only when their key is present. With neither set, Voxframe
behaves exactly as before: keyless Openverse, then gradient. `build_adapters`
drops an adapter whose key is absent and logs it at debug, because an optional
feature that is not configured is not an error.

Source order is configurable through `VOXFRAME_SOURCE_ORDER`, default
`local,pexels,pixabay,openverse`. `local` is not an adapter — the library is
always searched by the matcher first — so the registry skips that entry.

**Pexels, verified against the live API rather than the documentation:**

- 25,000 requests/month, reported per response in `X-Ratelimit-Remaining`
  (observed 24999/25000 on a fresh key).
- **A User-Agent header is required.** Without one the API returns 403. Found
  by a failing call, not by reading.
- Attribution is requested rather than legally required. Voxframe records the
  photographer's name and profile URL per asset and credits both them and
  Pexels.
- `large2x` (~1880px) is chosen over `original` (up to 6000px): the download is
  a fraction of the size and there is no visible difference after the frame is
  cropped to 1080p.

**Secrets.** `voxframe.sourcing.secrets` redacts by *pattern*, not by matching
against the configured key, so a rotated key, a key from another environment or
one typed into a test is caught identically. Cache keys strip secret parameters
entirely, so two requests differing only by key share an entry and a rotation
does not invalidate the cache. Verified: the Pexels `Authorization` header logs
as `***REDACTED***`.

### D-080 · Pixabay's terms are implemented, not just noted

Each obligation in their documentation maps to something in the code:

| requirement | implementation |
|---|---|
| cache responses 24h | `RequestCache`, TTL 86400s, keyed without the API key |
| no permanent hotlinking | selected images are downloaded into the library; `webformatURL` is never stored as an asset path |
| no mass downloading | only candidates the matcher selects are fetched |
| `q` ≤ 100 characters | `truncate_query` cuts at a word boundary, never mid-word |
| 100 requests/60s | `X-RateLimit-Remaining` read per response; pauses with headroom; 429 gives a clear message |
| safesearch | always on |
| `image_type=photo` | default for image searches |

**Resolution, verified rather than assumed.** `fullHDURL` and `imageURL` come
back `None` on a standard account, so `largeImageURL` (1280px) is the ceiling.
The asset records the *original* `imageWidth`/`imageHeight`, not the download's,
so the matcher can distinguish a genuinely large photograph from a small one.

A `resolution` weight (0.10) was added to `MatchWeights`: full for an asset with
enough pixels to survive the Ken Burns zoom, tapering to zero at exactly the
output size. Small on purpose — a sharper image that depicts the wrong thing is
still the wrong image.

Pixabay's own `isLowQuality` and `isAiGenerated` flags are both honoured.
AI-generated material is dropped: putting a synthesised image into a
documentary-style video is a claim the user has not agreed to make.

### D-081 · The 0.22 threshold was calibrated on generated images, and was wrong

The most consequential finding of this round.

With Pexels and Pixabay enabled, sourcing for the owner's recording produced 48
assets — and **0 of 10 scenes filled**, exactly as with Openverse. The images
had improved enormously while the scores had not, which is the signal that the
threshold, not the supply, was the constraint.

**Where 0.22 came from.** The 400-image evaluation (D-043), which is *generated*
imagery. Generated images are synthesised from text-like concepts and score
higher against text than photographs do, so a threshold tuned on them is
systematically too high for real stock photography. Against real results, 0.22
rejected **10 of 10** matches that were apt by inspection: an open Bible captioned
"#GOD IS LOVE" for a talk about God's calling, a man with arms outstretched
above clouds, a seedling in cupped hands, Earth from space.

**Measured separation.** Apt matches (the recording's scenes against a library
sourced for it) versus irrelevant ones (the winter poem's scenes against that
same library):

| set | n | min | median | max |
|---|---|---|---|---|
| apt | 10 | 0.126 | 0.187 | 0.208 |
| irrelevant | 7 | 0.135 | 0.151 | 0.162 |

The distributions **overlap** — the gap is −0.036 — so no threshold is clean.
Sweeping it:

| threshold | apt kept | irrelevant admitted |
|---|---|---|
| 0.14 | 90% | 57% |
| 0.16 | 70% | 14% |
| **0.17** | **70%** | **0%** |
| 0.19 | 40% | 0% |
| 0.22 | 0% | 0% |

**0.17** is where false positives reach zero without giving up recall: below it
they jump to 57%, above it recall falls for nothing. The 30% of scenes it still
rejects render as gradients, which is the intended behaviour (D-077).

**Result, n reported throughout:**

| audio | scenes | before | after | assets |
|---|---|---|---|---|
| Recording (3).m4a | 10 | 0 (0%) | **8 (80%)** | 6 |
| en_sonnet_january_45s | 7 | 0 (0%) | **7 (100%)** | 6 |
| fr_fable_cigale_45s | 8 | 0 (0%) | **6 (75%)** | 6 |
| **total** | **25** | **0 (0%)** | **21 (84%)** | |

Downloads: 10–15 MB per render.

This also retires the worry in D-077 that the fill rate was limited by the
matcher. It was limited by two things in sequence: the source (fixed by Pexels
and Pixabay) and the threshold (fixed here). Openverse's supply problem was
real; it was not the only problem.

### D-082 · A photograph of printed text is worse than a gradient — FOUND BY LOOKING

Found in the first render that actually filled its scenes. Two of seven showed
a **page of scripture**, matched to the LibriVox credit line ("Read for
LibriVox.org by...") because the word "Read" is close to a photograph of a book.
Captions burn over the image, so the result was two competing blocks of words
and an unreadable caption.

**Why the signal did not exist.** The sidecar recorded only the license, so
`Candidate.title` — Pexels' `alt` description — and `.tags` were thrown away,
and ingest fell back to filename-derived tags. Every Pexels asset in the library
was tagged `('pexels',)` and nothing else. The library knew nothing about what
its own images depicted.

**Three changes.**

1. `SourcedProvenance` replaces the bare `LicenseInfo` sidecar, carrying the
   description, tags, original resolution and **the query that found the
   asset** (item 25). Older flat sidecars still load, so an existing library
   ingests rather than being rejected. Tags now look like
   `('bridge', 'city', 'ducks', 'fog', 'frost', 'ice', 'krasnoyarsk')`.
2. A `text_subject` penalty (0.30) for assets whose own description names
   printed text as the subject — "bible", "typography", "newspaper",
   "scrabble". Screening the description is free; OCR would be a heavy
   dependency for a question this cheap to approximate. It is a penalty, not a
   ban: one sign in a street scene is fine.
3. **A candidate scoring at or below zero after penalties is refused**, and the
   scene renders as a gradient with the reason recorded. The penalty alone only
   *ranks*; when a text image is the sole candidate above the similarity
   threshold it would still win. Measured on the real render: the offending
   asset went 0.440 → 0.140 on one scene and to **−0.080** on another, and only
   the second was actually rejected until this was added.

Verified in the output: the duplicated scripture page is gone and that scene is
now a clean gradient with a readable caption. The remaining instance sits on the
credit line, where a photograph of a book is arguably apt and the score is
positive — left alone rather than over-tuned.

The general lesson is the one the standing rule exists for. Every score,
threshold and fill-rate number looked healthy; the fault was only visible in the
frames.

### D-083 · The 0.17 threshold was tuned and measured on the same set

A caveat on D-081, recorded at the project owner's request before it can be
forgotten.

The sweep that chose 0.17 and the fill-rate numbers that report its effect come
from **the same n=17 observations** — 10 apt matches and 7 irrelevant ones, one
voice and two LibriVox clips. Choosing the operating point on the data you then
report against is how a threshold flatters itself.

**Consequence: the 68% good-match rate may be optimistic.** The direction is not
in doubt — 0.22 rejected 10 of 10 apt matches, which no amount of tuning bias
explains — but the exact value and the resulting rate are not independently
confirmed.

**Not fixing it now.** Another eval round on the same three clips would repeat
the same bias with more ceremony. The honest test is new audio the threshold has
never seen, so this is re-checked on the **next new recording** rather than
manufactured here. If the good-match rate on unseen audio comes in materially
below 68%, 0.17 is overfitted and moves.

Noted in `docs/phases/phase-4.md` under Limitations so it travels with the
number rather than living only here.

### D-084 · The text penalty needed 0.55, and it costs fill rate — MEASURED

D-082 set the penalty at 0.30. A second render of the owner's recording showed
that was not enough: **"ACHIEVE" carved in stone** and **"EARTH" spelled in
Scrabble tiles** both reached the output. Both *were* detected and penalised —
scores fell to 0.133 and 0.157 — but survived because nothing else cleared the
threshold for those scenes. 0.30 re-ranked them; it did not reject them.

Raised to **0.55**, and the vocabulary extended with `spelling`, `tiles`,
`inscription`, `engraved` — the case where the word itself is the subject
rather than incidental.

Verified by comparing the two plans scene by scene:

| scene | 0.30 | 0.55 |
|---|---|---|
| 3 | pexels_0018 (ACHIEVE) 0.133 | **gradient** |
| 4 | pexels_0048 (EARTH tiles) 0.157 | **gradient** |
| all others | unchanged | unchanged |

Exactly the two intended scenes changed. **No good match was lost**, which was
the thing worth checking: a blunt penalty that also dropped the seedling or the
Earth-from-space image would have been a bad trade.

**The cost, stated rather than buried.** Fill rate on this clip falls from
**80% to 60%** (6 of 10 scenes, 4 gradients). Suppressing text images means
fewer filled scenes, and that is the correct trade — a caption burned over
printed text is unreadable, which is worse than a gradient (D-077) — but the
headline fill-rate number is lower because of it, not despite it.

The aggregate in the Phase 4 report is restated accordingly.

### D-085 · Video clips: never loop, and they cost bandwidth and fill rate

Phase 4b. Clips take a different render path from stills, because they differ
in four ways that each matter.

**Audio is dropped, not muted.** The narration is the soundtrack; a clip's own
wind or traffic would fight it. `-an` removes the stream so nothing survives
the concat. Verified: 0 audio streams in every rendered segment.

**Length is forced onto the frame grid.** Scene durations come from speech,
clip durations from whatever the contributor uploaded. Verified exact:
a 5-second scene renders 150 frames and a 20-second scene 600 frames at 30 fps
(D-013).

**No Ken Burns.** Two competing camera moves look like a mistake. The clip
supplies the motion.

**Scale-then-crop, never squash.** Same discipline as stills, so a 16:9 clip in
a 9:16 render is cropped rather than distorted.

#### The short-clip strategy, and why it is not a loop

A visible loop is the most recognisable mark of cheap automated video — the eye
catches the jump instantly and it reads as a fault. So:

| clip vs scene | strategy |
|---|---|
| long enough | trim |
| short | slow down, up to **2.5×** |
| far too short | slow to 2.5×, then **hold the final frame** |

Slowing reads as a deliberate slow-motion shot, which suits narration. Beyond
2.5× people move at impossible speeds, so past that the last frame freezes —
and a held frame is visually a still, which the rest of the video is full of. A
loop is consistent with nothing.

Verified on a real 12.5s clip: a 5s scene trimmed, a 20s scene slowed 1.60×.
Eight frames sampled across 17.5s of output show continuous motion through a
snowy forest with no jump.

#### The costs, measured

Clips are **not** a free improvement, and the numbers say so:

| | stills only | with clips (ratio 0.4) |
|---|---|---|
| download | 15 MB | **85 MB** (78.8 MB of it clips) |
| files | 34 | 23 |
| scenes filled | 6/7 (86%) | 4/7 (57%) |
| good matches | 5/7 (71%) | 3/7 (43%) |
| render time | — | 16.9s for 45s audio (0.38×) |

**Clips are 93% of the download for 30% of the assets.** Fill rate falls
because a clip search returns far fewer candidates than a still search — the
pool simply is not as deep — so scenes assigned to clips are likelier to find
nothing.

`media_mix` therefore defaults to `mixed` at `clip_ratio` 0.35 rather than
`clips`: motion where it helps, stills where they cover more scenes. Scenes
wanting clips are spread evenly rather than taken from the front, so the video
does not change character halfway through.

`max_clip_mb` (25) and `max_download_mb` (400) are now read by the fetcher and
the downloaded total is reported per render, so bandwidth is visible rather
than surprising.

#### Known fault, not yet fixed

The same clip was chosen for two scenes in a 3-asset pool, the second at 0.085.
The repetition penalty (0.35) is tuned for a library of stills where many
candidates clear the threshold; with clips the pool is small enough that a
repeat still wins. Recorded rather than patched blind — the fix is either a
larger clip pool or a repetition penalty that scales with pool size, and
choosing between them needs a wider measurement than this one clip.

### D-086 · A clip scene that finds no clip falls back to a still

Phase 4b measured fill rate *falling* when clips were enabled — 86% to 57%
(D-085). The cause was a gap at fetch time, not at match time: a scene assigned
to clips searched `kind=VIDEO` only, and if that returned nothing the scene got
no candidates at all. It went straight to a gradient without ever trying a
still.

The chain is now: **clip → still → Openverse → gradient.** `_search_scene` is
shared by both passes so the query-broadening chain (D-076) applies to each,
and `SourcingPlan.clip_fallbacks` counts how often the second pass fired so the
rate is visible rather than silent.

Match time needed no change: the matcher searches the whole library regardless
of kind, so a clip scoring below threshold already loses to a better still.

The requirement this satisfies is worth stating as a rule: **enabling clips must
never reduce fill rate.** Clips are an addition to the candidate pool, and an
addition that makes the result worse is a bug by definition.

### D-087 · The same asset may not repeat within N scenes — a rule, not a penalty

The graded repetition penalty (0.35, decaying with distance) is a tie-breaker:
it loses most ties, but a strong enough candidate still wins. In a small pool
that is not enough. Measured in a real render: the same clip was chosen for two
scenes in a 3-asset library, the second at **0.085**, because nothing else
cleared the threshold.

A viewer reads a repeat as a bug, not as a motif, so this is now a rule:

- **`no_repeat_window` (8 scenes):** an asset used within the last 8 scenes is
  barred outright, whatever it scores.
- **`no_repeat_below_scenes` (12):** in a video of 12 scenes or fewer, an asset
  may not repeat *anywhere*. A short video has nowhere to hide a repeat — the
  viewer sees both instances within a minute.

**The exception, and why it exists.** Barred assets are set aside rather than
discarded. If no unbarred candidate clears the threshold, the rule yields and
the asset is reused — a repeat still beats a gradient — and the waiver is
logged as `match.repetition.exception` with the scene, asset and similarity.
Silently degrading either way would be worse than choosing one and saying so.

### D-088 · The `lite` profile turns clips off by default

`lite` exists for users on limited bandwidth, and it was sized around the
models: 750 MB against 3 GB (D-067). But clips are **93% of a render's download
for 30% of its assets** (D-085), which makes them a far larger ongoing cost
than the one-off model download the profile was designed around.

So `lite` now defaults to `media_mix=stills` and caps a clip at **8 MB** rather
than 25, for a user who opts clips back on. Both are derived from the profile
and overridden by an explicit setting, so `--profile lite --media-mix mixed`
does what it says while keeping the smaller cap.

Downloaded megabytes are reported per render either way, so the difference is
visible rather than something a user discovers from their data bill.

### D-089 · The similarity threshold is per embedding model — FOUND BY MEASURING

D-081's mistake in a new form, caught before shipping.

Measuring the profiles side by side gave a result that could not be right:
`lite` filled **86%** of scenes where `standard` filled **57%**, from libraries
of the same 27 stills. A smaller, weaker embedder (D-044: French 68% against
80% top-1) does not match better.

It does not. It **scores higher**. Measured on identical scenes and images,
top-1 similarity:

| embedder | n | min | median | max |
|---|---|---|---|---|
| default | 7 | 0.167 | 0.197 | 0.236 |
| lite | 7 | 0.253 | **0.259** | 0.305 |

About 0.06 apart. A single 0.17 threshold is calibrated for `default` and
simply lenient for `lite`, so `lite` admitted matches `standard` rejected.

**The threshold now follows the embedder:** 0.17 for `default`, **0.23** for
`lite`, overridable explicitly. Re-measured at 0.23, `lite` still fills 86% on
this clip, so its advantage here is real rather than an artefact — but that is
one clip, and D-083's caveat applies to both numbers.

The general lesson, now twice: **a threshold belongs to the model that produced
the scores.** D-081 was a threshold tuned on generated images applied to
photographs; this is a threshold tuned on one encoder applied to another. Any
future embedder needs its own measurement, not an inherited constant.

### D-090 · Parallax is deferred indefinitely — PROJECT OWNER'S DECISION

Parallax was scoped for Phase 5 from the original brief and is now **deferred
as an experiment**, not scheduled.

**Why.** Video clips now supply real motion (D-085): actual camera movement
through a real scene, not a simulated one. Against that, parallax offers a
capped effect — D-007 limits displacement to 4% of image width precisely so
disocclusions stay invisible — for a depth model of 100–300 MB plus an
inpainting pass, on a project whose constraint is running on an ordinary laptop.

The cost is concrete and the benefit is marginal now that the motion it was
meant to provide comes from a better source.

**Consequences.**

- **No depth model is to be downloaded.** Not for evaluation, not to measure
  what it would look like.
- `parallax_enabled` and `parallax_max_displacement` stay in settings and
  `MotionKind.PARALLAX` stays in the plan schema. Removing them would be
  churn, and a plan that names parallax already falls back to Ken Burns with
  the reason recorded (D-007), which is the correct behaviour for an
  unimplemented mode.
- If it is ever revisited, the case to beat is a clip: parallax must look
  better than real footage of the same subject, not merely better than a still.

### D-091 · Music is user-supplied only — PROJECT OWNER'S DECISION

`--music path.mp3`, matching the original brief. **No music sourcing adapter.**

Licensing free music is materially harder than licensing images: the common
sources attach attribution and non-commercial terms that vary per track, and
getting it wrong exposes a user to a takedown on a video they have published.
The image adapters work because Pexels and Pixabay publish one clear license
each; music has no equivalent.

Behaviour:

- Sidechain-ducked under the narration, so speech always wins.
- Faded in and out.
- Looped or trimmed to the audio length **without an audible seam** — the same
  discipline as D-085's no-visible-loop rule, applied to sound.
- If the user supplies provenance, it goes in the credits file alongside the
  image attributions. If they do not, the credits say the music was
  user-supplied rather than inventing an attribution.

### D-092 · A title card appears only when the user asks for one

`--title` is required. **A title is never derived from the filename.**

The owner's own recording is `Recording (3).m4a`. "Recording (3)" rendered as
an opening title card would look broken, and a tool that does that to a user's
first video has told them it is careless. Filenames are not titles; they are
whatever the recorder happened to write.

Chapter cards follow the same rule: generated from structure the audio actually
has (long pauses), never from invented text.

### D-093 · The `band` caption backing was measured, not estimated — FIXED

D-060 shipped `band` with a known fault: its width came from "a character
averages about half the font size", and it rendered only slightly wider than
`box`. Recorded as unfixed because doing it properly needed real text
measurement.

Measured from the bundled Inter font with `ImageFont.getlength` at size 100:

| character | width | ratio |
|---|---|---|
| non-breaking space (the padding character) | 24 px | **0.24** |
| 'n' | 62 px | 0.62 |
| 'M' | 93 px | 0.93 |

The padding character is **0.24**, not 0.5 — so the code added roughly *half*
the padding it needed, which is exactly the observed symptom. Padding and text
now use separate measured ratios (0.24 and 0.55), since the shortfall is in
text widths and is filled with narrower padding characters.

Worth noting the shape of the original error: a single estimate stood in for
two different quantities, and the one it was least accurate about was the one
doing the work.

### D-094 · A non-Latin contributor name crashed the render on Windows — BUG

Found while generating the caption style sheet, not by a test.

A Pixabay contributor is credited as `徐`. Printing that credit line raised
`UnicodeEncodeError: 'charmap' codec can't encode character '\u5f90'` and
**aborted the whole render** — on Windows, whose console defaults to a legacy
codepage. Every frame had already been encoded; the render died at the final
credit print.

The console now reconfigures stdout with `errors="replace"`, so an unencodable
character degrades to `?` rather than raising. A mangled character in a
terminal credit is survivable; a failed render is not.

**The credits file and the plan keep the real name**, because they are written
as UTF-8 explicitly. Only terminal display degrades, and only on a console that
cannot show the character anyway.

This is a class of bug worth naming: the tool sources assets from the whole
world, so contributor names arrive in every script there is. Anywhere a name
reaches a byte-oriented interface is a place this can recur.

### D-095 · Caption backing per template — PROJECT OWNER'S DECISION

Chosen from the three-style comparison over real stock photography and clips
(`docs/phases/phase-5-caption-styles.png`):

| template | backing | why |
|---|---|---|
| default / documentary | **box** | guarantees contrast whatever is behind it |
| energetic / social | **outline** | cleanest over busy footage; the look the format expects |
| any | band | available, not a default |

`box` stays the schema default, so a template that says nothing gets the safe
option.

### D-096 · Cosmetic decisions are made without asking — PROJECT OWNER'S DECISION

Minor styling and cosmetic choices are mine to make and record. Decisions that
touch **functionality, licensing, user data, performance, or the project's
direction** still come to the owner.

Recorded because it changes how the rest of this project is built, and because
the boundary matters: "it looks better" is mine, "it downloads 400 MB", "it
requires an account", "it keeps a copy of your audio" and "it changes what the
tool is for" are not.

### D-097 · Crossfades, and the two ways they break the frame grid

Phase 5's first item, and the one with the most ways to go quietly wrong.

**Where a transition goes.** Not every boundary should blend:

| condition | choice | why |
|---|---|---|
| speaker paused ≥ 0.45s | cut | the silence already marks the break; a blend softens what the audio states |
| same asset either side | cut | crossfading an image into itself looks like a stall |
| blend would exceed ¼ of the shorter scene | cut | a 1s scene has no clean frames left |
| otherwise | crossfade 0.4s | |

Each choice records its reason, so a surprising cut is explicable from the plan
rather than requiring the code.

**Problem 1: xfade shortens the video.** It *overlaps* its inputs, so chaining
N transitions loses the sum of their lengths. Measured: three segments totalling
530 frames with two 12-frame crossfades produced **506** — ending 0.8s early,
with every caption after the first transition drifting. Exactly the failure
D-025 exists to catch.

Fixed by rendering each outgoing segment with the frames it gives away:
`padded_durations` adds the blend length, the overlap consumes the padding, and
the output equals the sum of the *scene* durations. Verified end to end on a
real render: **1350 frames out, 1350 in the plan, 45.000s**.

**Problem 2: xfade refuses mismatched timebases.** Chaining `concat` into
`xfade` fails with `-22 Invalid argument`, and the useful message only appears
at `-v warning`:

    First input link main timebase (1/1000000) do not match the
    corresponding second input link xfade timebase (1/15360)

`concat` emits 1/1000000; a decoded h264 stream is 1/15360. Every input is now
normalised with `settb=AVTB,setpts=PTS-STARTPTS`, and `settb` is reapplied after
each stage since both filters reset it.

Worth recording how long this took to find: `-22 Invalid argument` says nothing,
and I spent two attempts guessing (setpts alone, then the offset) before asking
FFmpeg for the real error. **Raise the log level before theorising.**

**Cost.** A video of all cuts still uses the stream-copy concat demuxer and pays
nothing. Only a render with at least one blend takes the re-encoding path.
Measured: 28.1s for 45s of audio (0.63× realtime), against 16.9s without.

### D-098 · Four style templates that change the edit, not just the paint

The schema was complete and exercised by one template. Three more, chosen so
each differs in **pacing** as well as appearance — a template that only changed
fonts would not be worth the flag.

| template | scenes | motion | transition | captions |
|---|---|---|---|---|
| clean-educational | 4–8s | 0.5 | crossfade 0.4s | box, 0.045 |
| documentary | 6–12s | 0.3 | crossfade 0.6s | box, 0.042 |
| energetic | 2–4.5s | 0.8 | **cut** | **outline, uppercase, 0.058** |
| minimal | 5–10s | **none** | cut | box, 0.045 |

The reasoning behind each choice:

- **documentary** holds a shot. Changing image every four seconds reads as
  restless against measured narration, and the gentler Ken Burns keeps apparent
  speed constant over the longer scene.
- **energetic** cuts hard. A crossfade is the opposite of punchy, and at
  two-second scenes there is no room for one anyway. Captions are uppercase,
  larger and outline-only (D-095) because this is watched on a phone, often
  muted.
- **minimal** has no movement at all, which also makes it the cheapest render:
  no zoompan, no 2× oversampling, no re-encode for transitions.

**Verified they change the actual edit**, not just the config: the same 45
seconds of audio produces **6 scenes** as documentary, **14** as energetic and
**7** as minimal — and all three land on exactly 1350 frames, so the grid holds
across every pacing (D-013).

**A consequence worth stating.** Changing template re-segments the audio, so a
library sourced for one template under-serves another: energetic's 14 scenes
matched 5 against a library built for a 7-scene edit. That is correct
behaviour, not a fault — but the workflow is choose the style, *then* source.
The `source` command already works from a plan, so it follows naturally.

### D-099 · Cards add time, and three things must move with them

Title and chapter cards are rendered as ordinary scenes carrying `card_kind`
and `card_text` instead of an asset, so they sit on the frame grid and pass
through transitions and the caption burn unchanged.

**A card adds time rather than taking it.** The audio is fixed; stealing frames
from the first scene to make room would desynchronise everything after it. So
the video becomes longer than the audio by the total card length — 1440 frames
against 1350 for a 3-second title.

That means three things must shift, and missing any one produces a video that
looks right in a thumbnail and is wrong to watch:

1. **The audio**, by `adelay`, or the narration plays over the title card.
2. **The captions**, by the same offset, or every word fires while the title is
   still on screen. Word timings come from the transcript, which knows nothing
   about cards.
3. **The tail padding**, by `apad`, which was already there for D-032.

Verified end to end: 1440 frames, 48.000s for 45s of audio, and the first
caption starting at **0:00:03.00** — exactly when the card ends.

**Chapter cards need a real boundary.** A silence of 2.0s or more, well above
the 0.35s used for scene boundaries and 0.45s for transitions: a chapter break
is a speaker stopping and starting a new thought, not breathing. They are also
suppressed below three minutes of audio, where a 2-second card is longer than
the section it introduces.

Their text is the **opening words of the section that follows**, not a
generated summary. A summary would be the tool inventing content; the first six
words are a quotation.

Per D-092, a title card appears only with `--title`. The chapter rule follows
the same principle: cards report structure the audio has, never structure
inferred from a filename or invented outright.

### D-100 · Music ducking: measured, not estimated

`--music track.mp3` only, per D-091. Three parts, each verified against real
audio rather than assumed.

**The sidechain works, and I nearly concluded it did not.** Measuring the whole
mixed file showed ducked and unducked within 0.2 dB, which looked like a broken
compressor. It was a broken *measurement*: the speech dominates both files, so
a whole-file average cannot show what the music underneath is doing. Isolating
the bed shows the real behaviour — **11.2 dB of reduction during speech, none
in silence**, which is exactly right.

Worth recording as a method note: when a measurement says a filter does
nothing, check that the measurement can see the thing before concluding the
filter is broken.

**The level was wrong, and that was real.** The first gain of 0.18 put the bed
22 dB under the speech once ducked — inaudible, so the feature would have
"worked" while doing nothing a listener could hear. Measured against narration
averaging -25 dB:

| gain | bed vs speech (quiet) | ducked |
|---|---|---|
| 0.18 | -11 dB | -22 dB |
| **0.30** | **-6 dB** | **-17 dB** |
| 0.60 | 0 dB | -11 dB |

0.30 is present in the gaps and out of the way under speech. Verified in the
finished video: **-33.4 dB in silence against -23.6 dB during speech**, matching
the isolated test exactly.

**Looping without a seam.** `aloop` butt-joins the end to the start, and unless
the track was produced for looping that edge is audible — the audio equivalent
of the visible video loop D-085 refuses. The bed is built from overlapping
copies joined with `acrossfade`, so the seam is hidden.

**Cards and music compose.** The narration keeps its own `adelay`/`apad` before
entering the mix, so a title card still pushes the speech later and the music
still fills the whole video (D-099).

**Credits.** The track is recorded in the plan, not just passed to the
renderer, so credits remain a projection of what was actually rendered (D-012)
and a re-render reproduces the same mix. A track with no supplied attribution
is credited as "supplied by the user" rather than given an invented one.

### D-101 · Segment caching, so a long render can resume

Phase 6's first piece. Transcripts and API responses were already cached;
segments were not, so a render that failed at minute 50 restarted from zero.
Irrelevant at 45 seconds, decisive at an hour.

**What makes a segment cacheable** is that it is a pure function of its inputs:
the asset, the frame count, the output size, the motion settings, the quality
preset, and nothing else. The key hashes exactly those. Anything omitted is
something that can change while the cache serves stale frames; anything
included unnecessarily is a cache that misses when it should hit.

Verified both directions, because a cache is only useful if both hold:

| changed | key |
|---|---|
| frames, width, height, fps, motion, quality, background | **changes** |
| scene index | **unchanged** |

The second matters as much as the first. Inserting a title card renumbers every
scene after it (D-099), and renumbering does not change what a segment looks
like — keying on index would throw away the entire cache for a one-word title.

The asset is keyed by its **content hash**, not its path, so the same image
moved or re-downloaded elsewhere still hits.

**Measured**, 7-scene video at 720p with crossfades:

| render | time | segments |
|---|---|---|
| cold cache | 115.0s | 0/7 reused |
| warm cache | **61.5s** | **7/7 reused** |

And the output is **pixel-identical**: PSNR between the two renders is `inf`. A
cache that served slightly different frames would be worse than no cache, so
this is checked rather than assumed.

`RENDERER_VERSION` invalidates everything when the renderer changes in a way
that alters output. Without it, a fix to the motion planner would leave every
cached segment stale and the next render would silently reuse the old
behaviour.

Two smaller guards: a zero-length cache entry is treated as a miss and deleted,
since it is a crashed render rather than a valid segment; and entries are
written to a `.partial` file and renamed, so an interrupted copy cannot leave
something that looks valid.

### D-102 · The web app comes before the AI director — PROJECT OWNER'S DECISION

Phase 8 moves ahead of Phase 7. The order is now 6, **8**, 7, 9.

**Why.** The web app is what makes Voxframe usable by people who are not
developers. Everything built so far is reachable only through a terminal, which
means the tool currently serves an audience that is not the one the brief
describes. The AI director is an *optional enhancement* to a pipeline that
already works without it (the brief says so); the interface is the difference
between a working pipeline and a working product.

Nothing about Phase 7 gets harder for waiting — it consumes the scene plan like
everything else, and the plan's schema is settled.

**A consequence worth naming.** The web app will surface decisions that were
made for a CLI audience: what a scene plan looks like to someone who has never
seen JSON, what "similarity threshold" means to a person who just wants a
better picture, and how much of the pipeline's honesty about failure
(gradients, low-confidence warnings) survives a graphical interface. Those are
interface decisions, not merely presentation, and they get recorded here as
they come up.

### D-103 · A full LibriVox chapter is the long-audio sample

45-second excerpts cannot exercise what Phase 6 exists to test: chapter
detection needs three minutes of audio (D-099), segment caching only matters
across hundreds of scenes, and memory at scale is meaningless at seven.

Added `en_golden_river_chapter`: "3b - Example Story: The Golden River" from
*How to Tell Stories to Children* by Sara Cone Bryant (1918, public domain),
read by Sean McGaughey. **14 minutes 41 seconds**, kept whole rather than
excerpted — `trim_seconds=0.0` now means "keep the recording".

Same provenance handling as the other samples: gitignored, fetched by
`scripts/fetch_samples.py`, attribution written to `PROVENANCE.md`. It came
down through the archive.org CDN fallback, which is D-058 still earning its
keep.

### D-104 · Long audio works, and transcription is the bottleneck — MEASURED

First end-to-end run on 14:41 of audio. **It works**: 117 scenes, 26,425
frames, 880.83s of video against 880.82s of audio. The frame grid holds at
scale, memory stayed flat, and nothing needed changing to make it complete.

**But it took 9.41× realtime** — 2.3 hours for 14 minutes — which is not a
usable figure and is the real result of this test.

Where the time went, measured rather than assumed:

| stage | time | share |
|---|---|---|
| transcription (`large-v3-turbo`, CPU, uncached) | ~2h | **~85%** |
| scene segments (117 gradients) | ~2 min | 1.5% |
| caption burn | ~9s | <0.5% |
| final encode | ~90s | 1% |

**Two hypotheses I had were wrong**, and both were cheap to check:

1. *"The caption burn must be the bottleneck"* — 2,389 dialogue lines, one per
   word, over 26,425 frames. Measured: captions add **0.3s per 30s of video**.
   Not the problem.
2. *"Rendering scales badly"* — a 7.5s gradient segment takes 1.1s, so all 117
   are about two minutes. Not the problem either.

It is transcription, and it does **not** scale linearly. Measured on both
models:

| model | 45s clip | 14:41 file | degradation |
|---|---|---|---|
| base | 0.12× | **0.90×** | 7.5× |
| large-v3-turbo | 0.92× | ~8× | ~8.7× |

**Both degrade by about the same factor**, which rules out the explanation I
first reached for. It is not that the larger model scales worse — the *shape*
is identical, so the cause is in how a long file is decoded rather than in
model size. Most likely faster-whisper's per-segment cost grows with
accumulated context within a single call.

The practical reading: `base` at 0.90× is usable for a 14-minute file where
turbo at 8× is not, but switching model treats the symptom. Chunking addresses
the cause and helps both.

**Consequences for Phase 6.** Segment caching (D-101) does not help here,
because segments are not the cost. What would:

1. **Chunked transcription** — split long audio at silence, transcribe each
   piece independently, and stitch the results with corrected offsets. Bounds
   the per-call context, which is what both models appear to suffer from, and
   makes progress resumable.
2. **Not** switching model by duration. `base` is 9× faster here, but the
   degradation factor is the same, so that hides the problem rather than
   fixing it — and it would silently give long recordings worse transcripts
   than short ones, which is exactly the kind of invisible quality cliff this
   project has been avoiding.

Recorded before fixing, because the number is what justifies the work and a
"9.41× realtime" claim deserves to be on the record whether or not it improves.

### D-105 · Chunked transcription: 5.1× faster on long audio — MEASURED

The fix for D-104. Long audio is split at silence, each piece transcribed
independently, and the results stitched with corrected offsets.

**Measured on the 14:41 chapter, `base` model, CPU:**

| approach | time | realtime factor |
|---|---|---|
| single call | 794s | 0.90× |
| **chunked** | **155s** | **0.18×** |

**5.1× faster**, and 0.18× is close to the 0.12× the same model achieves on a
45-second clip — which confirms the diagnosis rather than merely improving the
number. The degradation was per-call context, not model size or total length.

**Accuracy cost: 1.7%** (S29 D6 I6 of 2,395 words) against the single-call
result. Neither is ground truth, so this is *divergence*, not error — the
chunked version may well be right where they differ. Six chunk boundaries
producing about seven differing words each is exactly where context loss would
show.

**Design points that matter:**

- **Boundaries are placed in silence**, found by `silencedetect` (278 of them
  in this file, in 0.5s). Cutting mid-sentence gives the decoder half a phrase
  and the words either side come back wrong.
- **Timestamps are offset, not re-derived.** Each chunk knows only its own
  time base, so every word has its chunk's start added back. Getting this
  wrong desynchronises captions from audio.
- **Language is fixed after the first chunk.** Re-detecting per chunk risks a
  quiet passage being called a different language mid-recording — D-061's
  failure, repeated seven times.
- **Below 240s, nothing changes.** Chunking costs a silence-detection pass
  that a short clip does not need.
- **The cache key distinguishes chunked from single-call**, because they
  produce different word timings at boundaries and are genuinely different
  transcripts.

A fallback is kept for a file with no detectable silence: the chunk is cut at
the 180s maximum anyway. A hard cut mid-word is bad; a chunk long enough to
reintroduce the degradation is worse.

### D-106 · An apostrophe in card text crashed the render — BUG

The first long render died in 6 seconds with `UnescapablePath`. A chapter card
label quoted the speaker: *"The Golden River adapted from Ruskin's"*. Card text
was passed as a literal `drawtext` argument, and FFmpeg's filter parser strips
apostrophes regardless of escaping — the same fault as D-021, in a new place.

It surfaced only now because chapter cards need three minutes of audio to
trigger (D-099), so no earlier test could reach it. **The 45-second samples had
been hiding a whole class of bug.**

Card text now goes in a file, passed as `textfile=`. The helper already
documented this as "preferred for anything long or containing characters
awkward to escape" — the tool was there, I simply used the wrong parameter.

Worth naming the general shape: **anything derived from user speech will
eventually contain every character a parser dislikes.** Card labels, titles and
credits are all in that category.

### D-107 · Highlights must cover the recording, not just its edges

Highlights mode selects passages by measurable proxies — speech density, a
matched image, position, length — and deliberately **does not** summarise or
rank by importance. Those need a language model, and a highlights mode that
silently required one would make the core pipeline depend on something the
brief keeps optional.

**The first version was wrong, and only measuring the distribution showed it.**
On the 14:41 chapter it chose 19 scenes totalling 149s — correct length, and
every one of them from the first 20% or last 20%:

```
  0- 10%: #########      50- 60%:
 10- 20%: ##             60- 70%:
 20- 30%:                70- 80%:
 30- 40%:                80- 90%: ###
 40- 50%:                90-100%: #####
```

Nothing at all from the middle 60%. The opening and closing bonuses simply
dominated every other signal, and a "highlights" video that skips most of the
recording is not representative of it.

Fixed by dividing the timeline into five bands, each with a share of the
budget: within a band score still decides, across bands coverage does. A second
pass spends any budget a thin band leaves over, so the result is not short.

After:

```
  0- 10%: ###            50- 60%: #
 10- 20%: #              60- 70%: ####
 20- 30%: ####           70- 80%:
 30- 40%:                80- 90%: ###
 40- 50%: ###            90-100%: #
```

Selected scenes are restored to **chronological order** before rendering. Played
by score, the video would jump around its own timeline — disorienting in a way
the score cannot see.

### D-108 · Long audio, re-measured: 9.41× → 0.71× realtime

The full pipeline on 14:41 of audio, after chunked transcription (D-105):

| | before | after |
|---|---|---|
| wall time | 8,287s | **627s** |
| realtime factor | 9.41× | **0.71×** |

**13× faster, and now faster than realtime.** A 14-minute recording renders in
10.5 minutes rather than 2.3 hours.

Verified at scale: 26,605 frames against a plan declaring 26,605, memory flat,
148 scenes, and **3 chapter cards** — the first time D-099's chapter detection
has run on audio long enough to trigger it.

The scene count differs from the earlier run (148 against 117) because this
used `base` rather than `large-v3-turbo`, which transcribed more words. Not a
regression; a different transcript produces different scene boundaries.

### D-110 · Cards shifted scenes but not their words — BUG

Captions on any video containing a card fired **early**, by the total length of
every card before them: 0.87s after one, 1.74s after two, growing with each.

`insert_cards` (D-099) moved each scene's frames forward to make room but left
the word timings alone. The audio was delayed correctly, the scene boundaries
moved correctly, and the words inside them kept pointing at the original
recording.

**Why it survived this long.** Cards need three minutes of audio to trigger, so
no 45-second sample ever had one. Measured: the short clip has **0 of 7** scenes
misaligned; the 14:41 chapter had **15 of 145 scenes whose words ended entirely
before their scene began**. A whole class of bug was hiding behind the sample
length — the second time in this phase (D-106).

**How it was found, which is worth recording.** Highlights produced captions
that did not match the audio, and I assumed the fault was in highlights. It was
not. Walking back through the pipeline: chunked transcription produced
identical timings to single-call, and segmentation straight from the transcript
gave **0 of 145** misaligned. That isolated it to what happens between
segmentation and the plan, which is card insertion. Three wrong guesses before
the measurement pointed at the right place.

Fixed by shifting each scene's word timings by the same amount as its frames.
Verified: 0 of 148 scenes misaligned with 3 cards inserted.

### D-111 · Highlights must cut the audio, not just the scenes

Selecting scenes rebuilds the video timeline but leaves the audio untouched, so
the result played the original recording's **first 110 seconds** against scenes
drawn from throughout it. Verified before fixing: at 58s the captions read *"A
last-ticket bear no longer, he must drink"* while the audio said *"hands and
shorts were so cruel and so mean"*.

`extract_highlight_audio` now cuts and concatenates the corresponding ranges in
one filter graph — `atrim` per range then `concat` — so the audio matches the
rebuilt timeline frame for frame.

A second issue surfaced in the same place. A word belongs to the scene holding
its **midpoint** (D-011), so a word straddling a boundary legitimately starts
before its own scene. Harmless over continuous audio; wrong once the preceding
audio is cut away. Words are now clamped into their scene before rebasing.

### D-112 · D-109 does not exist

Four code comments cited **D-109** for the finding that highlights must cut the
audio, not just the scenes. No such entry was ever written — the numbering
skipped from D-108 to D-110, and the finding those comments mean was written up
as **D-111**.

The references now point at D-111. Recorded rather than silently renumbered,
because a decision log whose numbers move is worse than one with a gap: anything
written down elsewhere that cites a number must keep meaning what it meant.

D-109 stays permanently unused.

### D-113 · Phase 8 web app: the approved shape — PROJECT OWNER'S DECISIONS

The plan in `docs/WEB_APP_PLAN.md` was approved with six additions. Recorded
here because several of them constrain code that has not been written yet, and a
later session must not quietly choose differently.

**1. React with Vite, built to static files served by FastAPI. No Next.js.**
Next.js brings a Node server and a build system for server-side rendering that a
localhost tool serving one user does not need. Vite emits static files; FastAPI
serves them. Dependencies stay minimal and each is recorded in
`THIRD_PARTY_LICENSES` — the Phase 1 entry listing `next` has been corrected to
react, react-dom, vite and tailwindcss.

**2. Localhost-only for Phase 8, with installers in Phase 9.** One-click
installers for Windows and macOS bundling Python and FFmpeg, so a non-developer
never opens a terminal. **An installer bundling GPL FFmpeg is a GPL composite**
and is handled and documented exactly like the Docker image (D-016): the bundle
as a whole is distributed under the GPL, Voxframe's own source stays Apache-2.0,
and the obligation is stated rather than discovered. A hosted multi-user version
is out of scope and would be planned separately — it needs authentication, job
isolation, resource limits, and a policy for holding other people's audio.

**3. Sourcing consent is asked once, in plain language, and remembered.** A
short first-run screen states what is sent (words from the transcript), to which
services, and why it improves results; the choice is remembered and changeable
in settings. **Never silently on.** Users enter their own free API keys in
settings, with links to obtain them.

*The owner's personal keys must never appear in any package, installer, build or
fixture.* A release-time check fails if any key-like value from `.env` appears in
a built artifact — the same "ask what would actually ship" approach as the
`models/` guard (D-078) and the `.env` guard, rather than trusting review.

**4. A sixth screen: Library.** Upload the user's own images and clips with
provenance fields (author defaulting to the user, licence defaulting to "own
work"), browse thumbnails, see source and licence per asset, delete, and see
which videos used an asset. Provenance still cannot be blank (D-035); defaulting
it sensibly is not the same as omitting it.

**5. Local security from step 1, not retrofitted.** Bind 127.0.0.1 only,
validate the `Host` header, require a per-session token, confine file access to
the job and library directories, and test each. Written up below.

**6. A first run with an empty library and no keys must still produce a decent
video**, and must say why it looks plain rather than producing gradients in
silence. Implemented as warnings carried on the pipeline's result
(`PipelineOutcome.warnings`) rather than printed, so both front ends deliver the
same honesty (D-102 anticipated this).

### D-114 · The API is a second front end, not a second pipeline

The sequence — transcribe, segment, match, cards, highlights, render — lived
inside the `make` CLI command, tangled with Rich progress bars and
`typer.Exit`. A web API could not call it without importing Typer and catching
exit exceptions, so the obvious path was to reimplement the sequence in the API.

That path ends with two pipelines that drift, and the drift is silent: a flag
added to one simply never appears in the other. So the sequence moved to
`voxframe.jobs.pipeline` with exactly two changes — **progress is a callback**
rather than a console, and **failures raise** rather than printing and exiting.
The order of operations and the decisions recorded in the plan are untouched.

Three tests hold the line, in the spirit of the subprocess guard (D-020):

- Every `JobOptions` field must be reachable from the web app's request model,
  or listed as internal with a stated reason.
- An AST check fails if `voxframe/api/app.py` calls `build_plan`,
  `insert_cards`, `segment_transcript` or either renderer directly.
- Both front ends report the same stages, from one definition.

### D-115 · Local security: what each defence actually stops

A server on localhost sounds safer than it is. It is reachable by **every page
the user visits** while it runs, and by every other process on the machine. Four
defences, each cheap, each load-bearing, each tested:

**Bind 127.0.0.1.** `0.0.0.0` is reachable from the local network — a café, a
shared office. A non-loopback bind is refused unless `--allow-remote` is typed
deliberately.

**Validate the `Host` header.** Loopback binding alone does **not** stop DNS
rebinding: the attacker's page resolves their domain to 127.0.0.1, the browser
sends the request to our server, and an `Origin` check passes because it is
their own origin. The defence is to refuse any `Host` that is not a loopback
name, because a rebound request carries the attacker's hostname. The check runs
**before** the token check, so a rebinding attack that has somehow learned the
token is still refused.

*Verified against the real server over real HTTP*, with a client that forges the
header without resolving it: `attacker.example.com` → 400, even carrying a valid
token; `localhost:8791` → 200. This also means the Starlette test client's
default `Host: testserver` is refused, which is the guard working — every test
client is built against a loopback base URL.

**A per-session token**, generated at launch, never written to disk, compared
with `compare_digest`. Other local processes can reach the port but cannot read
the terminal. It is middleware rather than a per-route dependency, because a
route added later without the dependency would be unprotected and nobody would
notice; a test walks every route and asserts each returns 401 without it.

**Confine file access.** Every client-supplied path is resolved — symlinks
first, because a link inside an allowed directory can point outside it, so
containment cannot be a string prefix test — and required to sit inside the job
root or the library. A client never sends a path anyway: it uploads bytes and
gets an opaque id, and downloads name an artifact the job published.

Two things found by writing the tests rather than the code. The 403 for a
rejected path **echoed the absolute path back to the client**, handing a caller
the machine's layout; it now says only that the file is outside the allowed
directories, and the path goes to the log. And the uploaded filename is used
only for its suffix and for display — never as a path component — so
`../../evil.wav` cannot escape, which is asserted rather than assumed.

None of this makes the app safe to expose publicly, and it is not meant to.

### D-116 · Sourcing consent, and where a browser user's keys live

Sourcing is the one feature that sends anything off this machine, so it is a
decision the user makes knowingly rather than a default they discover
afterwards. Asked once, in plain language, remembered, and changeable in
Settings (owner's decision 3).

**Three consent states, not two.** `None` means *not yet asked*, which has to be
distinguishable from `False` (*asked, declined*). Collapse them and the screen
either never appears or appears on every launch. The test suite pins all three.

**Consent alone does not enable sourcing.** Without a key the only adapter is
the keyless one, which Phase 4 measured as insufficient on its own (D-077), so
`sourcing_enabled` requires consent **and** a key. Otherwise a user says yes,
gets gradients anyway, and concludes the feature is broken.

**Where keys live.** The CLI reads them from the environment and `.env`, which
is right for a terminal and useless in a browser: a non-developer has no `.env`
and no way to make one. The web app writes to the platform's normal per-user
config location instead — `%APPDATA%/voxframe` on Windows,
`~/Library/Application Support/voxframe` on macOS, `$XDG_CONFIG_HOME/voxframe`
otherwise — and **never the repository**. A test asserts that path resolves
outside the working tree.

**The environment wins.** A key already set through the environment or `.env` is
never overridden by one typed into a browser months earlier, and the UI disables
that field and says where the value comes from.

**A stored key is never sent back.** The settings route returns a mask
(`…wXyZ`), so a key can be replaced but not read — the contract a password
field has.

**The "check key" button exists** because a key pasted with a trailing space, or
one since revoked, otherwise announces itself as a video full of gradients an
hour later. One minimal request, answering only *does this work*.

### D-117 · The browser's security surface is not the API's

Step 1 secured the API against other *processes*. A browser adds a credential
that lives inside a page, and other pages that would like to use it (owner's
decision 2).

**The launch token must not stay in the URL.** It arrives in the query string
because on the first navigation nothing has run in the page that could set a
header. Left there, it sits in the address bar, in history, in any bookmark, and
in any screenshot of the window. So the page exchanges it once at
`POST /api/session` and removes it with `history.replaceState`. The strip
happens in a `finally`: a token that failed to exchange is still a secret and
still does not belong in the address bar.

**The cookie that replaces it** is `HttpOnly` (no script can read it, including
an injected one), `SameSite=Strict` (a request originating from any other site
carries no credential at all — a second defence independent of the Origin
check), and session-scoped. `Secure` is deliberately **not** set: the app is
plain HTTP on loopback, and a Secure cookie would simply never be stored.

**No CORS middleware, deliberately.** Without `Access-Control-Allow-Origin` a
browser already refuses to let another origin read a response; adding CORS could
only loosen that. A test asserts the header is absent, so nobody adds it "to
fix" a cross-origin request that ought to fail. A request carrying a foreign
`Origin` is refused outright, **even holding a valid cookie**.

**Response headers**, each closing a named door: `Content-Security-Policy`
confines the page to its own origin with no `'unsafe-inline'` for scripts and
`frame-ancestors 'none'`; `Referrer-Policy: no-referrer` stops the first URL —
which carries the token — reaching anywhere else; `X-Content-Type-Options:
nosniff` stops an uploaded file being guessed into a script; `X-Frame-Options:
DENY` leaves a clickjacking overlay nothing to cover. They are attached to
**every** response including failures, because a 401 is still something a
browser acts on.

**The built frontend is committed**, like the bundled fonts (D-024): `pip
install voxframe[web]` must work on a machine with no Node toolchain. The
catch-all route serves exactly one known file and ignores the requested path
entirely, so a client-side route survives a reload without a path from a client
ever selecting a file.

### D-118 · Three leaks the tests found, not the review

All three were written correctly and were wrong anyway. Recorded together
because they share a shape: **a defence that pattern-matches what it expects,
against input that did not read the plan.**

**1. Tests were reading the developer's real `.env`.** `Settings` loads it by
design, so any test constructing one inherited the owner's live Pexels key —
which then appeared in assertion output, and would have appeared in CI logs.
Found when a failing assertion printed the key. Fixed with an autouse fixture
that masks every secret environment variable and redirects the config directory;
autouse because opting in per test is a rule someone will forget.

**2. The key-check route echoed the key back.** The redactor knows the shapes of
secrets in URLs and headers, but an upstream library can quote the key verbatim
in a bare error message, and **no pattern reliably recognises an arbitrary
key**. In that route the exact value is known, so it is now removed by identity
first and passed through the redactor second. Two passes because they catch
different things.

**3. The secret scanner flagged `package-lock.json`.** 127 npm Subresource
Integrity hashes, which are base64 and therefore key-shaped. The fix excises
integrity *values* before scanning rather than exempting the lockfile, because
base64 contains `/` and `+` which break `\b` mid-hash and leave fragments that
look like fresh tokens. Verified both ways: the clean lockfile scans to zero,
and a real key pasted into it is still caught. A guard narrowed by exempting a
whole file would have stopped guarding it.

### D-119 · The guard locked out the page that was supposed to authenticate

The first run in a real browser rendered **nothing**. A blank page, title
correct, body empty, and three console errors:

```
401 (Unauthorized)                       /assets/index-*.js
Refused to apply style ... MIME type ('application/json')
```

The token guard covered every path, including the static bundle. But a browser
fetches the page and its assets from URLs **it constructs itself**, before a
single line of our JavaScript has run — so those requests carry no token and no
cookie. The script that would exchange the token for a cookie was itself behind
the token check. Nothing could ever authenticate, because the code that does the
authenticating never loaded.

The stylesheet error is the same fault wearing a different hat: the 401 body is
JSON, so strict MIME checking refused it as a stylesheet. Two symptoms, one
cause.

**Fixed** by serving the app shell — `/`, `/assets/*`, and any non-`/api` path —
without a credential. That is safe because the shell is the same bytes for every
user and contains no data: one HTML file, one JS bundle, one stylesheet.
Everything under `/api`, which is where all the user's data lives, still requires
one; the existing "every API route is guarded" test kept passing throughout, and
is what makes the narrowing verifiable rather than merely claimed.

**Worth recording for what it says about verification.** Every unit test passed
against this. `TestClient` presents a credential on every request because a test
author naturally writes it that way, so the one condition that mattered — *a
request made before any credential exists* — was the one no test created. It took
a real browser, which is exactly why the owner asked for one (decision 4). The
same lesson as the frame-extraction habit from Phase 1: run it, and look at what
actually happened.

### D-120 · `du` said 3.7 MB; the model was 3.2 GB

A small one, recorded because it is the fourth time in this project that a
**measurement** was wrong rather than the code (D-065, D-100, D-104).

Looking at the progress screenshot showed "Transcribing (first run downloads the
model)" on a run where the model was already cached — honest in intent,
misleading in fact, and the kind of status line a user learns to ignore. Fixed
by checking whether the model is on disk before choosing the wording.

Checking the fix, `du -sh` reported the `large-v3-turbo` cache at **3.7 MB**. I
took that as a partial download left by an interrupted attempt, wrote a size
threshold to exclude it, and explained the reasoning in a comment.

The reasoning was wrong. The HuggingFace cache stores weights as blobs and
**links** them into a snapshot directory; `du` does not follow those, so it
reports a fully-cached model as a few MB. Walking the files gives **3,243 MB**.
The model was complete, and the original presence check had been right all
along.

The size threshold stayed — it correctly rejects a directory holding metadata
and no weights, which is a real state — but the comment claiming to have
observed a 3.7 MB partial download was deleted, because it described something
that never happened.

**The habit that caught it:** the numbers disagreed with each other (a "partial"
download that nonetheless transcribed fine), so I measured a second way before
trusting the first. A comment asserting a cause I had not actually verified
would have outlived the code and misled whoever read it next.

### D-121 · The cold-load check is a test, not a script — PROJECT OWNER'S DECISION

The blank-page bug (D-119) was found by running the verification script by hand.
The owner asked for that scenario to become a **permanent** check, which means
it belongs in the suite, where it runs whether or not anyone remembers to look.

`tests/browser/test_cold_load.py` drives a real Chromium with a **fresh context
per test** — no cookie, no storage, nothing. The fresh context is the entire
point: reusing one would carry a session cookie from a previous test and
recreate exactly the state that hid the bug for a whole step.

Ten checks, each naming what it forecloses: the page is not blank, the bundle
loads, the stylesheet applies, the shell and the bundle each need no credential,
**`/api` still does**, the token is exchanged and stripped, a reload without the
token still works, and a visitor with no token at all gets an explanation rather
than a blank page.

The sixth matters as much as the rest. Without it, "serve the shell
unauthenticated" could quietly widen into "serve everything unauthenticated" and
every other test in the file would still pass.

**Verified by reintroducing the bug.** Setting `_is_app_shell` back to `False`
fails five of the ten, with the original symptom in the output
(`assets/index-*.js failed`). A regression test nobody has seen fail is a
regression test nobody should trust.

Marked `browser` and skipped cleanly when Playwright or Chromium is absent, so a
fresh clone still has a passing suite:

```
pytest tests/browser -m browser
```

One non-browser check lives with them because it belongs to the same risk: the
built bundle that `index.html` references must be the one actually shipped. A
stale bundle passes every browser test while serving code nobody reviewed.

### D-122 · Thumbnails are resolved by scene index, never by path

The filmstrip has to show what each scene chose, which means serving files from
the library — the first time the web app reads anything outside its own job
directory.

**The URL names a scene, not a file**: `/api/jobs/{id}/scenes/{n}/thumbnail`.
The alternative, mounting the library as static files, would put every path
decision in the hands of a URL. Here the index selects the asset and the server
resolves it.

**The plan is untrusted input.** It is an editable JSON file (D-011) whose whole
purpose is that a user can change it, so a path inside it deserves exactly the
treatment a path in a request gets. It is re-resolved against the sandbox
(D-115): a plan pointing at `~/.ssh/id_rsa` gets a 403 whose message does not
echo the path back.

Thumbnails are cached per job, keyed on the source file's modification time, so
replacing an image produces a new thumbnail rather than serving a stale one.
Generating one costs an FFmpeg invocation and a 148-scene filmstrip would
otherwise pay that on every page view.

**A test fixture worth remembering.** The first version asserted the thumbnail
was smaller *in bytes* than its source, and failed: the fixture used FFmpeg's
`testsrc` pattern, which compresses to 6.9 KB as PNG, so a 320px JPEG of it is
legitimately larger. The route was correct; the assertion was measuring the
wrong thing. It now asserts on **dimensions**, which is what the route actually
controls — byte size also depends on content, which the route does not.

### D-123 · The filmstrip explains empty scenes, which is why it exists

A read-only view of the scene plan could have been a JSON dump with syntax
highlighting. What makes it worth building is the question a user actually has,
which is not "what is in this file" but **"why does my video look like that"**.

So every card answers four things: what is on screen, what is said over it, how
long it lasts, and — when the answer is "nothing" — *why*. A scene with no
imagery shows its reason in the card itself ("Nothing matched closely enough",
"No library was used") rather than being a blank rectangle the viewer has to
interpret. The same honesty the CLI has always had (D-102), in the place someone
will actually look.

Read-only in step 3 deliberately. Seeing the plan is most of its value, and
building the view first means step 4's editing is added to something already
legible rather than designed blind.

Laid out horizontally and scrollable, because a plan **is** a sequence; a grid
would discard the ordering that makes it a timeline. It is an `<ol>` of buttons,
so a screen reader hears "5 scenes" and can walk them, and every card is
reachable and activatable from the keyboard.

### D-124 · A failed render sat at "running" forever — BUG

The step 3 browser check rendered a recording **with imagery** for the first
time through the web app. The render failed, and the page showed "Rendering"
for half an hour with no error.

A `py-spy` dump of the live process settled what was happening, after the
obvious guesses (an FFmpeg hang, a slow encode) were each ruled out by it: **no
FFmpeg was running, and the render worker thread was idle** — it had finished.
The job simply never recorded that it had failed.

Reproducing the store in isolation found why. The failure handler logged
*before* recording the state, and on Windows the traceback renderer raised
`UnicodeEncodeError` writing box-drawing characters to a `cp1252` stream. The
handler died inside the handler, the exception escaped into the executor, and
the job was left at "running" — persisted that way, so a restart would not have
fixed it either. It reproduced with stdout piped **or** redirected to a file,
which is every real launch path except an interactive console.

This is D-094's failure in a new place: the same encoding, a different stream.

**Fixed in three layers**, because any one alone leaves a gap:

1. **The state is recorded first**, before anything that can fail. Logging is
   diagnostics; it must never be the reason a failure goes unreported.
2. **Logging cannot raise**: the configured stream replaces unencodable
   characters, and tracebacks use the plain formatter rather than the rich one.
3. **`voxframe web` now configures logging at all.** It had been running on
   structlog's defaults — stdout, rich tracebacks — exactly the combination
   that failed.

The failure message shown to the user is the FFmpeg *error*, not the
multi-hundred-character command line in front of it, and `BaseException` is
recorded too, then re-raised when it is `KeyboardInterrupt` or `SystemExit`.

**The regression test uses a logger that fails only on errors**, as the real one
did — a first version failed on every call, including `log.info` inside
`create()`, and so tested nothing about the handler. Verified to fail against
the committed store and pass against the fix.

**The verification script had the same blind spot as the job.** It waited only
for "Your video is ready", so a failed render looked identical to a slow one
until a fifteen-minute timeout. It now waits for success *or* the error notice
and fails immediately with the message.

### D-125 · A photograph a hair off square broke every crossfade — BUG

The render that D-124 hid was itself a real bug, in the renderer, reachable
from the CLI too:

```
Input link in0:v0 parameters (size 1280x720, SAR 1843200:1843417) do not match
the corresponding output link in0:v0 parameters (1280x720, SAR 1:1)
```

The Ken Burns chain scales, crops, zooms and scales again. Each `scale`
preserves *display* aspect by adjusting the *sample* aspect ratio, so a
photograph whose proportions do not divide evenly comes out a hair off square
— here 1843200:1843417, within 0.012% of 1:1. FFmpeg's `concat` compares SAR
exactly and refuses the join.

**Why nothing caught it before:** it needs a matched photograph of awkward
proportions *and* a join that goes through `concat` rather than the stream-copy
demuxer — that is, a mix of cuts and crossfades. Every earlier web render had
no library, so every scene was a gradient; the CLI renders that had imagery
happened to use photographs whose sizes divided cleanly.

**Fixed at both levels.** Still segments end with `setsar=1` — the frame is
exactly the output size, so square pixels are simply correct, and the stream-copy
path gets them too. And the concat normalisation also applies `setsar=1`, because
every input passes through there: a cached segment, a user's clip, or a future
motion type could still arrive otherwise.

`RENDERER_VERSION` is bumped to 2 so cached non-square segments are invalidated
rather than silently reused (D-101). The rerun reported `0/9 segments reused`,
which is that working.

Verified: the render that failed now succeeds, 1906 frames against 1906 planned,
`ffprobe` reports SAR 1:1 and DAR 16:9, and the frames were looked at — the
Earth photographs are round rather than faintly stretched. Six regression tests,
all confirmed to fail against the committed renderer.

### D-126 · An empty library failed the first-run render — BUG

Owner's decision 6 says a new user with an empty library and no keys must still
get a decent first video. For a while, they got a **failed render** instead:
*"The library is empty. Ingest images first."*

The API used a library whenever its directory *existed*. But opening a library
creates its directory and database (`AssetLibrary._connect` makes both), so an
empty library is an easy state to reach — any command that touches one leaves
it behind — and the matcher refuses an empty library outright. The step 2
verification passed only because, at the time, no `./library` directory had
been created yet on this machine.

Fixed by using a library only when it **has at least one asset**, decided
without creating anything: a missing database is answered from the filesystem,
never by opening one that would then exist. An unreadable database is treated as
empty — plain backgrounds, not a failed render. The CLI keeps its error for an
empty `--library`, because there the user named the library explicitly and
telling them it is empty is the useful answer; the web app chooses one
implicitly and must not fail on its behalf.

Six tests, including the exact state that failed (a library with an empty
database) and that checking never creates one.

**A related miscount, found by looking at the screenshot.** The first-run result
said *"all 4 scenes show a plain background"* and *0 / 4 with imagery*, while
the filmstrip counted 3. The fourth was the title card, which is drawn text and
never takes a photograph. Cards are no longer counted as failed matches: the
warning, the fill rate and the result tile all count only scenes that could
carry imagery, and now agree with the filmstrip.

**This was the second time the permanent verification script failed to fail.**
Its "fail fast on an error" wait used `"text=Your video is ready, .notice-error"`,
intending two alternatives — but a comma inside a `text=` selector is part of
the text being matched, so it waited for a heading that could never exist and
timed out exactly as before. It now uses `locator.or_()`, and it caught nothing
only because, after this fix, there was nothing left to catch.

### D-127 · Near misses: shown, never chosen — PROJECT OWNER'S DECISION

A scene that falls back to a plain background now keeps the candidates that came
closest, and the scene plan offers each as **"Use this image anyway"**, with its
thumbnail shown first. Asked for by the owner as part of the first editing
slice, alongside runner-up swapping.

**What counts as near.** Within **0.05** of the threshold, so down to 0.12 on the
standard model (0.17 threshold). The number comes from the D-081 calibration:
apt matches went as low as **0.126**, and below the threshold apt and irrelevant
images *overlap* — the band where the matcher genuinely cannot tell. A person
glancing at a thumbnail can. So the band is exactly where a human's judgement
adds something, and 0.05 covers the lowest apt match measured. Up to three per
scene.

**Never chosen automatically.** Lowering the threshold would admit the
irrelevant images in the same band (57% of them at 0.14, per D-081). The point
is that the person decides, image by image.

Two refinements:

- A candidate the no-repeat rule bars (D-087) is **not** offered: suggesting an
  image the viewer saw a moment ago is suggesting a repeat.
- A scene left plain because its only candidates were images of printed text
  (D-082) offers *those*, since they did clear the similarity bar. The person
  may know the caption will not clash.

Recorded in the plan as `near_misses`, each with its similarity, so the choice
survives a reload and the Details section can say how near it was. Plans written
before this have none and load unchanged.

### D-128 · A person's choice is theirs

Editing arrives, and with it one rule every part of it keeps: **once a person has
chosen a scene's image, nothing automatic replaces it.**

- An edited scene is marked `asset_source: "user"`.
- **Re-rendering renders the plan as it stands and never re-matches.** A separate
  headless path (`render_plan`) exists for this, and a test fails if a re-render
  runs the full pipeline. Re-matching would silently undo the edits just made.
- Resuming a job that already has a plan resumes *from the plan*, for the same
  reason.
- A scene the person deliberately cleared is not reported as "found no suitable
  image" — that would describe their choice as a failure.

**Every edit is undoable the way it was made.** The image a swap replaces becomes
a runner-up, so choosing it back is one click. An edit that cannot be reversed as
easily as it was made is one people hesitate to try.

**An edit can only pick an image the plan already recorded.** The request names
an asset id; the server looks it up among that scene's runners-up and near
misses. No edit accepts a path for an existing asset (D-115). "Use your own
photo" is the one way to bring in a new file, and it is uploaded bytes, stored
under a name the server chooses, **verified as a real image** before it is
accepted — a file of any kind renamed `.png` would otherwise reach FFmpeg — with
provenance defaulting to the person's own work and never blank (D-035, owner's
decision 4).

**Only changed scenes are rendered again.** Segments are cached by their inputs,
not their position (D-101), so a one-scene edit re-renders that scene.

**A latent bug the editing would have exposed.** The thumbnail cache claimed to be
keyed on the source file's identity but was keyed on scene index and modification
time. Stock images downloaded in the same second share an mtime, so after a swap
the filmstrip would have shown the scene's **old** picture. It is now keyed on the
resolved path, size and nanosecond mtime, and the browser's own cache is defeated
by varying the URL with the asset id. A test reproduces the collision.

**Plain language first** (owner's decision 2). The scene panel opens with a
sentence and the actions that apply to that scene. Scores, thresholds, queries,
the camera move and frame numbers are under a collapsed **Details** — a native
`<details>` element, so it is keyboard- and screen-reader-accessible without any
script. Search is not offered yet: a button that does nothing is worse than no
button, and search arrives in a later slice.

### D-129 · No job stays "running" without a worker — PROJECT OWNER'S DECISION

D-124 fixed one way a job could be left at "running" forever. The owner asked
for the class of failure to be closed, not just the instance: on startup, and
periodically while running, any job marked running without a live worker
becomes **interrupted**, with a resume option and a clear message.

**A new state, not a kind of failure.** `INTERRUPTED` means the render did not
fail and nobody stopped it — the process running it went away. Nothing is wrong
with the job, so it is offered for resuming rather than reported as an error.
It is terminal (no worker will touch it) and resumable.

**Three places it is detected:**

1. **On startup**, a job recorded as running or queued cannot still be — the
   process that ran it is gone.
2. **Every ten seconds**, a background check marks any RUNNING job whose worker
   has finished, or never existed, as interrupted.
3. **On every read**, the same check runs first, so the state a page sees is
   always honest even between checks.

**The rule has to be exact, and the first version was not.** A job is created
QUEUED *before* a worker is attached to it, so "queued with no worker" is the
normal state of a job being submitted. Reaping it would have interrupted brand-new
jobs whenever a check landed between the two calls. The rule is therefore:
RUNNING with a missing or finished worker, or QUEUED with a *finished* one — and
five tests pin that a healthy job, a job being submitted, and a job waiting
behind another are all left alone.

It is safe to race with a worker that is finishing: a worker is only *finished*
once its function has returned, and the function records the outcome before
returning.

**The test the owner asked for kills a real worker.** A Python thread cannot be
killed from outside, so the honest version kills the **process** running the
render — a hard kill, no handler, no `finally` — after checking that the job is
recorded on disk as running. A new store then sees it as interrupted, and still
does after being reopened twice. The browser verification does the same to a real
server mid-render, restarts it, and resumes the video from the page.

**Resuming keeps the same job**, so a page following it keeps following it, and
a failed job can be retried the same way. A progress stream attached to a job that
gets reaped is notified at once rather than waiting for its next keepalive.

### D-130 · Caption correction in the browser: the Phase 3 path, reused

Misheard words are the most common thing a person will want to fix, so the scene
panel has a direct **Edit caption** button rather than burying it.

No new correction logic was written. The browser sets the scene's
`caption_text`, and the renderer applies it exactly as it has since Phase 3
(D-062): word timings are re-derived from the originals, including when one
heard word becomes two or two become one. The original `text` is never
overwritten, so the panel always shows what was actually heard beside the
correction, and **Use what was heard** restores it — which is simply the same
edit with the original text, since typing back exactly what was heard removes
the correction.

Refused: an empty caption (almost always an accident, and blanking a scene would
be worse), anything over 2,000 characters (a paste gone wrong, which would be
spread across the scene's timings until unreadable), and cards, which have no
speech.

**Verified where it matters, not only in the plan.** The browser check corrects
"people goals" to "people's goals", updates the video, and confirms the
correction in the exported subtitles *and* in a frame extracted from the video:
the burned-in caption reads "people's goals", with the apostrophe intact — the
character that broke FFmpeg filters twice before (D-021, D-106).

A caption edit reuses every image segment: captions are burned in a pass over
the joined video, so a caption-only change renders no scene again.

### D-131 · Messages say only what works today — PROJECT OWNER'S DECISION

The first-run message told people to *"add your own images, or turn on imagery
sourcing in Settings"*. Neither worked in the app: the Library screen is not
built, and the sourcing switch was saved but no render used it. A new user
following the advice would flip a switch, render again, get the same plain
backgrounds, and reasonably conclude the tool was broken. The advice arrived
before the features it pointed to — an ordering mistake, owned.

The owner's decision: describe only what works today, and restore the fuller
advice once sourcing and the Library screen actually work.

Five places carried it, not one, and all changed together: the upload screen,
the settings screen, the scene plan, the pipeline's result warnings, and the
Settings toggle — the worst of them, which said *"On. Scenes without a local
match will try Openverse"*, a flat statement of something that did not happen.
Each now points only at "add your own photo to any scene", which does work.

**Every change is tagged `D-131` in the code**, so restoring them is one search.
A test fails if any result warning recommends sourcing or adding to the library;
it is to be removed, deliberately, when both land.

### D-132 · Renders search online when allowed — and what that is worth, measured

The owner's next slice: make renders actually use the sourcing setting, before
building the search UI, because it has the biggest effect on a new user's first
video.

**What a render now does**, when the person has consented and a key is
configured: match against the library; for scenes still without an image,
search Pexels, Pixabay and Openverse with the scene's keywords; download into
`<library>/sourced` (where the files stay, D-074); add them to the library with
their per-file licences (D-072); and re-match. The same steps as `voxframe
source` followed by `make`, in one go and reusing that code.

- **Decided by the server, never the request.** A page cannot switch on network
  access the person did not agree to. The parity test lists `source_imagery` as
  server-decided, with that reason.
- **Failures are reported, never raised.** Offline, or a revoked key: the video
  is still made, with a sentence naming the source that failed and not its
  error text, which can quote a request URL carrying a key (D-075).
- **Capped at 40 scenes per render**, because Pexels allows 200 requests an hour
  and a long lecture could spend a whole hour's quota on one video. The rest
  keep plain backgrounds, and the warning says so.

**A bug found on the way in.** "Enabled" required consent *and a key saved in
the app*. Keys in the environment did not count, so the owner — whose keys are
in `.env` — could switch sourcing on and have nothing happen. It now counts a
key from anywhere, and a test pins the environment case. A second, subtler one:
the result warning was given the library the caller *passed*, not the one the
render *used*, so a video whose scenes had just been filled from the web would
have been told "your image library is empty".

**The measurement.** Each recording rendered twice from a genuinely empty
library — a brand-new user — with sourcing off and then on, against the live
services:

| recording | scenes | off | on | extra time |
|---|---|---|---|---|
| Recording (3).m4a | 8 | 0 | **1 (12%)** | +147 s |
| en_sonnet_january_45s | 5 | 0 | **3 (60%)** | +100 s |
| fr_fable_cigale_45s | 5 | 0 | **2 (40%)** | +107 s |
| **total** | **18** | **0** | **6 (33%)** | |

**33%, not the 84% Phase 4 reported (D-081), and this is the honest number.**
Phase 4's libraries were built over several sourcing rounds, and the 0.17
threshold was tuned on those same images — the risk D-083 flagged. A first video
from an empty library is the case that matters for a new user, and it had never
been measured.

Why the owner's recording fills so little, from its plan: three scenes had
candidates *above* the similarity bar (0.19–0.21) that were rejected as images
of printed text — the talk is about God calling a man, and stock sites answer
those keywords with Bible pages and scripture signs — and four fell just below
it, one by 0.002. **Six of its seven plain scenes offer close matches**, so in
the app they are one click from an image; the 33% is what happens with no
clicks at all.

Not changed in response, deliberately: the threshold. D-081 measured that
lowering it admits irrelevant images at a high rate. The decisions this
number raises are the owner's, and are proposed in the step report.

### D-133 · Printed text, detected by looking — measured, no OCR needed

The owner's instruction: build the text-image check as part of step 4, starting
with zero-shot classification on the embedding model already loaded, measured
on a small labelled set including "100%", "ACHIEVE" and "EARTH"; propose OCR
only if that cannot separate them acceptably. It can.

**Why it was needed.** The original detector (D-082) read an image's tags, and
in a real sourced library every tag was just the source's name — it flagged
nothing. Meanwhile images of printed text are *common* among close matches,
because the matcher penalises them: the verification's own one-click edit put
"100%" under the captions.

**The labelled set.** 121 images from the three Phase 4 libraries, labelled by
eye from contact sheets **before any score was seen**: 16 *text* (printed words
are the subject), 17 *incidental* (book pages, shelves, signage — reported
separately so they can neither flatter nor sink the result), 88 *none*. Kept in
`tests/eval/text_images.csv`, keyed by content hash.

**The method.** Each image's embedding is compared with five prompts describing
printed text ("a photo of text", "a sign with words", "a document or
screenshot", "large printed letters", "a word spelled out") and six describing
ordinary photographs; the softmax share on the text side, at CLIP's learned
scale of 100, is the score. The varied negative side matters: with only "a
photo" opposite, "text" wins by default on anything unusual.

| model | AUC | threshold | text caught | photos flagged | 100% | EARTH | ACHIEVE |
|---|---|---|---|---|---|---|---|
| standard | 0.967 | 0.7 | **12/16** | **0/88** | 0.940 | 0.999 | 0.997 |
| lite | 0.932 | 0.5 | **9/16** | **0/88** | 0.953 | 0.970 | 0.996 |

Each threshold is the lowest with no false alarms. **Per model**, because they
score differently — the D-089 lesson — and a model without a measured
threshold flags nothing rather than guessing.

The four the standard model misses are images where text is not the dominant
subject: a meme's caption strip under a photo, a rotated slogan on a graphic, a
disc of tiny type, and a slide behind a speaker. The highest-scoring ordinary
photo is a hand holding a phone at 0.688, under the bar; the next, book spines
at 0.608, arguably belonged in *incidental*.

**No OCR.** The owner's bar was "separate them acceptably"; this does, at zero
false alarms, with no new dependency, no download, and no image loaded again:
the check reads the vector already stored at ingest. That was verified, not
assumed — stored and freshly computed vectors give identical scores to three
decimal places.

**How it is used.** Every image a plan records — the chosen one, runners-up,
close matches — is flagged `prints_text`. The scene plan says so in plain
words ("Shows printed text, which can clash with the captions") and lists such
images after clean ones, so the one-click choice avoids them when anything else
exists. On the verification recording the offer for scene 2 now leads with a
seedling and puts "100%" last.

**Deliberately not done: using it in automatic matching.** D-082's penalty is
meant to keep printed text out of automatic choices, and with a detector that
works it would start doing so — which would lower the fill rate D-132 just
measured at 33%. On the owner's recording it would, for instance, reject
"#GOD IS LOVE" for "when God calls a man", which D-081 judged apt. That trade
belongs to the owner, and is proposed in the step report.

A regression test re-runs the measurement for both models and fails if a named
image stops being caught, any ordinary photo is flagged, or recall drops.

### D-134 · The printed-text check, applied in matching — PROJECT OWNER'S DECISION

The owner's decision: apply the visual text check (D-133) in automatic matching
as a **penalty, not a hard rejection**, so printed-text images are avoided in
automatic choices but remain available as close-match offers.

D-082's penalty already had that shape; it lacked a detector that could see.
The matcher now penalises a candidate the stored-vector check flags as well as
one whose tags say so. A clean candidate wins; a text image that is the only
one drops below zero, the scene falls back to plain, and the image stays in the
scene's close matches for a person to choose. Verdicts are cached per asset,
since a candidate recurs across scenes.

**Measured on the same 18 scenes**, same harness, from an empty library with
search on (the "before" run stashed the change and reproduced D-132's 6/18
exactly):

| | filled | good (score ≥ 0.25) | plain with close matches |
|---|---|---|---|
| before | 6 (33%) | 4 of 6 | 11 |
| after | **4 (22%)** | **2 of 4** | **14** |

**Both matches lost were images of printed text** — text scores 0.998 ("people
goals", the owner's recording) and 0.992 (the French clip's LibriVox credit). No
clean match was lost. The "good" count fell with them because it is a score
proxy (D-069): it cannot see that a picture of words clashes with captions,
which is exactly what the penalty is for. Both remain one click away.

Noticed while checking: in the sonnet clip one video clip fills three of five
scenes. That is the owner-approved D-087 exception — a repeat beats a plain
background when nothing else clears the bar, and it is logged.

### D-135 · Search terms from abstract speech: modals, contractions, bare verbs

The owner's diagnosis was exact: "need able" meant modal and auxiliary
constructions were not being filtered. Three causes, all fixed:

1. **Contractions were split.** The word pattern broke "don't" into "don" and
   "t"; "t" was too short and dropped, "don" survived as a search term. Tokens
   now keep an internal apostrophe (straight or curly), and are resolved:
   English negated auxiliaries are dropped whole, other English contractions
   keep their base ("people's" → "people", "you're" → "you", then judged as a
   stopword), French elisions keep what follows ("l'eau" → "eau").
2. **Modals and semi-modals were not stopwords** — "need", "able", "ought",
   "gonna"; the negated auxiliaries as bare words, for transcripts that split
   them already; and French "peut", "doit", "faut", "veut", "besoin" and the
   rest of their forms. URL fragments ("org", "com") too.
3. **Common base-form verbs** the suffix rules cannot see — "identify", "leave",
   "call", "assist", "accomplish", "execute", "read"; French "dit", "renseigner",
   "participer"; and the French "-ez" form, so "rendez" is not a subject.

On the owner's recording the queries went from `need / able / identify`,
`don / identify / leave` to `goal / earth`, `life / earth`; subjects such as
"God", "meaningful life" and "dominion" survive. 29 tests use the owner's
sentences verbatim. One cost, left alone: "wasted life" becomes "life", because
"wasted" meets the "-ed" verb rule, and loosening that rule would let true verbs
back in.

**Measured, same 18 scenes:**

| round | filled | good (proxy) | plain with close matches |
|---|---|---|---|
| after D-134 | 4 (22%) | 2 of 4 | 14 |
| **after D-135** | **9 (50%)** | **7 of 9** | **9** |

**And by eye**, because the proxy is only a score: **6 of the 9 are apt** — a
praying silhouette for "when God calls a man", a seedling in cupped hands for
"identify our purpose", a snowy tree for the sonnet's winter lines, a child
walking to a house for the ant's neighbour. Two are loose (books for a LibriVox
credit; a cow drinking for a mistranscribed line). **One is wrong: two foxes for
"read by Fox in the Stars"**, a reader's name taken literally — and the proxy
scored it highest of all, 0.47. The proxy counts it good; it is not.

The snowy tree fills three of the sonnet's five scenes, the D-087 exception.

### D-136 · A visual-metaphor dictionary for abstract speech — PROJECT OWNER'S DECISION

Speech about purpose, faith or growth names nothing a camera can photograph. The
owner asked for a small offline dictionary of visual metaphors — twelve themes,
English and French, each mapped to a few concrete queries — used when a scene's
search terms are abstract, and kept as a plain data file contributors can extend.

**The file** is `src/voxframe/match/metaphors.toml`: TOML, because it is
readable, takes comments, and is parsed by the standard library, so nothing new
is installed. Each theme lists the words that trigger it in each language and
three English queries (stock sites index in English; the matching model reads
both). An `[abstract]` list names words that carry a theme but depict nothing
("life", "earth", "place"). The file is checked on load: a theme without
queries or a mis-shaped list fails a test rather than silently vanishing. No
query asks for printed words, since captions are drawn over the image (D-082),
and a test holds that.

**Two rules keep it honest:**

- **Only for abstract scenes** — at least half of a scene's search terms must be
  abstract or theme words. "Coral reefs", and the sonnet's frozen winter, are
  left alone.
- **Never ahead of a literal match.** The matcher tries the scene's own words
  first and reaches for a metaphor only when they find nothing; the praying
  silhouette for "when God calls a man" stays. Each metaphor query is ranked on
  its own — averaging "summit", "lighthouse" and "finish line" into one vector
  would describe none of them — under the same threshold, penalties and
  repetition rules. Sourcing searches a metaphor as a *second* pass for abstract
  scenes, because its query chain stops at the first result: last, it would
  never run; first, it would stop the literal search. Each download keeps the
  query that actually found it, for its provenance.

A metaphor is recorded as one ("visual metaphor 'lighthouse at dusk'") and the
scene plan says so in plain words, so it is never taken for a literal match.

One entry was wrong on first look and removed: "identify" as a *learning* word
made "identify what you are called to do… your goal" lead with "person
reading outdoors".

**Measured, same 18 scenes:**

| round | filled | good (proxy) | apt by eye |
|---|---|---|---|
| after D-135 | 9 (50%) | 7 of 9 | 6 of 9 |
| **after D-136** | **15 (83%)** | **13 of 15** | **12 of 15** |

The owner's recording went from 2 of 8 to **8 of 8, every one apt by eye**: six
through metaphors — snow-capped peaks for "big goals", runners breaking the tape
for "accomplish a goal", a sunset over a ridge for "why are you on earth", a
storm at sea for "a wasted life", lighthouses for "purpose" and "what you are
called to do" — and the two literal matches kept. The two LibriVox clips were
unchanged: their speech is concrete, so the dictionary rightly did nothing.
Still wrong: the foxes for a reader called Fox.

### D-137 · The atmospheric fallback — PROJECT OWNER'S DECISION

When a scene still finds nothing, it now shows a calm, neutral image on the
whole recording's theme instead of a plain background: labelled "atmospheric"
in the plan and the scene plan so it is never taken for a match, bound by the
repetition rules, and switchable to plain with one click.

**The theme, from the whole transcript:** first, metaphor themes (D-136) that
recur across scenes; otherwise concrete words the recording keeps returning to.
Two details decided by looking at real transcripts:

- **Counted by scenes, not by occurrences, and a theme must recur in at least
  two.** The first version gave the La Fontaine fable the theme
  "enregistrement" — its LibriVox preamble says "recording" twice in one
  sentence — and would have filled the fable with a photograph of a studio.
  Counting scenes gives "fourmi", the ant. If nothing recurs, the scenes stay
  plain: an honest gap beats a guess.
- **Names are never themes** (a reader called Fox became foxes, D-135), and
  archaic pronouns ("thy", "thou", "dost") are now stopwords — the sonnet's
  first theme was "thy".

The winter sonnet's theme is "fire" ("heart of fire", "no fires can burn").

**The image** must clear the normal similarity threshold against the theme,
styled as "calm quiet photograph of …"; is never one already in the video,
and two plain scenes get two different images; is never an image of printed
text; and leaves a scene a person cleared alone. Close matches are kept, so the
person can still choose one. When searching is on, the theme is searched once
so the images exist. The result screen says how many scenes are atmospheric.

**Measured, same 18 scenes:**

| round | matched | good (proxy) | atmospheric | plain |
|---|---|---|---|---|
| after D-136 | 15 (83%) | 13 | 0 | 3 |
| **after D-137** | **16 (89%)** | **14** | **2** | **0** |

Every scene now shows an image. One confound, stated plainly: the sonnet's
"Her roses to forego…" became a *literal* match ("roses"), from the archaic-
pronoun stopwords improving its search terms, not from the fallback. The
fallback itself filled the fable's two plain scenes — an ant on a stem, ants
on a leaf — both calm and on theme; the second is nearly a literal fit for "la
Cigale est la fourmi".

By eye, the matches are 13 apt of 16; the new roses image carries a small
"Heart's Desire" ticket the text check did not flag, the kind of miss its
evaluation predicted (text present but not the subject, D-133).

### D-138 · The result screen says when plain scenes are one click from an image — PROJECT OWNER'S DECISION

**Context.** Owner decision 2d: tell people when plain scenes are one click
from filled. A plain scene with close matches (D-127) is exactly that: "Use
this image anyway" in the scene plan fills it. Until now the only way to find
out was to open the scene plan and look.

**Decision.** The job summary gains `close_match_scenes` (plain, not a card,
with at least one close match) and `atmospheric_scenes`. When the first is
above zero, the result screen shows an information notice — "3 plain scenes
are one click from an image. Nothing matched closely enough to use
automatically, but close matches were found." — with a "Show me" link that
opens the scene plan, which already opens on the first such scene.

It is an information notice, not a warning: it is the fastest improvement
available, not a problem. Atmospheric scenes get no second notice; the
pipeline's warning (D-137) already says how many there are and how to switch
them to plain, and saying it twice made four boxes above the video. That
warning's grammar for a single scene was fixed at the same time ("1 scene …
shows a calm image … You can switch it").

**Verified** in the browser on the winter sonnet against the Phase 4 library:
3 plain scenes, all with close matches, and the notice says so
(`phase-8-17-one-click.png`). The owner's own recording no longer serves for
this check: since D-136 it fills all 8 of its scenes from that library, so it
has no plain scenes and the notice, correctly, does not appear.

### D-139 · Web addresses never reach a search — PROJECT OWNER'S DECISION

**Context.** The sonnet's credit line, "read for Librevox .org", searched for
"Librevox" and matched an image for it. Owner decision: strip URLs, domain
names and anything ending in .org/.com/.net from search in all languages,
rather than special-casing LibriVox.

**Decision.** `strip_web_addresses` removes, in any language: `http(s)://…`
and `www.…`; email addresses; a name followed by a domain ending, however the
transcriber spaced it ("librivox.org", "Librevox .org", "example.co.uk/path");
and spoken forms ("example dot com", "librivox point org"). The name in front
goes too — "Librevox" is a website, not a subject.

- A space is allowed *before* the dot, never after, so a sentence break is
  never read as a domain: "la fin. De plus" keeps "De", "the ocean. Org
  charts" keeps "Org".
- Spoken forms use only unambiguous endings (com, org, net, edu, gov, info,
  io, fr): "point de vue" is French, not a domain.
- The removed address ends a phrase, so the words either side never join into
  one query.
- It applies to the extracted queries **and to the sentence the matcher
  embeds** — matching uses the whole sentence (D-051), so stripping only the
  queries would have left "Librevox .org" in the vector.

**Found and not fixed:** with "Librevox" gone, the same line matched a fox for
the reader called Fox. Names in reader credits ("read by", "lu par") are the
next source of wrong matches; not changed without the owner's say.

### D-140 · A forced repeat is the last resort, with a hard cap — PROJECT OWNER'S DECISION

**Context.** Since D-087 the matcher waived the no-repeat rule when a scene's
only candidates above the threshold were already in the video: "a repeat
beats a gradient". On the owner's recording against the Phase 4 library, one
summit photograph appeared in three of eight scenes. The measurement had also
been counting those forced repeats as filled scenes: two of round 2c's 16
(D-137) were waivers at 0.177 and 0.179.

**Owner decision.** When the preferred image is barred, try in order: more
candidates for that scene (if search is on), a different metaphor query, an
atmospheric image, a plain background, and only then a repeat. No asset more
than twice in a video, never in adjacent or near-adjacent scenes.

**Decision.**

- **The waiver is gone.** A scene whose only matches are barred is left
  unmatched, reason "its best match is already used in this video". The
  existing pipeline then gives it the steps in the owner's order: it counts as
  missing imagery so search fetches for it (D-132); the metaphor pass tries
  its other metaphor queries (D-136); the atmospheric fallback may fill it
  (D-137); otherwise it is plain.
- **The repeat itself is offered, never taken**: it appears among the scene's
  close matches — "Use this image anyway", noted "Already in scene N" — so it is
  one click away, which is what "only then a repeat" means once a plain
  background is always available.
- **Hard cap**: no asset is chosen or offered a third time; and two uses must be
  at least `min_repeat_gap` = 3 scenes apart (scenes 4 and 7 may share an image,
  4 and 6 may not), counted in scenes, plain ones included. The D-087 window
  (8, or the whole video under 12 scenes) is unchanged and still applies.
- **A person's own choice is not capped.** Choosing an image in the scene plan
  is the person's decision (D-128); the filmstrip shows where else it appears.
  Open to the owner to change.
- The scene panel says it plainly: "The best match is already shown elsewhere
  in this video, so this scene was left plain rather than repeat it."

**Measured**, same 18 scenes, fresh empty library, search on:

| round | matched | good | forced repeats | atmospheric | plain |
|---|---|---|---|---|---|
| 2c (D-137), recounted | 14 | 14 | 2 | 2 | 0 |
| **D-139 + D-140** | **13 (72%)** | **13** | **0** | **5** | **0** |

The sonnet's two forced repeats became atmospheric fire images; the credit
lines of both LibriVox recordings lost their "Librevox"/"Librivox" matches
(one became atmospheric, one matched the reader's name, Fox). Every scene
still shows an image; none repeats. By eye: 11 of the 13 matches apt (the fox
is wrong; "Pas un seul petit morceau" got a boy by a house), all 5
atmospheric on theme (fire, ants).

**The owner's recording against the Phase 4 library**, search off: before, 8
of 8 filled with the summit three times; now 6 of 8, no repeats, and the two
scenes the summit filled are plain with close matches offered (one of them a
repeat offer, one the "EARTH" tiles). The atmospheric fallback had nothing
unused on the theme to give them; that library is small.

### D-141 · Names in reader credits never reach a search — PROJECT OWNER'S DECISION

**Context.** With the web address gone (D-139), the sonnet's "read for
Librevox .org by Fox in the Stars" matched a photograph of a fox. Owner
decision: leave names in credit lines out of search, the same way as web
addresses — "read by", "lu par", "recorded by", "narrated by", "enregistré
par" — and only in those patterns, not person names in general.

**Decision.** `strip_credit_names` removes a credit from its verb to the end
of the name: read / recorded / narrated / performed / voiced **by**, and lu /
enregistré / raconté / narré / interprété **par** (with their feminine and
plural forms). An optional "for …" between verb and "by" is part of the credit
and goes too, since LibriVox writes "read for LibriVox.org by …".

- A name is capitalised words joined by the small words names contain ("Fox
  in the Stars", "Jean de la Fontaine"); "in" joins only as "in the", so
  "narrated by Jane Smith in London" keeps London.
- A credit needs a capitalised name after it: "read by the fire" and "read by
  candlelight" are untouched.
- A person named anywhere else stays: "Helen Hunt Jackson wrote about
  January", "Fox ran across the field".
- Addresses are removed first, leaving a break, so the LibriVox form is still
  recognised as a credit once ".org" has gone. Applied to queries and the
  embedded sentence alike (D-139).

**Measured** on the LibriVox samples: the sonnet's credit scene became
atmospheric instead of a fox; the 18-scene totals are unchanged at 13 matched,
5 atmospheric, 0 repeats, 0 plain.

### D-142 · Searching online for one scene, by hand — PROJECT OWNER'S DECISION

**Context.** Item 5 of the owner's plan: the search screen, when online search
is enabled.

**Decision.** In the scene plan, a scene gets a "Search online" button — only
when the person has agreed to online search and a key is set, the same rule as
automatic sourcing (D-116), so the setting means what it says.

- The box starts with the scene's own first search term, so the first search
  is one click. Results come from every configured service, interleaved, 12 at
  a time, images only.
- **Each result shows its author, source and licence before it is chosen**,
  because that is what the credits will say.
- **The browser never sees or sends a URL.** Results are remembered on the
  server per job under random tokens (the most recent 120), so a page cannot
  make the server fetch an address of its choosing. A token from another job
  is a 404.
- **Previews come through the app**, fetched and re-encoded to a small JPEG
  by the server, because the page's content policy admits images from the app
  alone, and so that what reaches the browser is an image the server wrote.
- **Nothing is downloaded in full until the person chooses.** The chosen
  image is kept with the job, beside a provenance record naming the query
  that found it, and put in the scene as the person's choice (asset source
  "user", D-128). It reaches the video at "Update the video", like any edit.
- **A failed service is named, and nothing more.** An adapter's error text
  can quote its request URL, and Pixabay's carries the key; only "Pixabay did
  not answer" reaches the browser. Tested with a key in the error.
- Searched images are not added to the library yet; the Library screen is
  where that belongs.

**Verified** in a real browser against the live services
(`scripts/verify_search.py`): a fresh user agrees on the consent screen,
renders the sonnet, searches "frozen lake in winter" for one scene — 12
results from Pexels, Pixabay and Openverse, every preview loaded — uses one,
re-renders, and the photographer is credited on the result page.

### D-143 · The library is readable before it exists

**Found by** the search verification: on a first run, every scene thumbnail in
the scene plan was broken. The server's path sandbox allowed the library only
if it existed at startup, and a new user's library is created by their first
render's download — so every image that render found was refused until the app
was restarted. The earlier verifications used an existing library, or none.

**Fix.** The configured library is always in the sandbox; the check resolves
each allowed directory at request time, so one created later works. Tested
both ways: the test fails on the old code, and a path elsewhere on the disk is
still refused.

### D-144 · Cards pause the speech; three timing bugs from D-110 — BUG

**Found** while reading the card code before building card editing (item 5),
and confirmed by measurement, not by the plan: `scripts/check_av_sync.py`
transcribes a rendered video's own soundtrack and compares when each word is
heard with when its caption highlights it.

D-110 moved each scene's word timings with its frames when cards are
inserted, so the plan is on the video's clock. Three places still assumed
the recording's clock:

1. **Chapter cards mid-video did not pause the audio.** Only cards at the very
   start delayed it. After a chapter card, pictures and captions ran late
   against the speech by the card's length. Measured on 200s of the Golden
   River chapter: **+0.0s before the first card, +2.0s after it, +4.0s after
   the second.** Every video over three minutes with a chapter card.
2. **A title shifted the highlights twice.** The renderer added the leading
   cards' length to words that already included it (D-099's offset, written
   before D-110), so every titled video's word highlights ran late by the title,
   3s by default.
3. **Highlight reels cut the wrong audio when the plan had cards.** The cut
   ranges were read from the plan's frames as if they were times in the
   recording, so a titled reel was cut 3s late, plus 2s per chapter card before
   each range.

D-110's own verification checked that each scene's words fell inside the
scene: true, and not the same as matching the audio.

**Fix.** The plan's clock is the video's, everywhere:

- `ScenePlan.card_pauses()` says where each card sits in the recording and for
  how long. The narration is split at those points and each piece padded with
  silence the card's length (`atrim` + `apad=pad_dur` + `concat`); cards at the
  start still use `adelay`. With no mid-video card the filter is unchanged.
- The renderer no longer adds the leading-card offset to words.
- `audio_ranges` subtracts the card time before each scene.

**Verified** on real renders with the new checker: 200s with a title and two
chapter cards, **median offset 0.00s** in every stretch between cards (298
words); a 60s highlight reel from a titled 400s recording, 0.00s (94 words).
Eight tests, including four that render a plan against a recording that is
silent but for one tone and find the tone in the output; the four covering the
bugs fail on the old code.

Plans saved before D-110 (the morning of 27 September) hold words on the
recording's clock and will now caption early after a card; their timing was
already wrong. The plan version is not bumped, since that would refuse every
current web job's plan.

### D-145 · Editing title and chapter cards — PROJECT OWNER'S DECISION

**Context.** Item 5 of the owner's plan: title and chapter cards in the scene
plan. Cards were set once, at render time: a title only from the settings
screen, chapters only where the speaker paused for two seconds.

**Decision.** In the scene plan a person can:

- **change what a card says** — any title or chapter, up to 90 characters (a
  card is read in two or three seconds; more is a paragraph);
- **remove a card** — everything after it moves up;
- **add a title** to a video that has none — only ever what they type (D-092);
- **start a chapter** before any spoken scene except the first, and not
  straight after another card. It takes the scene's opening words, as
  automatic chapters do, and can be reworded.

Adding or removing a card retimes every later scene and its words, which are on
the video's clock (D-110), and the renderer pauses the speech for each card
(D-144) — so a card added after the fact is in step with the speech. The whole
plan comes back from these routes, since scene numbers change; a card a person
added or reworded is marked as their choice. Removing and re-adding round-trips
to the identical plan (tested).

**Card text now wraps** to the frame. A card was one centred line: fine for
"The Golden River" at 16:9, off both edges for a longer title, or any title in
9:16, where barely a dozen title-sized characters fit across. Each line is its
own centred `drawtext`, so it works without `text_align`, which older FFmpeg
lacks. Segment cache version 3, so no stale card is reused.

**Nothing to search, no search.** Verifying cards showed the sonnet's credit
scene — nothing left in it after D-141 but "of ." — matched a slide of text at
0.176, just over the threshold: embedding near-empty text still finds a nearest
image. A scene with no searchable words now skips the search and falls through
to atmospheric or plain; the scene plan says "Nothing here to search for".

**Verified** in a real browser (`scripts/verify_cards.py`): render the sonnet
with no title; add "A Calendar of Sonnets"; start a chapter, reword it
"Winter"; update the video — both cards appear in the frames, and the word
highlights match the speech (`check_av_sync`, median 0.00s either side of the
chapter); remove the chapter, and the plan is whole.

### D-146 · The Library screen — PROJECT OWNER'S DECISION

**Context.** The sixth screen from the approved plan (D-113, decision 4):
upload your own images and clips with provenance, author defaulting to you
and licence to "own work"; browse thumbnails with source and licence per
asset; delete; see which videos used an asset.

**Decision.**

- **Adding.** Photos (JPEG, PNG, WebP) and clips (MP4, MOV, WebM, M4V), up to
  50 at a time, 50 MB per photo and 400 MB per clip. Stored inside the library
  under `own/`, under names the server chooses; a client's filename is used
  only for its suffix, and a photo must open as an image before it is kept.
  Each gets a provenance record beside it and goes through the ordinary ingest,
  so duplicates are skipped (and their files removed) exactly as for a folder.
- **Credit.** "Credited to" is the person's name, asked once and remembered;
  the licence defaults to "Own work". A name is required: "You" would put
  "You (Own work)" into a published video's credits, and a blank credits
  nobody (D-012). Uploads are recorded with source `local`, like a folder
  ingested from the command line, so credits read "Jane Doe (Own work)". The
  screen labels them **"Added by you"**, not "Your own": a person may add a
  photograph they have a licence for but did not take.
- **Browsing.** Newest first, 60 at a time, filterable by where things came
  from (Added by you, Pexels, Pixabay, Openverse). Every item shows author,
  source and licence, and how many videos show it; selecting one shows its
  size, a link to its original page when it has one, and the videos by title.
  Paths never reach the browser; thumbnails are named by asset id and
  sandbox-checked.
- **Which videos.** Read from each job's saved plan, the record of what a
  video shows (D-011).
- **Removing.** Always confirmed, and the confirmation says what it means: how
  many videos show the asset — they keep their finished files, but updating
  them needs another image for those scenes — and whether the file goes.
  **Only files the Library screen added are deleted**; a download or a folder
  the person ingested themselves keeps its file, and only its library entry
  goes. A person's folder is not the app's to empty.
- The matching model is loaded once per server, on the first upload (about
  twenty seconds, and the screen says so).

**The first-run advice is whole again.** D-131 cut the "add photos to your
library" half of every empty-library message until the Library existed. It
does now, so the upload and settings screens, the scene plan and the result
warnings suggest it again, and the test that forbade it now requires it where
nothing was found.

**Verified** in a real browser (`scripts/verify_library.py`), as a new user
whose library does not exist yet: the empty state; three photographs added,
each credited to its real photographer under its real licence, read from the
file's own provenance, so nothing in the screenshots is misattributed; a video
made from the owner's recording, which used all three; each then shows "In 1
video", the detail names the video, the removal warns about it, and removing
one leaves two.

### D-147 · Plans record their renderer; pre-release plans are unsupported — PROJECT OWNER'S DECISION

**Context.** D-144 changed what a plan's word timings mean after a card:
plans saved before D-110 hold them on the recording's clock. The owner's
decision: no migration -- there are no users yet -- document that pre-release
plans are unsupported, and record the renderer version in each plan from now
on, so future format changes can be detected and warned about.

**Decision.**

- Every plan records `renderer_version`, the renderer's version when it was
  made. The constant moved to `voxframe/render/version.py` so the plan and the
  segment cache can both read it.
- A saved plan without one is **pre-release**: `ScenePlan.load` marks it
  (`renderer_version: 0`) rather than refusing it, so it can still be opened.
  Rendering one warns, in the app's result warnings and on the command line:
  "This scene plan was made by a pre-release version of Voxframe, which is not
  supported. Its captions may be out of step after a title or chapter card. To
  be sure, make the video again from the recording."
- A plan from an older *released* renderer is logged, not warned about: most
  renderer changes alter how segments look, not what a plan means. When a
  future change does alter what a plan means, `_plan_age_warnings` is where it
  says so.
- Editing keeps the version a plan was made with: an edit does not make an old
  plan's timings new.

**The A/V sync check is permanent** (owner's decision). The checker moved into
the package (`voxframe/render/sync_check.py`; the script wraps it), and
`tests/integration/test_av_sync_real_render.py` renders the public-domain winter
sonnet through the app's own pipeline with a title card, adds a chapter card as
the scene plan does, renders again, and transcribes the finished soundtrack:
every stretch between cards must have a median offset within 0.5s. Against the
renderer from before D-144 it fails -- "words from 3.0s are off by +3.00s" --
and with the fix it passes. It skips only if the Whisper model is not
downloaded.

### D-148 · A music bed from the browser — PROJECT OWNER'S DECISION

**Context.** Carried forward since step 2: the command line took `--music`,
the app did not.

**Decision.** The settings screen has an optional **Music** card: upload a
track (MP3, WAV, M4A, FLAC, OGG) and, if it needs attribution, a credit as it
should appear. The track uploads like the recording, and the render request
names it by upload id -- the page never names a path (D-115). It is mixed
under the voice and ducked while anyone speaks, as on the command line.

- Voxframe never supplies music of its own (D-091): the card says so.
- A track with no credit is recorded as supplied by the person, never given an
  invented credit (D-091). Uploads are stored as `source.*`, which would have
  made that line "Music: source.mp3 (supplied by the user)"; the name the
  person uploaded it under is recorded at upload (made safe: letters, digits,
  spaces and a few marks, 80 characters), and the credit uses it.

**Verified** in a real browser (`scripts/verify_music.py`) with a generated
chord, so no licence question arises: attached with a credit, credited on the
result page and in the plan, and present in the soundtrack -- the opening
second, before the first word, measures -47.7 dB with the bed and is silent
without it.

### D-149 · The release-time key check — PROJECT OWNER'S DECISION

**Context.** The owner's rule (D-113, decision 3): their personal keys must
never be in any package, installer, build or fixture, and a release-time check
must fail if any key-like value from `.env` appears in built artifacts.

**Decision.** `scripts/release_key_check.py`:

- **What it looks for:** every `.env` value whose variable name contains KEY,
  TOKEN, SECRET or PASSWORD (eight characters or more), and every key saved
  through the app's Settings screen -- each as written and base64-encoded.
- **Where:** files and directories given, and inside wheels, zips and tarballs.
- **What it prints:** only the file, the variable's name and the key's last four
  characters. A check that leaked the key into a build log would defeat itself;
  a test plants a key and asserts it is never printed.
- **No keys to look for is not a pass** (exit 2), so a CI run without the
  secrets cannot pass silently; `--allow-no-keys` says so explicitly.

It runs twice: the release build runs it on the built wheel and source archive
and stops the release on any finding; and the test suite runs it on **every file
tracked in git** -- code, fixtures, docs and the built web bundle -- against this
machine's `.env`, so a key committed anywhere fails the build.

### D-150 · Packaging: what ships is what git tracks

**Context.** Phase 8's packaging promise: `pip install voxframe[web]`, then
`voxframe web`, with no Node toolchain and no separate install step.

**Decision.** `scripts/build_release.py` checks the promise against the built
packages rather than the source tree: it rebuilds the web app and stops if the
committed bundle was stale; builds the wheel and source archive; fails if either
holds a file git does not track, or any key from this machine (D-149); then
installs the wheel into a fresh virtual environment and starts `voxframe web`
from outside the repository, requiring the page, its script and the health
route to be served from the installed package. The process is written up in
`docs/RELEASING.md`.

**What its first run found.** The source archive held the owner's own
recordings from `samples/private/`. Nothing was published -- `dist/` is local
and gitignored -- but a release would have shipped them. The build tool
(hatchling) honours only the root `.gitignore`, and `samples/private/` is
ignored by a nested one. Two changes, either sufficient alone:

- The source archive lists what it contains in `pyproject.toml` instead of
  taking everything not ignored, and excludes `samples/private/` and the
  library explicitly.
- **Every packaged file must be tracked in git.** Everything personal on a
  developer's machine is untracked -- recordings, `.env`, the library, rendered
  videos -- so this holds whatever any ignore file says. Checked against the
  old configuration: it names all three private recordings and stops.

An empty library database, created by opening the default library and committed
by accident in step 3, was untracked, and `/library/` is now ignored.

**Also documented:** the web app guide (`docs/web-app.md`) for people using it,
the release process (`docs/RELEASING.md`), and the Phase 8 report. The library,
cache and output folders default to paths under the directory the app is
started from, which suits a checkout; the Phase 9 installers should put them in
a per-user data folder.

### D-151 · One committed recording: the sonnet excerpt — PROJECT OWNER'S DECISION

The A/V sync test (D-147) needs real speech, and sample media is fetched rather
than committed (D-019), so on a machine that had not fetched it the test
skipped and could not catch a sync regression. The owner's decision: commit the
45-second public-domain sonnet excerpt as a documented exception to D-019, and
only that file.

`samples/public/en_sonnet_january_45s.wav` -- 1.4 MB, SHA-256
`cd927d85f09c3bde1422529afd66488302fea67d264a784f5453342e7fad4b2d`, the LibriVox
excerpt whose provenance `samples/PROVENANCE.md` already recorded (public
domain: LibriVox recordings are dedicated to it, and the text is public domain
by age). `.gitignore` un-ignores that one path; `fetch_samples.py` records the
same hash; the source archive includes it, so the test runs from a source
install too. A test fails if any other media file anywhere in the repository is
tracked, and another if the committed excerpt's hash changes.

### D-152 · Phase order: 9 before 7; motion editing dropped — PROJECT OWNER'S DECISION

- **Phase 9 (installers, release, launch) comes before Phase 7 (the AI
  director).** The director is to be built after the first public release,
  informed by real user feedback rather than guesses about what people want.
- **Motion direction editing is dropped from Phase 8** and recorded under
  "Future ideas" in PROGRESS.md. Per-scene motion on/off stays as it is: the
  scene plan file takes `"motion": "none"`.
- **Before any public release, or making the repository public,** the entire
  git history is scanned -- every commit, not only the current tree -- for API
  keys, key-like strings, personal recordings and anything else that should
  never be public, with a permissively licensed secret scanner; findings are
  reported without printing any secret, with a proposal for cleaning the
  history if anything is found.

### D-153 · The history scan, and what it found

Run as the owner required before anything goes public (D-152), with
`scripts/scan_history.py`: gitleaks 8.30.1 (MIT; downloaded from its release and
verified against the published checksums) over all 62 commits with secrets
redacted, an exact search for this machine's own keys in all 712 file versions
and every commit message, and a list of every path ever committed.

- **Keys: none.** gitleaks flagged two key-shaped strings, both made-up keys
  planted by the redaction tests (in `test_sourced_render.py` and
  `test_api_settings_routes.py`); compared in code, neither is a real key. They
  are listed by fingerprint in `.gitleaksignore`. The owner's two keys appear
  nowhere in the history, raw or base64-encoded. (TruffleHog was not used: it
  is AGPL.)
- **Recordings: none.** No audio or video file was ever committed; the sonnet
  excerpt (D-151) will be the first.
- **One file:** `library/library.db`, the empty library database committed by
  accident in step 3 and untracked in D-150. It holds no assets, but it remains
  in history.
- **Personal, not secret** -- for the owner to decide before publishing: the
  commit author email on all 62 commits; and text from the owner's own
  recording -- its filename and transcript lines -- in 12 tracked files
  (tests written from it at the owner's request, decisions, reports) and in
  about a dozen Phase 4 and Phase 8 screenshots.
### D-154 · A per-scene camera movement switch — PROJECT OWNER'S DECISION

Phase 9 decision 11. The scene panel has **Camera movement** for any scene
showing a photograph: on is the usual slow Ken Burns pan and zoom, off holds the
image still. Not offered for clips (they move by themselves, D-085), cards, or
plain backgrounds. Off survives choosing another image for the scene: it is a
choice about the scene, not the old image. Toggling it does not change where the
image came from, so an atmospheric scene stays labelled atmospheric. Direction
and strength remain a future idea (D-152).

Verified in a real render (`scripts/verify_cards.py`): with movement off, the
scene's first and last frames differ by 0.7 (mean grey level, top of frame,
captions excluded) -- compression noise.

### D-155 · Checking for updates: a button only — PROJECT OWNER'S DECISION

Phase 9 decision 9. Settings has **Check for updates**: one request to GitHub's
latest-release endpoint for `Ngum12/voxframe`, made only when the button is
pressed (a POST, so no page load or prefetch can trigger it). It reports "you
have the latest", "version X is available" with a link, or "no release has been
published yet" (GitHub answers 404 until one is). A link to anywhere but
github.com is not passed on. A test fails if anything but that route calls the
check, or anything but the button's client function calls the route.

Also: every project link now points to `github.com/Ngum12/voxframe` (Phase 9
decision 3) -- `pyproject.toml`, the README, the Dockerfile label, sample
provenance, and the User-Agent the image services see.
### D-156 · Per-user folders for an installed app — PROJECT OWNER'S DECISION

Phase 9, step 1 (plan approved). A source checkout keeps its local folders,
which is what development and the tests expect; an installed Voxframe -- the
installer or `pip install` -- keeps things in the person's own folders.
`is_source_checkout()` tells them apart: the package sits in `src/` beside a
`pyproject.toml` only in a clone (an editable install included).

| | Windows | macOS | Linux |
|---|---|---|---|
| library, cache, jobs, models | `%LOCALAPPDATA%\Voxframe` | `~/Library/Application Support/Voxframe` | `$XDG_DATA_HOME/voxframe` |
| finished videos | `Videos\Voxframe` | `~/Movies/Voxframe` | `~/Videos/Voxframe` |

- **The environment always wins** (`VOXFRAME_LIBRARY_PATH` and the rest).
- **Models** go in the data folder's `models/`: the package sets `HF_HUB_CACHE`
  when first imported, before any model library reads it -- only when
  installed, and never over a person's own `HF_HOME` or `HF_HUB_CACHE`.
- **Jobs** (uploads, thumbnails, working files) are app data; only **finished
  videos** go to the videos folder, under the title or the recording's name --
  a hard link where the disk allows, so a long video is not stored twice, and a
  re-render replaces its own file. The result screen says where it was saved,
  with Open folder.
- **Settings > Your folders** shows the library, videos and data folders with
  Open folder, and **Change library folder...** opens the operating system's own
  folder window. The path comes from that window on this machine, never from
  the page (D-115); it is remembered in the user's preferences and used from the
  next start, so nothing being rendered moves underneath it. Where no folder
  window can be shown (no Tk), it says to set `VOXFRAME_LIBRARY_PATH`.
### D-157 · Getting ready: the models before the first video — PROJECT OWNER'S DECISION

Phase 9, step 2 (plan approved). The first time the app opens without its
models, a **Getting ready** screen comes before anything else: what the two
models are (speech recognition, picture matching), what each is for, and how
big -- *Standard, about 3.1 GB* or *Lite, about 750 MB* (D-067). Nothing is
downloaded until **Download** is clicked; the choice is remembered (the
environment's `VOXFRAME_PROFILE` wins); **Not now** leaves it to the first
render, and Settings has the same card.

- **Progress is measured**: the bytes on disk in the model cache, partly
  downloaded files included, polled each second, with time left from the actual
  rate. Each model is fetched with its own library's download function
  (`faster_whisper.download_model`, open_clip's pretrained download and
  tokenizer), so what arrives is exactly what the app uses, without loading it.
- **Counting real bytes took three tries.** The cache keeps files as blobs
  linked into snapshots; on Windows without link permission, as real files in
  snapshots; or as blobs that are themselves links into a store shared across
  repositories. Following every entry to its real file and counting each once
  matches the published sizes within 1% on all four models here. The old
  "is Whisper cached" check double-counted and looked only in the default
  cache, so it would have been wrong for an installed app (D-156); it now uses
  the same measure.
- **A failure** says to check the connection and try again, and never repeats
  the library's error (which can hold URLs). An interrupted file resumes.
- **Offline:** the screen names the models folder to copy them into.

**Verified** with a real download (`scripts/verify_first_run.py`): a server
with an empty model folder, Getting ready shown first, Lite chosen, the real
download measured (284 of 750 MB, "about 7 minutes left"), finished in 815s,
753 MB in the new folder, then the consent question as before.

### D-158 · The Windows installer, the app launcher and the icon — PROJECT OWNER'S DECISION

Windows is installed with pynsist (MIT) and NSIS (zlib licence) rather than
Briefcase, because pynsist can bundle tkinter, which the launcher's small window
needs. The installer holds its own Python 3.13, every library as a published
wheel (PyTorch from PyPI, the CPU build), and FFmpeg 9.0.2 (gyan.dev
"essentials", GPL-3.0, SHA-256 checked) in its own folder with its licence and a
source note. 264 MB; about 1.2 GB installed; models download on first start
(D-157). The `app` extra (`transcribe`, `library`, `web`) is what it bundles:
`web` alone could neither transcribe nor match, which the install instructions
had wrongly said it could.

`voxframe.launcher` is what the Start-menu shortcut runs: one app at a time,
the bundled FFmpeg first on the PATH, logs to a file, the browser opened at the
app, and a small "Voxframe is running" window with Open in browser and Quit.
The icon (`scripts/make_icon.py`): a play triangle over a caption bar with one
word in the captions' gold.

**Verified by installing it on the owner's machine, with their agreement**:
the installed copy, alone and with its personal folders redirected to scratch,
imported from the install, used its own FFmpeg (libass and x264), loaded
PyTorch, Whisper and CLIP, rendered the 45-second sonnet with a title (1,440
frames, 48.0s, captions correct), and served the app; its uninstaller then
removed the program folder, the shortcut and the uninstall entry.

Found by that test: a silent install ignored `/D=<folder>`, because pynsist's
multi-user setup overwrites it. The build patches the generated script so `/D=`
is kept; not yet verified, so silent installs are left out of the v1 docs, and
the release workflow's own install test uses pynsist's `/INSTDIR=` instead.
Unsigned for v1 (owner's decision). SignPath Foundation's free signing needs a
public, already-released project, so it comes after v1.

### D-159 · v1 is trimmed to what is needed to ship — PROJECT OWNER'S DECISION

v1 is: the Windows installer; an early, unsigned macOS build for Apple Silicon,
built by GitHub Actions; the clean public repository; a release workflow that
builds both on a version tag the owner pushes; and essential docs only (README,
install guides for Windows and Mac, a first-video walkthrough, LICENSE,
THIRD_PARTY_LICENSES with the FFmpeg notice, a privacy note, a minimal
SECURITY.md). Deferred until after launch, listed in PROGRESS.md under "After
v1": PyPI, a GitHub Pages site, SignPath signing, extended contributor guides.
Drafts of those that were not yet committed were removed. The update-check
button and the camera-movement switch were already finished and tested, and
stay.

### D-160 · The public repository is a clean copy — PROJECT OWNER'S DECISION

This repository stays private with its full history. The public one is made in
a separate folder from one commit of the current tracked files, authored and
committed by `Ngum12 <127442228+Ngum12@users.noreply.github.com>` with no other
attribution, after a backup (`git bundle`). Before it: the owner's recording's
words were replaced in the tests with public-domain text exercising the same
rules (Lincoln, Twain, Emerson, Thoreau, Keller); every screenshot showing the
recording was retaken on the public-domain sonnet, or removed where no document
used it; the empty library database was already untracked (D-150). The scan
(D-153) runs on the new repository before it is handed over.

### D-161 · The macOS app, and the release workflow — PROJECT OWNER'S DECISION

`scripts/build_macos_app.py` runs on a GitHub macOS runner (Apple Silicon,
free for public repositories) with no secrets or accounts: Python 3.12 from
python-build-standalone and FFmpeg 9.0.2 (static, with libass and x264, from
ffmpeg.martin-riedl.de), both pinned by SHA-256, Voxframe installed into that
Python, an app bundle with a two-line launcher, **ad-hoc signed** (Apple Silicon
runs no unsigned native code; to Gatekeeper it is still unsigned), and a DMG.
The bundle is tested on the runner before packing: its own Python, its own
FFmpeg, a real render, the app server. Labelled early; Apple Silicon only.

`.github/workflows/release.yml` runs only on a `v*.*.*` tag the owner pushes:
checks (version, notes, gitleaks over the whole history), the Windows installer
built and install-tested on a Windows runner, the macOS app built and tested on
a macOS runner, then a **draft** release with both, `SHA256SUMS` and the notes
from `.github/release-notes/<tag>.md`. Untested until the first tag: nothing can
run GitHub's machines from here.

### D-162 · FFmpeg's source ships with every release — PROJECT OWNER'S DECISION

The installers' FFmpeg source notes no longer carry a written offer. Each
release attaches the corresponding source instead: FFmpeg's release tarball for
every version the installers bundle (both 9.0.2 today), fetched from ffmpeg.org
by the release workflow and checked against a SHA-256 pinned in
`scripts/ffmpeg_sources.py` after checking FFmpeg's release signature, with the
signature file; and a list of the libraries compiled into each build, with
their versions as the build reports them and where each provider publishes
their source. The Windows build, which followed gyan.dev's latest release, is
now pinned to one version and SHA-256 like the Mac build, so the attached
source always matches what ships.

Contact: security reports go through GitHub private vulnerability reporting,
with the maintainer's LinkedIn as the fallback; other questions go to GitHub
issues first, LinkedIn second.

## v0.1.1 — what v0.1.0 got wrong on a real Windows install

### D-163 · The picture model ships whole, and fails loudly if it cannot run

**What happened.** On a fresh install, 0 of 106 scenes got an image. The
installed app's own log showed the chain: the Getting-ready screen's download
stopped with `ModuleNotFoundError`; every render then loaded the embedding
model once per batch (205 times) and failed each time; ingest recorded all 374
good downloads as "failed" ("0 added, 374 failed"); nothing entered the
library, so matching and the atmospheric fallback had nothing to choose from;
and the scene plan said "no image library supplied". Running the installed
Python showed the cause: `No module named 'transformers'`. The default
multilingual model's tokenizer and text tower are Hugging Face models, which
open_clip imports lazily and does not declare; the `library` extra did not
declare it either. Every development machine had it through another extra, so
no test noticed. With only `transformers` added, the installed build itself
embedded text and images (checked with its own Python, read-only).

**Decided.**
- `transformers` is in the `library` extra, so every installer bundles it.
- A test reads each model's needs from open_clip's own config and fails if any
  is missing from the `library` extra. Run against v0.1.0's `pyproject.toml`,
  it reports `transformers` missing.
- A model that cannot load raises `EmbedderUnavailable` with the reason, once,
  remembered; ingest passes it up instead of failing every file; a render
  loads the model **before transcribing** and stops with a clear message,
  instead of producing a video of plain backgrounds 17 minutes later.
- Getting-ready calls a model ready only if every one of its repositories is
  there (the weights alone passed the size test while the tokenizer never
  arrived) and its modules import; a broken install is not blamed on the
  internet connection.
- Both installers' tests on GitHub now embed a sentence and an image with the
  real default model (`voxframe.selfcheck`), not just import it.
- An empty library is described as "no image in the library yet", not "no image
  library supplied".

### D-164 · The app is always visible; a second launch opens the running one

**What happened.** The installed app had `_tkinter.pyd` but not the Tcl/Tk DLLs
or script libraries (pynsist copies only the module), so its "Voxframe is
running" window never opened; the launcher fell back to running with nothing
on screen. Closing the browser tab left no way back, and a second launch said
"already open, use its window" -- of a window that did not exist. Checked
with the installed Python: `DLL load failed while importing _tkinter`; with
`tcl86t.dll`, `tk86t.dll`, `zlib1.dll` beside it, the libraries in `lib/`, and
`TCL_LIBRARY`/`TK_LIBRARY` set, it opens a window.

**Decided.**
- The Windows build copies those files from the building Python into the
  package folder; the launcher points Tcl at them (`prepare_tk`); both
  installers' tests open a real Tk window.
- If Tk still cannot open, a Windows message box offers **Open** and **Quit**,
  so the app is never running unseen.
- The running app writes a marker with its process id, a local "wake" port and
  a random wake key. The session token is still never written to disk
  (D-115): the key's only power is to make the running app open a browser tab
  at its own address. A second launch uses it and exits.
- A marker whose lock is free is stale (the operating system releases the lock
  when the process ends, however it ends) and is cleared. A copy that holds the
  lock but does not answer is offered to be stopped, after checking it is a
  Python process.

### D-165 · No console windows

The installed app runs under `pythonw`, with no console, so Windows gave every
FFmpeg and FFprobe call a console window of its own: they flashed throughout a
render. Every subprocess call passes `creationflags=NO_WINDOW`
(`voxframe.processes`); a test reads the source and fails on any call without
it, or on any way of starting a process that cannot take it.

### D-166 · A long recording's search budget is shared, spread and cached

v0.1.0 searched the first 40 scenes of a long recording and left the rest
blank, to protect the image services' hourly limits. Measured on the 106-scene
recording, the limit was not the only problem: the first 40 scenes had already
downloaded 386 MB of the 400 MB per-render cap, a second render downloaded the
same 386 MB again, and files named by their position in a run
(`pexels_0000.jpg`) were overwritten by the next render with different images
under names the library still pointed at.

**Decided.**
- No fixed scene cap. Every scene's queries are worked out first; a query
  shared by several scenes is searched once, with a page big enough for all of
  them, and each scene takes its own slice. Phrases recurring in three or more
  scenes are searched first, as themes.
- Scenes are searched in an order that covers the whole recording early (first,
  middle, quarters...), so a limit reached partway still leaves coverage
  spread.
- Each source stops for the run when its own rate-limit headers say it is
  nearly used, when it refuses with a 429, or after two connection failures in
  a row; the others carry on. Scenes left when every source rests are
  recorded, and the person is told which source ran out, when to try again,
  and that making the video again continues from there.
- Downloads are named by their address, so the same image always has the same
  name and a later render reuses it; Openverse responses are cached for 24
  hours like the others'. Long recordings (over 40 empty scenes) take two
  candidates per scene, since every download joins one pool the whole plan is
  matched against; clips may use at most half of the download cap.

**Measured** on the owner's 17-minute talk (107 scenes; same transcript; a new
user's empty library and search cache; real Pexels, Pixabay and Openverse):

| | own image | atmospheric | filled | downloaded |
|---|---|---|---|---|
| v0.1.0, installed | 0 | 0 | 0 of 106 | 386 MB, all rejected |
| v0.1.0, source checkout | 36 (34%) | 71 | 107 of 107 | 421 MB |
| v0.1.1 | 52 (49%) | 55 | 107 of 107 | 197 MB |

The source checkout filled its scenes: the installed failure was entirely the
missing module. The rest of the gain is the budget: 104 of 107 scenes searched
(three have no searchable words) against 40, at under half the download.
v0.1.0's warning also said "the rest show a plain background" while they were
being given atmospheric images; the new messages say only what happened.

## v0.1.2

### D-167 · A long video's segments are joined in runs, never on one command line

**What happened.** A 126-scene talk failed after rendering all 157 segments,
three times, with `FileNotFoundError: [WinError 206] The filename or extension
is too long` (the installed app's log, `subprocess.run` inside `run_ffmpeg`,
called from `_concat_with_transitions`). The join passed every segment's full
path as an `-i` argument, plus the whole crossfade graph, on one command line;
Windows refuses a command line over 32,767 characters before FFmpeg starts. A
112-scene video had fitted, which is why v0.1.0's long renders worked.

**Decided.**
- The join runs in runs of at most 40 segments, breaking only at hard cuts
  (a blend needs both its segments in one call), then joins the runs with the
  concat demuxer from a list file, which has no length limit. Every blend is
  kept; the frame count is exact (tested with real FFmpeg, forced into
  several runs).
- `run_ffmpeg` refuses any command over 32,000 characters, on every platform,
  with a message that says so, so a test anywhere finds what would fail on
  Windows. A test builds a 157-segment join with long paths and checks every
  command fits in half the limit; on v0.1.1's code its one command was about
  36,000 characters.

### D-168 · The installer and uninstaller wait for Voxframe to be closed

Installing over a running Voxframe replaced files under a live process, which
fails partway or leaves two versions mixed. Both now check first: a running
Voxframe holds its `pythonw.exe` open, and Windows refuses to open a running
program for writing, so that is the test (no process listing, no console
window). If it is running they say so and offer Retry after Quit; a silent
install stops with an error code instead of waiting. The release workflow
proves it on GitHub's Windows machine: with the installed `pythonw.exe`
running, the installer and the uninstaller must both refuse and change no
file.

## Towards v0.2.0

### D-169 · Language detection is limited to English and French by default

The installed app, on its Lite profile, detected an English talk as Yoruba
(`language=yo` in its log), the failure D-068 measured: Whisper `small`,
unrestricted, heard the owner's accented English as Yoruba and garbled it,
while restricting the candidates to English and French gave a transcript
identical to forcing English. English and French are the languages Voxframe is
tested in (D-040), so `VOXFRAME_LANGUAGES` now defaults to `en,fr`. `any`
restores unrestricted detection. The transcript cache key includes the
candidate set, so earlier unrestricted transcripts are not reused.

### D-170 · The music director, Stage 1: the person's own track, edited to the speaker

Approved in the plan (`docs/MUSIC_DIRECTOR_PLAN.md`, private) with the owner's
decisions: own tracks only (D-091 stays), librosa only, no torchaudio.

**What it does.** A track is decoded once by FFmpeg and analysed by librosa:
tempo, beats, bars (3 or 4 beats, the bar's first beat chosen from the
accents), energy and harmony per bar, cached per file. The bed is then built
from whole bars: a talk longer than the track repeats whole phrases (the loop
whose harmony matches best, keeping the intro and the ending out of it); a
shorter one drops whole phrases. Joins are 20 ms equal-power crossfades. A
phrase-starting downbeat lands exactly on the frame where the last word ends,
by shifting the start by less than a bar under the fade-in. Under each stretch
of speech the music is lowered just enough to sit 15 dB below that stretch's
measured voice; pauses of 1.5 s or more swell to full level and are back down
before the next word. The bed is rendered in blocks (an hour-long track never
sits in memory), cached, and mixed under the narration with no sidechain, which
would duck a second time. `VOXFRAME_MUSIC_MODE=simple` keeps the old looped
bed, as does any track the director cannot use, with a note saying why; music
is never the reason a video fails.

**Measured, and fixed on the way:**
- Bar starts on a generated 4/4 track with known bars: librosa's beats are
  frame-quantised (23 ms), measured -43 to +39 ms off. Each beat is refined to
  the steepest rise in a 10 ms energy envelope, stepped every 2 ms (2 ms
  windows rippled with a 55 Hz kick and landed inside the note), within a third
  of a beat (the tracker's tempo was 117.5 against 118, so its grid drifts).
  Result: 62 of 62 bar starts within 20 ms, median 4.4 ms.
- Steadiness: the tracker forces an even grid onto anything, so its own beat
  intervals look regular even for beats wandering by 40%. Local tempo variation
  separates them: 0.00 on the steady track, 0.22 on the wandering one,
  0.05-0.11 on the owner's three tracks (worship instrumental, piano hymns, a
  song); the limit is 0.15. The hymns are the loosest; the listening test
  decides whether they cut well.
- Speech stretches were grouped separately in video and recording time; a gap
  of 0.6 s less a rounding error merged in one and not the other, and every
  later stretch was measured against the wrong voice. Grouped once now.
- Whisper timed the sonnet's last words to 47.64 s of a 45.0 s recording, which
  put the landing after the voice and past the end of the video. Word timings
  are clamped to the recording.
- On the sonnet with the owner's worship instrumental: the landing on frame
  1519, the last word's; every stretch of speech 15.0 dB over the music; the
  one long pause swelled to -24 dB and was back to -39 dB by the next word.
  Analysis of ten minutes of music: about 4 s.

**Dependencies.** The `music` extra (`librosa`) is in the installers' `app`
extra. The owner approved about 45 MB; measured, it is about **91 MB** of
wheels, because librosa brings SciPy (37 MB) and scikit-learn (8 MB), which
the installers did not have. That difference is for the owner to decide before
release. Development uses `.venv-music`, a virtual environment layered on the
main Python (the owner's choice), so the main Python is unchanged.

**Stage 1 ends with the owner's blind listening test**
(`scripts/music_listening_test.py`); Stage 2 does not start until the owner
approves its results.

### D-171 · Mix controls: the sound is made from stems, to a loudness target, and checked

The first stage of the approved music plan (`docs/MUSIC_DIRECTOR_PLAN.md`,
section 8), built before the generated score because it is useful either way.

**The render's last step is split in three.** The pictures -- segments joined,
captions burned in, no sound -- are kept whole in the cache, keyed by the
segments, transitions, captions and quality. The sound is mixed from two stems
on the video's timeline: the voice (with the silences cards add) and the music
bed at full level, cut on bars by the director or looped, but not ducked. Then
the two are joined, the pictures copied, not encoded. A change to the sound
alone re-renders no picture: on the sonnet, 14.8 s against 69.6 s for the full
render, with the one cached pictures file reused.

**The person's settings** are kept in the plan (`audio_mix`): voice level,
music level, how far under the speech the music sits (15 dB by default, as
D-170), and the destination. Ducking follows the words' timings to exactly
that distance, measured per stretch of speech, so the music slider changes the
music in the pauses and the margin holds. The destinations and their loudness
are as the owner approved: YouTube and social -14 LUFS / -1 dBTP (the default),
WhatsApp -15 / -1.5, podcast -16 / -1. Loudness is reached with FFmpeg's
`loudnorm` in two passes, linear, then limited below the ceiling. The sidechain
compressor is gone: word timings duck exactly, and a compressor on top would
duck twice.

**Every mix is checked** and a failure is flagged on the video, never passed
silently: integrated loudness within 1 LU, true peak under the ceiling, no
clipping, the music at least the chosen distance under every stretch of
speech, and no click at any edit in the music.

**The web app's Sound card**: sliders, a destination, undo and redo, a warning
(not an override) when the music is set within 12 dB of the voice, the last
checks, and "Apply to the video". Moving a slider plays 15 seconds of the mix
from where the video is paused, made from the kept stems.

**Measured on the owner's 17-minute talk:** stems 16 s (once, with the first
render); a sound-only update 77 s (mix, two loudness passes, checks) plus the
join; a preview 0.12 s. Result -16.0 LUFS, margin 15.0 dB, passed.

**Found on the way:** the new mix module imported the plan package, which
imports the renderer, which imports the mix module -- fine when the app loaded
the renderer first, a crash when anything imported it first. It imports the
settings type for annotations only; a test imports it on its own.

### D-172 · Stage 1 approved; own-track analysis on demand; no RNNoise models — PROJECT OWNER'S DECISIONS

- **Stage 1 is approved** on the owner's own listening to the Sound card on a
  short recording ("the voice stays clear, and the music feels right"). The
  formal blind ratings were not needed. **The director stays the default** for
  a person's own track.
- **Own-track analysis becomes a download-on-demand component.** librosa and
  what it brings (SciPy, scikit-learn, numba, llvmlite and others, about 91 MB
  of wheels) are left out of the installers. They are fetched, checked against
  pinned SHA-256 values, and unpacked into the person's data folder the first
  time someone uses their own music track. Everyone else keeps a small
  installer. Until the component is there, own-track music plays as the plain
  loop, ducked the same way.
- **No RNNoise model files.** FFmpeg's `arnndn` filter needs a model, and the
  usual source (`GregorR/rnnoise-models`) has no licence file, only the
  statement that the models are "not subject to copyright". Its training
  scripts use the McGill TSP speech database and unnamed cough and laughter
  recordings, with no terms stated. The owner's rule: if the licence is
  unclear, use FFmpeg's built-in noise reduction instead. Voice polish uses
  `afftdn`, which needs no model.
- **Three music choices** on the settings screen (No music, Use my own track,
  Let Voxframe score it). No music stays the default until the generated score
  passes the owner's listening check. They are recorded in the plan and built
  with Stage 3.

### D-173 · Voice polish: FFmpeg's own filters, measured, cached, with an exact "Original"

Stage 2 of the music director plan. On by default, switched on the Sound card
("Polished" / "Original"). Every step is an FFmpeg filter the app already
ships, so nothing is downloaded and no model file is needed (D-172):

1. Noise reduction (`afftdn`), set from the noise measured in the pauses
   between words (gaps of 0.4 s or more in the transcript), 3 to 12 dB. Left
   off when the room is already quiet (under -65 dBFS), and when what sounds
   between the words is music: plainly tonal (spectral flatness under 0.05)
   and audible, or tonal (under 0.2) and within 20 dB of the speech.
   Reducing it would damage both.
2. A high-pass at 75 Hz; a 2 dB presence lift at 3 kHz; a 2 dB cut at 320 Hz
   only when the recording measures boxy.
3. A mild de-esser and gentle compression (2.5:1).
4. Room tone: the recording's own quietest two seconds, looped under the voice
   at no more than -60 dBFS, so pauses are never dead digital silence.

**Checked, not hoped:** the pauses must stay above -75 dB when noise was
reduced, and the voice may lose at most 6 dB above 4 kHz against 1-4 kHz. A
failure shows with the sound checks on the video.

**Calibrated on two recordings**, and to revisit with more: the planned
de-esser strength took 6.5 dB from the talk's high frequencies (music under
the voice set it off); the milder one takes 0.06 dB. Boxiness measured 13.1
and 14.5 dB on two ordinary voices, so the planned 8 dB threshold would have
cut everyone's; it is 18 dB. Measuring noise in the quietest windows gave
-25.6 dB on the 17-minute talk -- speech, not noise -- hence the pauses.

**Two measuring faults found by the full suite**, both in the first version:
the public-domain sonnet, a plain reading, was reported as having music in
it. Whisper's word timings run short, so the transcript's gaps held the ends
of words: they measured -26.5 dB over a -50 dB room. The floor is now the
quiet end of the gaps (their 20th percentile), and never above the
recording's quietest tenth. And a 16 kHz recording at 48 kHz is empty above
8 kHz, which read as tonal (flatness near 0, against 0.39 at its own rate):
flatness is now measured only up to where the recording has sound. The level
rule alone was then fragile on the talk (its music 20.1 dB under the speech,
against the 20 dB limit), hence the plainly-tonal rule. After the fixes: the
sonnet, -50.4 dB and 0.23, not music, 12 dB of reduction; the talk, 0.00,
music, reduction off.

**Measured:** the owner's 17-minute talk, flatness 0.00: music is in the
recording, so noise reduction stays off; polishing took 15 s. A phone
recording: the first version read its background at -58 dB and reduced it by
12 dB (high frequencies changed by -0.7 dB). Measured in its real pauses it
is -69 dB -- the phone gates its own noise -- so it now gets no reduction,
only the tone, de-esser and compression. To confirm by listening.

**Cached like the other stems:** the polished voice is made once per voice
stem and polish version, with what polishing did kept beside it, so changing
the mix, or switching between Polished and Original, never polishes again.
Both are kept, so the preview compares them instantly. A failed polish leaves
the original voice in use and the video still made.

**"Original" is exactly the recording:** with polish off the mix reads the
voice stem itself -- the recording decoded at 48 kHz, placed on the timeline --
with no filter at all. Tested sample for sample against the mix, and against
the recording decoded separately.

**Music already in the recording** (the same measurement: tonal and close
under the speech) is said on the Sound card. With no music added, it notes
that "No music" is likely best. With a track added, the card and the video's
warnings say two layers of music may clash. Nothing is changed for the person.
The recording is only known once it has been through the app, so the note
comes with the first video, not on the settings screen.

### D-174 · The music component, as built (D-172)

Both installer builds resolve the app with and without the `music` extra and
ship the difference as a manifest (`components/music.json`): each wheel's
PyPI address, SHA-256 and size, cross-checked against PyPI's own published
digest at build time. The app itself is installed pinned to the same
versions (a constraints file), so the component always fits the installed app.

The first time a person's own track is used, the job's progress shows "a
one-time download of about N MB" and its progress. Each wheel is checked
against its SHA-256 and refused if any file would land outside its folder,
unpacked into a staging folder, and marked complete only when all are in; a
failure leaves nothing half-installed, and the video is still made, with the
music as a simple loop and a warning that it will try again. Only PyPI's file
host is contacted (listed in the privacy notes). A checkout, which has librosa
from the `music` extra, has no manifest and never downloads.

### D-175 · Voice polish approved — PROJECT OWNER'S DECISION

The owner listened in the app, comparing Polished and Original on the Sound
card with the phone recording and the 17-minute talk, and approved voice
polish as built (D-173): "all is cool". So:

- **Polish stays on by default**, with the Polished / Original switch on the
  Sound card; Original remains the recording exactly.
- **The phone recording keeps no noise reduction**: its pauses measure -69 dB
  (the phone gates its own noise), and only the tone, de-esser and
  compression apply.
- **The music-in-the-recording note** behaves as asked: it suggests "No music"
  when the recording already has music, and warns of two layers clashing when
  a track is added.
- The listening kit's voice mode is not built; the switch in the app is the
  comparison.

### D-176 · Generated score approved; build the full feature with variety at its core — PROJECT OWNER'S DECISION

The owner listened to the prototype gate's round two (four clips, blind A/B
against no music: inspiring, calm, reflective, and a 4:41 cinematic clip)
and approved it: "it sounds really good". Round one had been judged right in
timing, ducking, swells and endings but too thin ("a single piano"); round
two's layered arrangement -- energy levels from the speech, string
ostinatos, cinematic percussion, sections with their own progressions and
leads, human variation -- is the sound to build on. Prototype scripts:
branch `score-prototype` (`prototype/score/score2.py`).

The full feature is to be built in stages, each approved by the owner, with
**variety as a core requirement** so videos never all sound the same:

1. **Variation within a style,** seeded per video and reproducible: key,
   tempo, progressions, ostinato patterns, lead instruments, section order.
   A "New variation" button re-renders only the audio.
2. **About six styles for the first release** (for example inspiring, calm,
   reflective, cinematic, hopeful, documentary), each with a short preview,
   and a plan for more (tense, worship, ambient, acoustic, light electronic,
   and the African palette: balafon, mbira, kalimba, hand percussion).
3. **Styles as data files,** like the metaphor dictionary, so styles and
   progressions are added without code changes.
4. **More instruments:** further free sample libraries are searched and
   listed (licence, size, quality) and added only with the owner's
   approval; synthesis widens variety without downloads.
5. **Editor controls:** a style picker with previews, instrument group levels
   (piano, strings, percussion, bass, pads), overall intensity, and "New
   variation".
6. **Variety measured:** at least 20 scores across styles and recordings,
   shown by analysis to be no two too similar.
7. **A compressed, trimmed sample pack** downloaded on first use of "Let
   Voxframe score it", like the models; per-style packs if that keeps
   downloads smaller.
8. **Every mix rule and check stays,** and the three music choices: no music,
   my own track, let Voxframe score it.

Nothing is added -- a sample library or a Python package -- without asking
the owner first.

### D-177 · The generated score, Stage 1: the engine in the app, styles as data, the third music choice

Stage 1 of `docs/GENERATED_SCORE_PLAN.md` (D-176).

**The engine** is `voxframe/music/score/`, ported from the approved round-two
prototype and kept to its sound:

- reading the speech (stretches, long pauses, energy from speaking rate and
  loudness);
- composing: sections every 45 to 90 seconds at a speech boundary, sooner
  after a long pause, then chords on a breathing beat grid, each with an
  energy level from 0 to 4;
- arranging the layers;
- sampled instruments and synthesis;
- rendering and fitting under the voice.

The composition becomes a list of events with every value fixed, including
which of several equal recordings plays. The same speech, style and seed
therefore give the same audio, sample for sample (tested; the file bytes can
differ only because libsndfile stamps the time into a float WAV's header).
Rendering is pure.

**Long talks:**

- The prototype held everything in memory, several GB for a 17-minute
  talk. The engine renders in 20-second blocks, carrying each reverb's tail
  across them, and finishes the score in streaming passes. A test checks
  that 3-second blocks sound the same as 20-second ones.
- Samples are loaded when a note first needs them and kept within a memory
  budget.
- The speech-band dip is now a peaking filter that follows the words,
  instead of whole-signal STFT masking, so it streams too.

**Styles are data:** `styles/*.toml`, one file per style, documented in
`styles/README.md`.

- A style sets its keys, tempo range, energy mapping, progressions, chord
  lengths, ostinato patterns and accents, lead instruments and arpeggio,
  the level at which each part enters, and group volumes.
- The loader checks every value and names the file and field when one is
  wrong.
- The four approved styles are files: inspiring, calm, reflective,
  cinematic. Instruments are data too (`instruments.toml`).

**Variation per video:** the seed chooses the key (from the style's list),
the tempo (within its range), the progression order (each used once before
any repeats), the ostinato patterns, the leads and the voicing register. The
seed is kept in the scene plan (`score: {style, seed}`). Twelve seeds of one
style on the same speech gave twelve different event lists, at least three
keys and six tempos (tested).

**The third music choice:**

- The settings screen offers "No music" (still the default, D-172), "Use my
  own track" and "Let Voxframe score it" with a style list. A new video
  gets a fresh seed.
- The score becomes the music stem in the render's sound stage, cached by
  the speech, style file, seed and engine version. The mix ducks and checks
  it like any music, so a mix change never re-composes it (tested).
- A plan holds a track or a score, never both.
- The credits read "Music: generated by Voxframe, with samples from
  Versilian Studios' VSCO 2 and VCSL libraries (CC0)".
- A score that cannot be made leaves the video without music and says why.

**Dependencies, as the owner decided:**

- **soundfile** (BSD-3, with libsndfile LGPL-2.1, dynamically linked) is now
  a direct dependency of the app. The mixer reads and writes stems with it
  in every render (D-171), but it had arrived only with librosa, which D-172
  made a download. So an installer built since then would have failed every
  video for anyone without the music download. It is now declared, with
  cffi (MIT) and pycparser (BSD-3) in the licence notices.
- **SciPy** stays out of the installers. The score gets it from the existing
  music download (D-172) on first use, and the progress says "Preparing the
  music score (a one-time download of about N MB)". If that download fails,
  the video is made without music, and the warning says so.

**The samples, for Stage 1,** are read from `VOXFRAME_SCORE_SAMPLES_PATH`
(the prototype's download in development). Without them the choice says the
sounds aren't installed and can't be started. The installed app gets the
pack in Stage 5.

**Measured:** a 45-second sonnet video scored "inspiring", made from start
to finish in 55 seconds (draft quality). The score alone for a 67-second
clip took about 25 seconds, against about 54 seconds per minute of audio in
round two, which included voice polish and two mixes.

**Not in Stage 1:** the command line (`voxframe make`) builds its own
pipeline, and gets the score choice later; the editor controls are Stage 2.


### D-178 · Stage 1 of the generated score approved — PROJECT OWNER'S DECISION

The owner tried "Let Voxframe score it" on the development server and
approved Stage 1 (D-177): "it sounds great". Stage 2 follows as planned
(docs/GENERATED_SCORE_PLAN.md): "New variation", changing the style after the
video is made (re-rendering only the sound), and instrument group levels and
intensity in the editor. No sample library or package is added without
asking.

### D-179 · The generated score, Stage 2: changing the music after the video is made

Stage 2 of `docs/GENERATED_SCORE_PLAN.md`, after the owner approved Stage 1
(D-178). Everything here re-renders only the sound; the pictures come from
the cache (D-171), which a test checks after every kind of change.

**The Sound card's Music section:**

- The choice: "No music", "Let Voxframe score it", or "Your track" (shown
  when the video had one; switching away keeps it with the job, so it can be
  chosen again).
- For a score:
  - **a style list,** with "Hear this style": a 12-second sample of each
    style, composed over a stand-in speech pattern (two phrases and a pause,
    so it builds and swells), made once per style and engine version and
    kept;
  - **"New variation":** a new seed, so a new piece in the same style;
  - **an intensity slider,** from much calmer to much more driving than the
    speech alone (it moves the energy mapping by up to 1.2 levels either way);
  - **levels for the five instrument groups:** piano, strings (with the
    ostinatos), percussion (with the risers and impacts), bass and pads,
    from off (-30 dB) to +6 dB.
- Undo and redo cover the music as well as the mix. Variations are seeds, so
  undo returns exactly to the previous piece, even after applying.

**Group stems:**

- The engine now renders the score as five group stems (24-bit FLAC, to keep
  the cache small) and their sum.
- Finishing is decided once, on the sum, and applied to every group alike:
  the level, the bus compression's gain curve, the fit under the voice, the
  pause ceiling and the speech-band dip, a linear filter.
- So the groups add up to the finished score (tested to 24-bit rounding),
  and a level change re-mixes them without composing again.
- The 15-second preview mixes the groups at the new levels on the fly, with
  each stretch's music level estimated from its groups' powers. Applying
  sums them at those levels into a kept file, which the mix measures exactly
  and checks like any music.

**What costs what:** group levels re-mix (about 8 s for the 45-second
sonnet). A new style, variation or intensity composes a new score, since
each is part of the score's cache key.

**Speed:** the pad is now synthesised at a quarter of the rate and
upsampled. It is filtered below 3 kHz, so nothing it keeps changes (energy
above 6 kHz: -71 dB). That made the engine faster even with five groups:
back to back on the same 67-second clip, Stage 2 took 27.5 s against Stage
1's 44.5 s, and 57.9 s against 94.0 s in a second round (the machine was
busy with other work; absolute times swung about twofold).

**Line endings:** every scripted edit now keeps each file's own line
endings, and `tests/unit/test_line_endings.py` caught the one slip, a
CRLF file written back as LF, before it was committed.

### D-180 · Stage 2 of the generated score approved; a studio interface before 0.2.0 — PROJECT OWNER'S DECISION

- **Stage 2 approved** (D-179): the owner tested changing the music after
  the video is made, on the development server.
- **Before 0.2.0, the interface is redesigned as a studio editor.** Today
  the result page means scrolling down to edit and back up to watch. The
  player is to stay fixed in view, with a tabbed side panel for every edit
  (Scenes, Captions, Sound, Style), a timeline across the bottom (filmstrip
  and transcript, aligned with the player, with room for the music lane),
  keyboard shortcuts, changes previewed in place, and clear status while
  something updates.
- **Its identity:** a distinctive, professional visual style, for example a
  dark studio theme with one signature accent colour, refined typography and
  subtle motion. Two or three directions are proposed.
- **Design first:** clickable static mockups of the editor and the upload
  screen, for each direction, before anything is built. Everything that works
  stays: accessibility, the honest notes and warnings, the upload, settings
  and progress flow, and the library and preferences screens.
- **Next after the interface:** caption animations and transitions.
- The plan is `docs/INTERFACE_PLAN.md`.

### D-181 · Voxframe's identity: Ember, with Paper as the light mode — PROJECT OWNER'S DECISION

- **Ember is Voxframe's identity:** dark graphite with an ember-orange
  accent. Lumen's smoother motion and slightly rounder panels are borrowed
  where they fit, keeping Ember's calm, neutral look.
- **Paper is an optional light mode,** switchable in Preferences and
  following the system's light or dark setting by default. It may come in a
  later step of the build. Both modes use the same colour roles, so they
  share one layout. The light mode keeps Ember's type and shapes, so the two
  are one identity.
- **The upload screen keeps "Captions in English and French".** I had
  removed it, thinking it overstated the app; the owner pointed out that it
  is true. Voxframe's scope is English and French (D-070), and detection is
  limited to them by default (D-169).
- The updated mockups are in `demo_output/interface_mockups/`, and the build
  plan (Stage B, six steps) is in `docs/INTERFACE_PLAN.md`, for the owner's
  approval before the app changes.

### D-182 · The studio: the video edited in place, with undo for every edit

Steps B2 to B4 of `docs/INTERFACE_PLAN.md` (approved, D-181). They were built
together, because the shell, the tabs and the timeline depend on one another,
and committed together after the full suite.

**The studio replaces the result page.** It fills the window under the
header, with no page scrolling:

- **The top bar:**
  - the project and how it was made;
  - the status, as text with a small mark;
  - Undo and Redo;
  - "Update video";
  - Download: the video, captions, scene plan and videos folder, and the
    scene-by-scene plan view, which stays;
  - ?, for the keyboard shortcuts.
- **The player:** fixed in view, with play, time, scrub, sound and full
  screen. A test checks it never moves while every tab is used.
- **The side panel's tabs:**
  - **Scenes:** the scene at the playhead, with today's picture choices,
    search, your own photo, camera movement, cards and adding a title;
  - **Captions:** the scene's words, each jumping the player to it, and
    caption correction;
  - **Sound:** the Sound card;
  - **Style:** how it was made, the credits, and plainly why the template
    and shape cannot change here.
- **The timeline:** a ruler, the scenes (with thumbnails, the current one
  marked, changed ones dotted), the words in their own time slots on
  alternating rows, and the music lane reserved. One playhead follows the
  video. Clicking moves the video there, it zooms, and only the words in
  view are drawn.

**Updating stays in the studio.** The status reads "Updating your
video… N%" from the job's own progress. The player reloads the new video
at the same moment, and the status returns to "Your video is ready". The
tests wait on those same words.

**Previews in place.** A scene whose picture changed shows the new picture
over the video while the playhead is in it. Any changed scene is marked
"Preview · not yet in the video", and its clip on the timeline carries a
dot.

**Undo for every edit (new).** Every edit is saved to the plan at once, so
the history is kept beside the plan:

- every saved version is kept, with a pointer to the current one;
- undo and redo restore a version exactly;
- a new edit after undo drops the redo branch;
- 60 versions are kept.

Ctrl+Z therefore undoes whatever changed last: a picture, a caption, a card,
the camera, the music or the mix. "Changes not yet in the video" became the
distance from the version last rendered, so undoing back to what the video
shows leaves none. The Sound card's own Undo still steps through sliders not
yet applied.

**Windows:** the stress test of the history found that rapid saves can be
refused for a moment ("Access is denied") while a scanner or indexer holds
the file. Replacing a file now retries briefly, for the history and for the
plan's own save.

**Keyboard:**

- Space: play or pause, or press the focused control;
- ← and →: the previous or next scene (back goes to this scene's start
  first);
- , and .: one second back or forward;
- Ctrl+Z, and Ctrl+Shift+Z or Ctrl+Y: undo and redo;
- 1 to 4: the tabs;
- + and −: zoom the timeline;
- ?: the list of shortcuts.

None of them act while typing in a field.

**Tests:**

- the studio in a browser: the player stays in view, every shortcut, a word
  and a scene seek to within a second, an edit is counted, previewed, undone
  and redone, updating stays in the studio, and the Download menu;
- the history (versions, the redo branch, pending against the rendered
  version, the cap) and the API (every kind of edit undone and redone in
  order, refused during a render);
- the existing Sound card browser tests open the Sound tab first.

### D-183 · The studio approved through B4; two additions; online music for the next release — PROJECT OWNER'S DECISION

- **The studio is approved through step B4** (D-182): "excellent". Steps B5
  and B6 continue as planned, with two additions in B5:
  1. **Panels:** the side panel and the timeline collapse and expand, with a
     button and a shortcut, to give the player more room, and resize by
     dragging their edges. Each scrolls on its own, never the whole page.
     Sizes and collapsed states are remembered between sessions.
  2. **Your own track in the studio:** in the Sound tab, a music track can be
     uploaded at any time after the video is made, heard in the 15-second
     preview, switched between it, the generated score and no music, and
     removed. Only the sound is made again.
- **A plan for the release after 0.2.0**, for approval, with no building
  yet: caption styles and animations, transitions, and a music library.
- **D-091 is reversed for that music library, in the next release.** Until
  now Voxframe never sourced music. Online search of openly licensed music
  (Openverse audio) is allowed only under the same rules as images:
  - it follows the online-search consent setting;
  - it uses the existing licence policy: no NonCommercial, no NoDerivatives,
    and ShareAlike off by default (D-071);
  - it credits every track automatically;
  - it never chooses online music without the person's action;
  - it shows a note that some openly licensed music may still trigger
    YouTube copyright claims.

  The person's own uploaded tracks are saved for reuse, like the image
  Library. Plan only for now.


### D-184 · Step B5: the upload screen, panels that make room, and your own track in the studio

Step B5 of `docs/INTERFACE_PLAN.md`, with the two additions from D-183.

**The upload screen:**

- a headline, one drop zone and the four promises that are true of every
  video, including "Captions in English and French" (D-070, D-169);
- **Recent videos** beside it, with a thumbnail, the person's own name for
  the recording, the number of scenes and the date. Clicking one opens it in
  the studio.

The steps now read Recording, Look and music, Making, Studio. Jobs are now
named after the file as the person named it, not "source.m4a". That name
also names the copy saved to the videos folder.

**Panels:**

- **Collapse and expand.** The side panel and the timeline each fold away,
  with a button in the top bar or a shortcut: `[` for the side panel, `]` for
  the timeline. The player takes the room.
- **Resize.** Each panel's edge is a handle that can be dragged. It is also
  a keyboard separator: the arrow keys move it 24 px at a time. The
  timeline's handle sets the height of the scenes lane.
- **Scrolling.** The panel, the timeline and the stage each scroll on their
  own; the page never does.
- **Remembered.** Sizes and folded states are kept in the browser between
  sessions, clamped to sensible limits. A blocked or corrupt store falls
  back to the defaults.
- **Narrow windows** stack as before, without the side handle.

**Your own track, at any time after the video is made:**

- **Adding a track.** The Sound tab offers "Add your own track…" (or "Use a
  different track…"). The track uploads, is chosen in the card, and plays at
  once in the 15-second preview, under the voice, at the card's settings.
  Nothing is applied yet.
- **The preview always plays the card's choice:**
  - a new track;
  - your earlier track;
  - no music (the voice alone);
  - the video's own music, fitted to the voice as rendered.

  For a new track, the server builds the same looped, faded bed a render
  makes, and keeps it. It measures the voice and music only over the
  stretches of speech in the window, so a preview stays quick.
- **Credit.** It is written into the credits exactly as typed, and never
  invented (D-091). Left empty, the track is credited by its file name.
- **Switching.** You can move freely between your track, the generated score
  and no music. The track switched away from stays offered.
- **Removing.** "Remove this track" stops offering it. Using and removing a
  track at once is refused.
- **Applying** saves to the plan, with undo like every edit, and re-renders
  only the sound.
- **Errors.** A file that is not sound is reported plainly ("That track
  could not be played"), never as an error page.

### D-185 · Step B6: light mode (Paper), and a Theme setting

- **Paper** sets the same roles as Ember, in warm paper and a deeper ember:
  - canvas #f4f1ea, card #fbfaf7, ink #1c1a16, accent #c8452a with white
    labels;
  - status colours darkened until every pair passes WCAG AA. Warn became
    #8f5800 and OK #24693f, because the mock-up's values were 3.8:1 and
    4.3:1 on their notices.

  `tests/unit/test_contrast.py` now checks every pair in both modes.
- **Settings → Appearance:**
  - **Follow the system** (the default): it follows the computer's setting,
    including while the app is open;
  - **Dark**;
  - **Light**.

  A choice shows at once and is kept in the user's preferences file.
- **No flash.** The server writes `data-theme` into the page as it serves it,
  so a chosen look is right from the first frame, before any script runs.
  "Follow the system" writes nothing, and the stylesheet's
  `prefers-color-scheme` rule decides.
  - A test keeps the chosen-light and followed-light blocks identical.
  - Only the three known values are ever written into the page.
- The pictures that stand for the video itself (the player, the scene card
  previews) stay dark in both modes, as the video is.

### D-186 · The next release's plan approved, and 0.2.0 prepared — PROJECT OWNER'S DECISION

- **`docs/NEXT_RELEASE_PLAN.md` is approved**, with these answers to its four
  questions:
  1. **Order:** captions, then transitions, then the music library.
  2. **Emphasis:** a suggest-emphasis button that marks the most stressed
     words. Nothing changes until the person clicks.
  3. **ShareAlike music** is off by default, like images (D-071).
  4. **The release is 0.3.0.**
- **B5 and B6 approved** after trying them ("they work well"). 0.2.0 is
  prepared from master with everything approved:
  - the language fix;
  - mix controls;
  - voice polish;
  - the music director with on-demand download;
  - the generated score, Stages 1 and 2;
  - the studio, B1 to B6.

  The public repository gets one commit and a local `v0.2.0` tag. The owner
  pushes it.

### D-187 · In 0.2.0 the generated score is offered only where its sounds are installed — PROJECT OWNER'S DECISION

The score's instrument sounds reach an installed app only with the sample
pack, which is score Stage 5 and is not built. A 0.2.0 installer would have
shown "Let Voxframe score it" as a choice that cannot be made. The owner
chose to hide it, rather than ship it disabled or hold the release for
Stage 5.

- The choice does not appear on the settings screen or in the Sound tab
  until the sounds are present. It stays visible for a video that already
  has a score.
- The release notes do not offer the score; the roadmap lists it under
  "Later".
- The privacy note's download row names only the own-track music tools.
- A browser test, with the server's answer changed to "sounds missing",
  checks that the choice is not there.

### D-188 · The captioned pictures were reused after an edit that changed only a picture

Found while taking the 0.2.0 screenshots: a render with new pictures showed
the plain backgrounds of an earlier render.

- **The cause:** the whole captioned pictures (D-171) were cached under a key
  made from the segments' file names, the transitions, the captions and the
  quality. Segment files are named by position (`scene_00001.mp4`), not by
  content. An edit that changes a segment but leaves the captions and
  timing alone therefore matched the old key: a new picture, a card's text,
  or the camera movement. "Update video" then made the old pictures again.
- **The fix:** the key is made from each segment's content (SHA-256, read in
  blocks), and `PICTURES_VERSION` is 2, so nothing cached under the old key
  is reused.
- **Tests:**
  - A new integration test renders the sonnet, changes only the title
    card's text, renders again, and compares frames. It failed on the old
    code ("the updated video still shows the old title") and passes now.
  - The sound-only update still reuses the pictures whole.
- **Who it affected:** only builds since D-171. No release had it.

### D-189 · The studio put every word late after a card; two small studio fixes

- **Word times.** The studio added the cards' durations to every word's
  time. The plan's word times are already on the video's clock, cards
  included (D-144, `ScenePlan.card_seconds_before`), exactly as the burned
  captions use them. So:
  - after a title card, the timeline's words, clicking a word, and the
    Captions tab's jumps were late by the length of the cards before them
    (3 s on the sonnet with a title);
  - the studio tests made videos without cards, so they could not see it.

  The studio now uses the plan's times as they are. The studio browser test
  makes its video with a title card, and a new test checks that clicking the
  first word lands at that word's time in the plan, to 0.1 s.
- **Scene labels over pictures** on the timeline were dark text on the
  picture in the light look, unreadable on dark pictures. Over a picture, a
  label is now white on a dark band, in both looks.
- **The music lane** read "the music lane comes with the score's editing
  stage", a development note. It now says what the music is: no music, your
  own track, or the generated score and its style.

### D-190 · PyAV is capped below 19: a fresh install could not read audio

The v0.2.0 release build failed on macOS at its first transcription:

- **The error:** faster-whisper's audio decoder raised `TypeError: open()
  got an unexpected keyword argument 'metadata_errors'`.
- **The cause:** faster-whisper 1.2.1, the latest release, opens every
  recording with `av.open(..., metadata_errors="ignore")`. PyAV 19.0.0
  removed that option; 18.0.0 still had it. Both wheels were downloaded and
  inspected, not installed.
- **Why only a fresh install saw it:** the build resolves its versions
  fresh, so it got PyAV 19. This machine had 17.1.0, so the suite passed.

**The fix:** the `transcribe` extra declares `av>=11,<19`, with a comment
saying when to lift the cap. No package is added: PyAV was already there,
through faster-whisper.

**The tests:** a new unit test reads the sonnet through faster-whisper's own
decoder, so the pair is checked wherever the suite runs, and checks the
installed PyAV is under 19.

**The release:** nothing was published from the failed tag. As the owner
chose, the public repository gets the fix as a second commit, and `v0.2.0`
is moved onto it. The owner deletes the old tag on GitHub and pushes again.

### D-191 · The v0.2.0 release build: wheels unpacked from memory, the GPL text kept here

The release run for `v0.2.0` failed on both installers:

- **Windows: the music component could not be unpacked** (`EOFError`). SciPy
  1.18.1's Windows wheel holds an empty file named exactly like the wheel
  itself. `install()` saved each downloaded wheel into the folder it unpacks
  into, so unpacking that file emptied the zip being read. Reproduced with the
  real Windows wheels, every time: it is not a network fault, so the retry
  added just before could never succeed.
  - **The fix:** each wheel is unpacked from memory, after its SHA-256 check,
    and nothing is written beside the files being unpacked. The retry is
    gone. All 26 real music wheels for Windows unpack.
  - **The test** builds a wheel holding its own name: it failed three times
    over on the retrying code and passes now.
- **macOS: gnu.org timed out** while the build fetched the GPL text for the
  bundled FFmpeg. The text is now `scripts/gpl-3.0.txt` (SHA-256 matches the
  canonical file), copied in by the build.

`v0.2.0` was moved onto the fixed commit; nothing had been published from the
failed run.

### D-192 · Your video on screen: the speaker in sync, pictures as cutaways — AWAITING THE OWNER'S APPROVAL

The first step of the Shorts plan (ROADMAP: "Then: Shorts"). A video file used
to be reduced to its sound. With **Use my video**, the speaker is on screen,
cut to the frames each scene's words were spoken over, and the matched
pictures become cutaways.

**What the plan records** (the renderer still reads nothing else, D-011):

- `ScenePlan.footage`: the recording's path, displayed size (after a phone's
  rotation), frame rate, length, `audio_offset` and `subject_x`.
- Per scene: `shot` (`speaker` or `picture`), `shot_source`, `shot_reason`,
  and `footage_start`: where the scene starts in the recording, in seconds on
  the sound's clock. Kept per scene, not derived, so cards and highlights,
  which move scenes on the video's clock, keep each scene on its own frames.
- Plans without these fields load unchanged; the plan version is not bumped.

**Sync.** A word's time counts from the first decoded sound; FFmpeg seeks a
picture from the file's start, which can be earlier (phones; AAC priming). The
seek adds `audio_offset`, measured by ffprobe from the same zero the
soundtrack uses. Footage is resampled to the grid with `fps` before scaling,
and exactly the scene's frames are written (D-013). A scene followed by a
crossfade carries on into the footage for its padding (D-097); past the end
of the footage the last frame is held.

- **Measured, not trusted:** source videos carry clapper flashes at the
  moments a tone sounds, and tests find both in the finished video: through
  cuts on either side of each clap, a chapter card, crossfades, a 9:16 crop,
  sound starting 0.4 s after the picture, a picture starting 0.3 s after the
  sound (its first frame is held until it begins), and 25 fps footage on a
  30 fps grid. Every flash lands on its tone within a frame (measured 0 to 11 ms).
  A frame-numbered source checked seeks at 25, 30 and 60 fps, frame for frame.

**Cutaways** (`plan/shots.py`), each scene saying why it shows what it shows:
the video opens and closes on the speaker; only a matched picture cuts away,
best matches first; never two cutaways in a row; at most 40% of the speaking
time; not under 1.2 s or over 7 s. A person's choice (`shot_source="user"`)
is never changed. Without an image library the speaker is on throughout.

**Framing.** Footage is scaled to cover the frame and cropped, never squashed
or letterboxed. When the video is narrower than the footage (a vertical video
from a landscape recording), the crop is centred on the speaker, found by
where the picture *moves* across sixteen pairs of frames: the head, mouth and
hands move and the room does not. No model and no new dependency; a recording
where nothing moves, or everything does, keeps the centre, as before. Face
tracking that follows a moving speaker is the next step.

**Where it shows:**

- **Upload:** the response says `has_video`; the settings screen offers
  "Use my video" / "Pictures only" for a file with a picture, on by default.
  An MP3's cover art is not a picture.
- **Studio:** every scene of such a video has "You" / "The picture"; choosing
  or adding a picture for a scene also cuts to it. Thumbnails, the timeline
  and the preview show the speaker's frame. A shot change is a pending edit
  like any other, and only that scene is rendered again.
- **CLI:** `voxframe make talk.mp4 --video`.
- **Never silent:** a file with no picture, asked to use it, makes a
  pictures-only video and says so. A recording moved since falls back to the
  scene's picture rather than failing the video.

**Not in this step:** face tracking, jump cuts, clips from a long recording,
Shorts captions and export presets: the later steps of the Shorts plan.

### D-193 · Framing that follows you, and captions clear of your face — AWAITING THE OWNER'S APPROVAL

Step 2 of the Shorts plan. D-192 placed a vertical crop once per video; a
speaker who leaned or stepped drifted towards the edge, and captions could
sit across a face filmed low in the frame.

**Finding the face.** YuNet, OpenCV's small face detector (MIT, 232,589
bytes), ships inside the package at `assets/models/`, run by OpenCV, which
the installers already carry: no new package, nothing downloaded, nothing
leaves the computer. The file is the one OpenCV's model zoo publishes: its
SHA-256 matches the zoo's git-LFS record. It works on OpenCV 5.0 at every
frame shape tried (16:9, 9:16, square).

- Frames are read four times a second at 320 pixels wide, in 30-second
  windows. Measured on 1080p at 12 Mb/s: 27 times faster than real time,
  about a minute and a half for a 40-minute talk.
- The speaker is the face nearest where the speaker was, so someone passing
  through does not take the camera; at the start, the largest face.

**The camera** (`render/motion/faces.py`) behaves like an operator, not a
tracker:

- it holds still while the face stays within a zone around the centre
  (16% of the crop's width);
- when the face leaves it, one move for one change of place, however long
  the walk: easing in from where the camera was, following the face's own
  path smoothed over a second (centred, so it neither leads nor lags), and
  settling where the face comes to rest. It starts a little early, since the
  whole recording is known in advance;
- brief misses and one-frame glitches are smoothed out before it reacts.

The path is a short list of points in the plan (`Footage.track`, D-011),
and each speaker segment's crop moves along its own stretch of it, frame by
frame, clamped inside the picture. A segment's cache key includes its
stretch, so a different path re-renders only the scenes it touches.

- **Measured in the finished video:** a face that walks across the frame
  stays within 0.09 of a vertical frame's width of the middle (0.31 with a
  single eased move, the first version, which raced ahead), and a still face
  off to one side is centred to within 0.01.

**Captions clear of the face.** For each speaker scene, where the head
reaches in the finished frame is worked out from the path, with the
renderer's own scaling and crop and a quarter of the face's height added
for the head beyond the detector's box. When it reaches into the caption
area at the bottom and the top is clear, that scene's captions go to the
top (a second caption style, `VoxframeTop`). A face filling the frame keeps
them at the bottom, where a viewer looks; a template whose captions are not
at the bottom is left alone.

**Falling back, never failing.** Without OpenCV, or where faces are found in
under 30% of the samples (slides, a screen, someone filmed from behind), the
crop is placed where the picture moves, as in D-192, then in the centre.

**Test media.** `tests/fixtures/astronaut_collins.jpg` (20 KB), a crop of
NASA's public-domain portrait of Eileen Collins, from which the tests make
recordings at run time. No video is committed.

### D-194 · Caption lines fit the frame they are in: vertical videos no longer run off the edges

Found by the owner looking at a vertical video from D-193: the captions ran
from edge to edge.

- **The cause:** a caption line ended at 32 characters whatever the frame,
  and the font is sized by the frame's height. A vertical frame is under a
  third as wide as a landscape one of the same height, so the same line took
  its whole width: "The winter morning was bright" was drawn 1059 px wide in
  a 1080 px frame whose margins leave 950. This was true of every vertical
  video, with or without the speaker on screen, and of square videos in the
  `energetic` template, whose font is larger.
- **The fix:** a line also ends when it would be wider than the frame
  allows: inside the side margins, less a backing box's padding, measured
  with the bundled Inter at the caption's size. libass sizes a font so its
  ascent and descent equal the font size; converting from the font's own
  metrics predicted 1053 px for that line against 1059 drawn, so 2% is
  allowed on top. Without the fonts, lines are limited by characters alone,
  as before.
- **Landscape is unchanged:** 32 characters fit a landscape frame easily, so
  its lines are exactly what they were, in every template (tested).
- **Tests** burn captions with libass in every template at vertical, square
  and landscape sizes and measure the lit pixels: every caption stays inside
  the side margins. On the old code, every vertical case and `energetic`
  square failed; landscape passed.

### D-195 · Checks on every pull request, on Linux, Windows and macOS

Until now the only workflow was the release, run when a version tag is
pushed: whatever broke an installer was found on release day (D-191), and
merging a pull request ran nothing at all.

`.github/workflows/checks.yml` runs on every pull request and every push to
`main`:

- **lint:** `ruff check src tests scripts`, which now passes on the whole
  repository (one long line and seven unneeded `noqa` comments fixed);
- **web:** rebuilds the web app and fails if the committed build differs
  from it (D-117);
- **tests:** the unit and integration suites on Linux (Python 3.11 and
  3.13), Windows (3.13, as the installer is built) and Apple Silicon macOS
  (3.12, as the Mac app is built), with the FFmpeg each one ships:
  `scripts/ci_ffmpeg.py` takes the Windows and Mac builds and their SHA-256
  from the installer scripts, so there is one place they are pinned. Linux
  ships none, so it takes BtbN's static build of the same release branch
  (9.0.2 today); that file is replaced as the branch gets fixes, so it is not
  pinned by checksum, and it only runs tests. Whisper's models are kept
  between runs;
- **browser:** the Playwright tests in Chromium.

Left out, and said so in the workflow: tests marked `needs_models` (the
1.5 GB picture model), `tests/eval` (measurements, not checks), and mypy
(many errors from before it was checked; its own piece of work).

Fixed on the way, so the checks start green:

- two tests used FFmpeg's `-vsync`, removed from current FFmpeg; they use
  `-fps_mode passthrough`, its replacement since 5.1;
- `test_loading_fails_once_and_is_remembered` failed in a full run but not
  alone: it replaces a function for the whole process and counted every
  call, including those from a render an earlier test left running. It now
  counts only its own thread's calls.

Run as the Linux job runs it, with FFmpeg 9.0.2: no failures; the only
errors were Whisper downloads, which the machine it was run on cannot make.

**What the first run found** (on this pull request, before merging), each
fixed at its cause:

- **Browser tests assumed the models were downloaded.** A fresh machine
  opens on "Getting ready" (D-157), which the older tests never met; they
  now pass first-run screens as a new user does ("Not now",
  `tests/browser/first_run.py`).
- **macOS:** the Python GitHub's runners provide cannot load SQLite
  extensions, so 24 library and matching tests failed although the app
  works. The checks now run on the Python the Mac app ships
  (python-build-standalone 3.12.14, `scripts/ci_python.py`, the build
  script's own pin and checksum).
- **Linux:** `torchvision` came from PyPI while `torch` came from the CPU
  index, so the two did not match; both now come from the CPU index.
- **The licence audit** read every requirement, including `colorama`, which
  `click` needs only on Windows and which is not installed elsewhere; it
  now audits what is installed, so `colorama` is audited by the Windows
  checks.
- **Windows:** Git on the runner turned every LF into CRLF at checkout, so
  the line-ending test saw every file as changed; checkout is now
  byte-exact.
- **The long-command test** built its paths under the runner's temporary
  folder, which differs in length by system (16,647 characters on macOS,
  16,155 on Windows, about 15,000 on Linux, against a threshold of 16,000).
  It now uses a fixed, realistic AppData-length path: about 13,800 to 14,700
  everywhere.

### D-196 · Caption studio: saved looks, spoken-word animation and real previews

The owner asked to build the caption expansion in D-186 and go beyond its
six modes. Eight presets now combine eight animations with colour, backing,
position, lift, size, page length, line count and emphasis controls. Spotlight
shows one word at a time; Pulse settles the spoken word into place.

The plan owns both a whole-video treatment and optional scene overrides,
plus indices into each scene's displayed words. Old plans keep their original
caption rendering. Styling enters the existing edit history as one change.
Text corrections clear stale emphasis indices, keeping the chosen treatment.

Animations follow actual word starts, including pauses, rather than adding
word durations into a second clock. Wrapping reserves emphasis space; long
unbroken words shrink to fit portrait frames. Positioning honours explicit
top/centre choices and keeps bottom captions clear of tracked faces.

The immediate browser sketch is labelled as a sketch. Exact previews are
short, neutral-background MP4s generated locally by the export's ASS/libass
renderer. They do not save a draft or change the source video. Full exports
reuse unaffected caches and keep the original recording and word timestamps.

Emphasis suggestions measure word-level RMS and duration in the local
recording. They are acoustic suggestions, not semantic understanding, and
stay in the draft until the user saves. No additional model download is needed.

Validated through API history/authentication checks, rendered tests for all
eight presets and French portrait fitting, and a browser flow that previews,
saves, undoes/redoes, applies a whole-video look and renders the result.

### D-197 · Transition studio: deliberate joins over cached, unpadded scenes

The owner asked to build the next roadmap item after merging captions.
Seven kinds are available: cut, crossfade, dip to black, slide, push, zoom
and soft blur. Slide brings the next picture over the previous one; Push
moves both. Both support left, right, up and down. Presets and duration
controls can apply to one outgoing-scene join or the whole video.

Scene overrides and a video default live in the plan. An absent choice
keeps template rules: pauses and repeated images still make automatic cuts.
A deliberate choice overrides that automatic decision, with a safety limit
of a quarter of the shorter scene. Fewer than four blend frames means cut.
Applying a video default clears scene overrides; resetting restores the
template rules. Each operation is one reversible edit-history change.

Old plans retain D-097's padded-segment renderer. Saved transition choices
use canonical, unpadded scenes: no transition changes their duration, motion
or footage timestamps. At a join, the outgoing last frame is held while the
incoming frames continue on their original clock. The short join window
replaces those incoming frames, so neither the video nor its captions lose
a frame. Sound is assembled separately and never faded by this visual effect.

Canonical scenes, trimmed body pieces and join windows have separate caches.
A kind or direction change remakes one window; duration changes also remake
its neighboring body trims, while original scenes remain cached. The video
still needs final assembly and caption burning. New cached clips are written
atomically and their actual frame counts are checked before publication.

A muted preview renders the real pair of scene pictures with the export's
join effect, about a second either side. It does not save a draft. Its sketch
is explicitly labelled, and the rendered preview contains pictures only.
The studio exposes the actual capped frame duration and explains inheritance.

Checks include all seven real exports, unchanged caption sidecars, scene
and unaffected-join cache reuse, fractional/high frame rates, speaker flash
and tone synchronization, and a browser edit/preview/undo/redo/export flow.

The join inputs explicitly restore frame rate before edge-frame padding and
before blending: FFmpeg 7 otherwise clones no frames after a trim, turning
a nominal blend into a cut. Pixel checks verify a balanced black midpoint
and a true intermediate crossfade, as well as the output frame count.

### D-198 · Reviewable pause cuts with original source clocks

The first Shorts Producer stage is a Pacing tab. Interior transcript gaps of
at least one second are offered for review, including gaps across scene
boundaries. No suggestion is selected automatically: missing transcription
is not evidence of silence. Cards and corrected-caption scenes form protected
boundaries. Removal keeps about 180 ms on each side, quantized inward to the
frame grid, and never overlaps any timed word. Fillers, false starts, hook
selection and punch-ins are separate future stages.

Saving splits retained spans, renumbers scenes and moves words to output time.
Each non-card scene stores audio_start on the original narration clock;
footage_start stays on the original footage's sound clock. This handles
already-extracted highlights whose audio and footage have different origins.
The renderer trims original audio by these positions, inserts silent card
spans and concatenates. Input timestamps are normalized before trimming to
handle delayed sound streams. Source files are never rewritten. New jump
boundaries explicitly cut even with a whole-video blend selected.

Source ranges shape the voice cache key. Long narration graphs live in a file
to avoid Windows command-length limits, using FFmpeg's file-valued option on
7+ and its legacy script option on earlier versions. Existing uncut narration
keeps its original graph path. Save is one full-plan history operation; undo
restores all cuts in that batch and redo reapplies them. Stale/duplicate choices
are rejected. Studio listening is disabled while a saved edit awaits export.

Checks cover frame grids, crossing boundaries and empty scenes, protected
captions, authentication, save/undo/redo, actual 25 fps footage exported at
30 fps, silent cards, delayed sound/picture streams, subtitle timing, and a
Chromium review/save/history/export flow. No transcription model is rerun.

### D-199 · Quoted Shorts candidates, editable word boundaries and real drafts

The second producer stage keeps a contiguous passage in source order. Up to
three candidates use sentence boundaries and visible opening cues (question,
number, explanation/contrast), aim near 30 seconds and fit 3–60 seconds.
Overlap is limited so options differ. Openings and endings are quoted from
the displayed transcript; punctuation-free endings and context-dependent
openings are flagged. These are reviewable signals, not semantic story
understanding or predicted engagement. Cards and untimed text separate
suggestion groups; an overlong sentence is not arbitrarily cut into a candidate.

The person can select first/last words and 9:16 or the current shape. A short
retains matching scene attributes and caption corrections, rebases words,
remaps emphasis and records original audio_start and footage_start independently.
It omits cards, preserves earlier jump boundaries, and keeps the source file.
Applying the selection is one full-plan history edit; undo restores the full
edit. A 24-character full-plan revision protects word indices from stale edits.

Draft previews run the actual export renderer at height 480 with source voice,
footage and captions. Added music/score fitting is deferred to final export;
saved music settings remain intact. Drafts do not save plans or edit history.
Files are keyed by the draft and source timestamps, rendered in a temporary
folder, and published only after successful verification. Playback is session
authenticated, job-scoped, range-aware, and accepts only hexadecimal ids.
Caption emphasis measurement now follows audio_start after a saved cut.

Checks include source-clock separation for highlights, frame grids, corrected
captions, card boundaries, quoted candidates, stale edits, authentication,
undo/redo, real 25 fps source footage in 30 fps vertical exports, previous pause
cuts, delayed sound streams, cache reuse, and the Chromium trim/preview/save/
history/export flow. Tests seed plans and require no transcription downloads.

### D-200 · Reversible visual direction and editable transcript beats

Three looks split a short at timed words and existing emphasis choices.
Voice and footage positions advance independently by the same frame offset;
the output duration and word order remain unchanged. Original joins survive.
Director-created internal joins are marked so changing the look can restore
its own splits and replace cadence rather than accumulating cuts. Restore
requires continuous source ranges and compatible scene settings; manual
shots, assets and pinned visual beats prevent automatic merging.

VisualBeat stores editable text (96 characters), role, look, position and
speaker zoom (1-1.25). Automatic text quotes up to eight transcript words;
manual edits are pinned. Existing shot selection supplies matched cutaways,
returns to the speaker at both ends and limits automatic image coverage.
Caption styling changes only when explicitly requested. Footage zoom uses
the existing face-following crop and participates in the segment cache key.

Text plates are ASS layers on the exact scene clock, with a short fade,
wrapped text and escaped control characters. Auto placement avoids the
caption region and the tracked face at the actual zoom. No safe zone means
no extra text; manual positioning requires preview. SRT/VTT remain spoken
captions only. These margins are generic, not platform-specific guarantees.

Direction and per-beat preview use the actual draft export without saving
history or refitting added music. Saving uses full-plan history; full-plan
revision checks protect scene indices. Checks cover words and clocks,
caption corrections, cutaway budgets, manual overrides, replacing cadence,
auto placement, actual zoomed frames, 25 fps footage at 30 fps, delayed audio,
combined pacing/selection/direction and Chromium preview/save/undo/export.
