# Voxframe — Architecture Plan

**Status:** approved with changes (2026-09-25). Phase 1 in progress.
**Target platform:** Linux, macOS, Windows. Developed on Windows 11 / Python 3.13.

Voxframe turns an audio file into a captioned, visually dynamic video. The core
pipeline runs fully offline on CPU after a one-time model download. No API keys,
no paid accounts, no cloud dependency.

---

## 1. Guiding principles

1. **Offline-first is a constraint, not a goal to approximate.** Every required
   dependency runs locally. Network access is needed only to fetch models once,
   and for sourcing adapters that are off by default.
2. **Stages are pure functions over typed models.** Each pipeline stage maps
   `input model -> output model` with no hidden state. Caching, resume, testing
   and the scene-plan edit loop all fall out of this rather than being bolted on.
3. **One owner per external system.** Only `render/compose` and `render/encode`
   invoke FFmpeg. Only `library/` touches SQLite. Only `sourcing/` makes network
   calls. This keeps the blast radius of any change small.
4. **The scene plan is the product.** The renderer consumes only the plan. Hand
   editing the JSON, editing it in the web UI, and regenerating it are the same
   operation as far as rendering is concerned.
5. **Degrade, never fail.** No GPU, no API keys, no depth model, a missing font:
   each degrades to a working lesser result with a clear log line.
6. **Claims are earned by measurement.** Where this document asserts a quality
   property (no drift, smooth motion, no generation loss), a named test enforces
   it. An unverified claim is a to-do, not a property.

---

## 2. Repository layout

```
voxframe/
├─ src/voxframe/
│  ├─ config/          settings (pydantic-settings), style templates, presets
│  ├─ models/          pydantic domain types — contracts between stages
│  ├─ transcribe/      faster-whisper wrapper, VAD, language detect, cache
│  ├─ segment/         transcript -> scenes (pauses, sentences, topic shift)
│  ├─ timeline/        frame grid: the single authority on time -> frame
│  ├─ library/         SQLite + sqlite-vec, ingest, embeddings, dedupe
│  ├─ sourcing/        adapter protocol + pexels/pixabay/unsplash/openverse
│  ├─ match/           scene -> asset selection, repetition + consistency rules
│  ├─ plan/            ScenePlan builder, JSON schema, load/validate/diff
│  ├─ director/        optional LLM providers (anthropic, ollama, null)
│  ├─ render/
│  │   ├─ captions/    ASS generation, styles, line-breaking, safe areas
│  │   ├─ motion/      Ken Burns solver, saliency, depth scoring, parallax
│  │   ├─ compose/     FFmpeg graph builder, per-scene segments, transitions
│  │   ├─ ffpath/      FFmpeg filter-argument escaping (cross-platform)
│  │   └─ encode/      encoder probe + selection, quality presets
│  ├─ jobs/            job state, content-addressed cache, resume, progress
│  ├─ storage/         StorageBackend protocol; LocalStorage implementation
│  ├─ cli/             typer app
│  └─ api/             FastAPI (Phase 8)
├─ web/                Next.js (Phase 8)
├─ assets/fonts/       bundled OFL fonts
├─ tests/              unit + golden-frame + end-to-end
├─ samples/            short sample audio + images (small, committed)
├─ demo_output/        render output — gitignored, never committed
├─ docs/
│  ├─ architecture.md  this file
│  └─ phases/          screenshots used by the guides
├─ DECISIONS.md  LICENSE  THIRD_PARTY_LICENSES  Dockerfile
├─ setup.ps1  setup.sh  .env.example
```

---

## 3. Data model

```
Word          text, start, end, confidence
Transcript    words[], language, duration, model_id, audio_hash
Scene         id, start, end, start_frame, end_frame, text, words[],
              emphasis[], pause_after
Asset         id, path, kind(image|video), sha256, width, height, embedding,
              tags[], colors[], source, author, license, source_url, added_at
DepthQuality  score, separation, confidence, usable(bool), reason
PlannedScene  scene + asset_id + alternatives[] + motion + motion_reason
                    + transition + caption_style
ScenePlan     version, audio_hash, fps, format, style, scenes[]
Credits       entries[] (asset, author, license, url)
```

`ScenePlan` is a versioned JSON document with a published schema. It is the
single input to rendering and the single artifact a user edits to control
output quality.

### Provenance is mandatory

Every `Asset` carries `source`, `author`, `license` and `source_url` from the
moment it enters the library. An asset without provenance cannot be ingested.
This makes the credits file a pure projection of the library rather than
separate bookkeeping that can drift out of sync with what a render used.

---

## 4. The frame grid

*(Correction 1. This section is the precondition for any no-drift claim.)*

A single global frame grid owns all time-to-frame conversion. Every boundary in
the system is computed once, through `timeline/`:

```
start_frame = round(t * fps)
duration_frames = next.start_frame - this.start_frame
```

Rules:

- Scene boundaries are stored in the plan as **frames**, not floats. Seconds are
  derived for display only.
- A segment's duration is defined by the *next* scene's start frame, never by
  its own rounded duration. Rounding error therefore cannot accumulate: each
  boundary is absolute, not relative to its predecessor.
- The final scene's end frame is `round(audio_duration * fps)`, so the video
  length is pinned to the audio, not to the sum of the parts.
- No scene may be shorter than 1 frame; the segmenter merges anything smaller.

**Enforcing test (`test_frame_grid_no_drift`).** On a long sample (target: 30+
minutes), assert that the total frame count of the concatenated video equals
`round(audio_duration * fps)` within one frame, and that the sum of segment
durations equals the total exactly. Until this test passes, this document makes
no claim that drift is impossible — only that the design intends to prevent it.

---

## 5. Rendering approach

### 5.1 Per-scene segments, then concat

Each scene renders to an independent intermediate segment; segments are
concatenated. Peak memory stays flat regardless of audio length, every scene is
independently cacheable (which is what makes Phase 6 resume work), and a
re-render after editing one scene touches only that scene.

### 5.2 Audio

Audio is laid in **once, at the end**, from the original file. Never sliced,
never re-encoded per segment. Combined with the frame grid above, this is what
prevents drift.

### 5.3 Transitions with independent segments

*(Correction 2. Specified now, implemented in Phase 5.)*

A crossfade needs frames from two scenes at once, which independent segments do
not naturally provide. Approach: **dedicated transition segments.**

For a transition of `D` frames between scenes A and B:

1. Scene A renders its full length, then is **trimmed** to end `D` frames early.
2. Scene B renders its full length, then is **trimmed** to start `D` frames late.
3. A separate transition segment renders from A's final `D` frames and B's
   first `D` frames via `xfade` (verified present in the target FFmpeg build).
4. Concat order: `A_trimmed, AB_transition, B_trimmed, ...`

Consequences, handled explicitly:

- **The frame grid absorbs the transition.** The transition's `D` frames are
  drawn from the scenes' own allocations, so total frame count is unchanged.
  Transition length is clamped so no scene falls below a minimum visible
  duration; the plan records any clamping.
- Motion continues through the transition: A's tail and B's head render with
  their own motion curves at the correct phase, so a crossfade does not freeze.
- A hard cut is `D = 0` and produces no transition segment.
- Transitions are chosen against speech pauses; a transition never begins
  mid-word. The segmenter supplies pause locations.

### 5.4 Intermediate format

*(Correction 4. Measured, not assumed.)*

Benchmarked on this machine, 3 s of 1080p30 synthetic `testsrc2`:

| Codec | Size (3 s) | Extrapolated 10 min | Encode |
|---|---|---|---|
| FFV1 (lossless) | 9.6 MB | ~19 GB | 536 ms |
| libx264rgb (lossless) | 26.6 MB | ~53 GB | 559 ms |
| **libx264 CRF 16** | **3.9 MB** | **~7.6 GB** | 718 ms |
| libx264 CRF 18 | 3.7 MB | ~7.2 GB | 1683 ms |
| UT Video (lossless) | 43.8 MB | ~87 GB | 1275 ms |

Synthetic content compresses differently from photographic content, so these are
ratios rather than absolutes.

**Decision: `libx264 -crf 16 -preset veryfast`, yuv444p, default.** One
subsequent encode (the caption burn) will not expose CRF 16 artifacts, and true
lossless costs ~2.5x the disk. `--intermediate=ffv1` is available for users who
want a mathematically lossless path.

yuv444p for intermediates specifically: caption edges and fine text suffer from
chroma subsampling, and the final output converts to yuv420p once at the end.

**Enforcing test (`test_intermediate_generation_loss`).** Render a
high-detail sample through the full two-pass path, compare against a single-pass
reference with VMAF and SSIM, and assert VMAF >= 95. If CRF 16 fails the bar,
escalate to FFV1 by default and record the change here.

### 5.5 Captions

Burned in a single pass over the concatenated video from a generated `.ass` file
through libass. Word-level highlighting uses ASS inline timing tags, so caption
timing derives directly from word timestamps.

**Windows path escaping — verified experimentally.**
*(Correction 8. Tested before writing, because the failure mode is misleading.)*

| Form | Result |
|---|---|
| `ass=C:\path\to\sub.ass` | **FAILS** — colon parsed as option separator, backslashes stripped |
| `ass=C\:/path/to/sub.ass` | **FAILS** — escaping the colon alone is not sufficient |
| `ass=filename='C\:/path/to/sub.ass'` | **WORKS** |

The required form needs *all three*: single-quoted `filename=`, an escaped drive
colon, and forward slashes. The naive failure reports a bogus
`Unable to parse "original_size"` error that points nowhere near the real cause,
which is why this is centralised in `render/ffpath/` with unit tests covering
drive letters, spaces, apostrophes and non-ASCII characters. No other module
builds filter paths by hand.

### 5.6 Motion

Affine moves (Ken Burns) use FFmpeg `zoompan`. Saliency detection aims the move
at the subject rather than dead space.

**zoompan jitter is a known risk.** *(Correction 3.)* `zoompan` computes pan
offsets with integer pixel rounding, which on slow zooms produces visible
stepping. Phase 3 includes an explicit gate:

**Gate (`test_zoom_smoothness`, Phase 3).** Render a slow zoom (1.0 -> 1.08 over
8 s), extract consecutive frames, and measure the per-frame centroid delta of a
registered feature. Assert monotonic sub-pixel progression with no frame
repeating its predecessor's offset. Inspect the frames visually as well.

If `zoompan` fails the gate at acceptable speed, fall back in this order, and
record the reason in DECISIONS.md:

1. Oversample: render at 2x scale, animate on the larger grid, downscale
   (sub-pixel motion becomes integer motion at 2x).
2. `scale` + `crop` driven by frame-indexed expressions.
3. numpy compositing with proper Lanczos resampling (slowest; already required
   for parallax, so the code path exists).

### 5.7 Parallax, and when not to use it

*(D-007 as amended: parallax appears only where it looks good.)*

Pipeline: depth map -> layer slicing -> inpainting of disoccluded regions ->
per-layer offset composite.

Because only the Apache-2.0 **Small** depth checkpoint is permitted (see §7),
depth maps are coarser and parallax must be applied selectively:

1. **Displacement cap.** Maximum parallax offset is capped as a fraction of
   image width, so disoccluded regions stay small enough to inpaint invisibly.
   The cap is a style-template parameter with a conservative default.
2. **Depth quality score.** Each image's depth map is scored for foreground/
   background separation (bimodality of the depth histogram), edge agreement
   with the RGB image, and confidence. Produces a `DepthQuality` record.
3. **Automatic fallback.** Below threshold, the scene silently uses Ken Burns
   instead. The plan records `motion_reason` (e.g.
   `parallax_rejected: depth_separation 0.21 < 0.45`) so the user can see why
   and override it by editing the plan.
4. **Plan-level override.** Forcing parallax on a rejected scene is always
   permitted; the plan is authoritative.

This makes parallax a feature that appears where it earns its place, rather than
a uniform effect that sometimes tears.

### 5.8 Encoder selection and output formats

*(Correction 5.)*

Encoders are **probed at runtime**, never assumed. Verified on this machine:
`libx264`, `libx265`, `libvpx-vp9`, `libaom-av1`, `h264_nvenc`, `h264_qsv`,
`h264_amf` present; **`libopenh264` and `libsvtav1` absent** — which is exactly
why the chain probes rather than assumes.

Fallback chain, each step logged clearly:

```
h264_nvenc -> h264_qsv -> libx264 -> libopenh264 -> libvpx-vp9 -> libsvtav1 -> libaom-av1
```

Draft presets prefer hardware. Final-quality presets default to `libx264` CRF,
because NVENC at 4 GB VRAM gives up meaningful quality per bit.

**Royalty-free output is a first-class option**, not a fallback:
`--codec vp9` (WebM) and `--codec av1` are explicit choices. Users who want to
avoid H.264/H.265 patent-pool exposure entirely can do so. Documented in the
README Licensing section.

---

## 6. Licensing posture

**Voxframe is licensed Apache 2.0.**

### 6.1 FFmpeg is invoked, not linked

Common FFmpeg builds are GPL-3.0 (`--enable-gpl`, x264, x265). Voxframe never
bundles, links against, or statically incorporates FFmpeg. It locates and
invokes the installed binary as a subprocess, exchanging data over files and
pipes. This arm's-length invocation does not place Voxframe's own source under
GPL copyleft.

The **Docker image** bundles FFmpeg and is therefore a GPL composite
distribution. Documented in the image and in `docs/install.md`, with a
corresponding source offer. The PyPI package carries no GPL obligation.

### 6.2 Why Apache 2.0 — stated accurately

*(Correction 6. An earlier draft of this document claimed the patent grant
"matters for a codec-adjacent project." That conflated two unrelated things and
was wrong.)*

Apache 2.0's patent grant covers **only patents held by contributors to this
project**, granted to users of this project. It protects users from a
contributor later asserting their own patents against them.

It provides **no protection whatsoever** against third-party codec patents.
H.264 and H.265 are covered by patent pools (MPEG LA / Via LA, Access Advance)
whose licensing is entirely independent of Voxframe's license and of FFmpeg's.
No open-source license can grant rights the licensor does not hold.

Apache 2.0 is chosen for the contributor patent grant, the explicit trademark
clause, and the NOTICE mechanism — all of which matter for a project expecting
outside contributors. Not for codec patents, which it does not address.

**Practical position on codec patents,** documented in the README Licensing
section in plain language:

- Distributing *encoded output* is generally not the concern; commercial
  distribution of *encoders/decoders* at scale is where pool licensing applies.
- Voxframe ships no codec. It invokes the user's FFmpeg.
- Users wanting to avoid the question entirely should use `--codec vp9` or
  `--codec av1`, both royalty-free by design.
- This is informational, not legal advice, and is labelled as such.

### 6.3 Attribution artifacts

- `LICENSE` — Apache 2.0.
- `THIRD_PARTY_LICENSES` — every runtime dependency and every model checkpoint
  with its license text, generated and verified in CI.
- `README` **Licensing** section — plain language: what Voxframe is licensed
  under, the FFmpeg/GPL relationship, what the patent grant does and does not
  cover, codec patent basics, and the royalty-free output options.

---

## 7. Dependencies and licenses

*(Correction 7. Includes model-loading and inpainting dependencies.)*

### Core runtime

| Purpose | Library | License |
|---|---|---|
| Transcription | faster-whisper | MIT |
| Transcription backend | ctranslate2 | MIT |
| Whisper weights | Systran/faster-whisper-* | MIT |
| Embeddings | open_clip_torch | MIT |
| CLIP weights | LAION ViT-B-32 (laion2b) | MIT |
| Tensors | torch | BSD-3-Clause |
| Vector index | sqlite-vec | Apache-2.0 / MIT |
| Arrays | numpy | BSD-3-Clause |
| Imaging | Pillow | MIT-CMU |
| CV ops, inpainting, saliency | opencv-python-headless | Apache-2.0 |
| Models / validation | pydantic, pydantic-settings | MIT |
| CLI | typer | MIT |
| Progress / TUI | rich | MIT |
| Logging | structlog | Apache-2.0 / MIT |
| Sentence segmentation | pysbd | MIT |
| Audio I/O probe | soundfile (libsndfile) | BSD-3 (LGPL-2.1 lib) |

### Depth stack (Phase 5)

| Purpose | Library | License | Note |
|---|---|---|---|
| Model loading | transformers | Apache-2.0 | `AutoModelForDepthEstimation` |
| Tokenizer/config deps | huggingface-hub, safetensors | Apache-2.0 | pulled by transformers |
| Image preprocessing | torchvision | BSD-3-Clause | |
| Depth weights | **Depth-Anything-V2-Small** | **Apache-2.0** | only permitted checkpoint |

**Inpainting.** Phase 5 starts with OpenCV `INPAINT_TELEA` / `INPAINT_NS`
(Apache-2.0, already a dependency, no extra model). Adequate for the small
disocclusions the displacement cap guarantees. If insufficient, evaluate
LaMa (Apache-2.0) — license-compatible but adds a model download; decision
deferred to Phase 5 with measurements.

### Interfaces

| Purpose | Library | License |
|---|---|---|
| API | fastapi | MIT |
| ASGI server | uvicorn | BSD-3-Clause |
| Frontend | Next.js, React, Tailwind | MIT |

### External tools and assets

| Purpose | Item | License |
|---|---|---|
| Assembly | FFmpeg (external binary) | GPL-3.0 — see §6 |
| Fonts | Inter, Montserrat, Source Serif 4, Bebas Neue | OFL-1.1 |

### Wheel availability on Python 3.13 — verified

Confirmed by downloading the actual wheels:

- `open_clip_torch` 3.3.0 — `py3-none-any` (pure Python)
- `sqlite-vec` 0.1.9 — `py3-none-win_amd64` (no C-extension ABI tie)
- `opencv-python-headless` 5.0.0.93 — `cp37-abi3` (stable ABI, covers 3.13)
- `pysbd` 0.3.4 — `py3-none-any`

Declared support: **3.11 – 3.13**.

### Excluded, and why

- **Remotion** — license restricts use by companies above a size threshold.
- **MoviePy** — shells to FFmpeg anyway while holding clips in memory, which
  conflicts with the 60-minute audio requirement.
- **Depth-Anything-V2 Base / Large** — **CC-BY-NC**, non-commercial. Never
  auto-downloaded.
- **OpenAI CLIP weights** — usage terms we do not want to propagate.

---

## 8. Verification discipline

Every rendering-related change is verified before being reported as working:

1. Render a sample through the real pipeline.
2. Extract frames at scene boundaries, mid-scene, and across every transition.
3. Inspect the frames.
4. Fix, re-render, and only then report.
5. Generate a PNG contact sheet into `docs/phases/` as the durable record.

Backed by golden-frame tests using perceptual hashing with tolerance.

### Named quality gates

| Test | Phase | Asserts |
|---|---|---|
| `test_frame_grid_no_drift` | 2 | Total frames == round(audio_dur * fps) +/- 1 on 30-min sample |
| `test_ass_path_escaping` | 2 | Windows drive letters, spaces, apostrophes, non-ASCII |
| `test_intermediate_generation_loss` | 2 | Two-pass VMAF >= 95 vs single-pass reference |
| `test_zoom_smoothness` | 3 | Slow zoom: sub-pixel monotonic, no repeated offsets |
| `test_transition_frame_accounting` | 5 | Transitions do not change total frame count |
| `test_depth_quality_fallback` | 5 | Poor depth maps fall back to Ken Burns, reason recorded |
| `test_e2e_short_sample` | 2+ | Full pipeline on sample audio, no black frames |

---

## 9. Phase order

| Phase | Content |
|---|---|
| 1 | Skeleton, dependency + license audit, CLI stub, config system, samples |
| 2 | Frame grid, transcription, segmentation, captions, **style template schema** |
| 3 | Library, embeddings, matching, scene plan, Ken Burns (+ smoothness gate) |
| 4 | Sourcing adapters, license tracking, credits, stock video clips |
| 5 | Parallax + depth gating, transitions, style templates, ducking, cards |
| 6 | Long audio: chaptering, caching, resume, highlights mode |
| 7 | Optional AI director (Anthropic, Ollama, null provider) |
| 8 | Web app: FastAPI backend + Next.js frontend |
| 9 | Documentation and launch readiness |

Style template *schema* moved from Phase 5 to Phase 2 (D-006): captions are the
first visible output, and retrofitting the abstraction later would mean
refactoring every caption, font and pacing constant from Phases 2–4.

---

## 10. Session continuity

Each session assum## 10. Project records

- `DECISIONS.md` — every significant choice: what was decided, why, and what it
  rules out. Code comments refer to its entries by number (`D-013`).
- `demo_output/` is gitignored. No rendered video or large media is ever
  committed. Sample inputs in `samples/` stay small.

---


| Component | Value |
|---|---|
| CPU | i7-1260P, 12 cores / 16 threads |
| RAM | 31.7 GB |
| GPU | NVIDIA T550 Laptop, 4 GB VRAM |
| Disk free | 336 GB |
| FFmpeg | 8.1, libass / freetype / harfbuzz / fribidi |
| Encoders present | libx264, libx265, libvpx-vp9, libaom-av1, NVENC, QSV, AMF |
| Encoders absent | libopenh264, libsvtav1 |
| Filters confirmed | ass, subtitles, zoompan, xfade, scale2ref, minterpolate |
| torch | 2.7.1+cpu — CUDA not currently enabled |

The CPU path is the tested default. GPU is auto-detected, never required.
