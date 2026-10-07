# Roadmap

What's planned for Voxframe, roughly in order. Plans can change; the
[releases page](https://github.com/Ngum12/voxframe/releases) is the record of
what has shipped. If you'd like to help with any of this, see
[CONTRIBUTING.md](CONTRIBUTING.md), and please open an issue before starting
on anything large.

## Studio expansion after 2.1.0

Three additions to the studio, built in this order.

### 1. Caption studio — implemented

- **Styles:**
  - **Highlight** (today's): the line, with the spoken word coloured;
  - **Karaoke:** the line fills with colour as it is spoken;
  - **Pop-in:** each word appears as it is spoken;
  - **Typewriter:** the line builds up word by word;
  - **Emphasis:** chosen words larger and in colour;
  - **Plain:** no animation.
- **Positions:** bottom, centre or top, with a small lift to clear a lower
  third or a face.
- **Presets:** each template has its own style, and any scene can use a
  different one.
- **Emphasis words** are yours to choose. A **suggest** button marks the words
  you stressed most, and nothing changes until you click.
- **Captions stay frame-exact.** Timing still comes only from each word's
  own timestamp.
- **Eight ready-to-use looks:** Classic, Electric, Cinema, Karaoke, Impact,
  Spotlight, Pulse and Quiet. Spotlight follows one word; Pulse gives the
  spoken word a small bounce.
- **Make a look yours:** accent/text colours, backing, size, page length,
  line count, capitalisation, position and emphasis strength.
- **Two previews:** an immediate sketch and a locally rendered preview
  using the export's caption renderer, up to the first 12 seconds of a scene.
- **Reversible:** save for one scene or the whole video; undo/redo restores
  the saved look. Re-rendering keeps the original word timestamps and audio.

**Current build: Music library — local reuse and online discovery implemented.
Shorts pacing, selection, visual direction and portrait delivery are implemented.**

### 2. Transition studio — implemented

- **Kinds:** cut, crossfade, dip to black, slide, push, zoom and soft blur.
- **Presets:** each template has its own transition, and any join between
  two scenes can be changed in the studio.
- **Frame-exact:** a transition never shifts the sound or the captions.
- **Quick to change:** saved transitions use cached scene footage and cached
  join windows. Changing a kind or direction remakes the affected join; a
  duration change also rebuilds its neighboring trim pieces. Scenes keep
  their original duration, motion and source timestamps.
- **Studio controls:** choose any join, apply a preset, set direction and
  duration, save for that join or all joins, or return to template defaults.
- **Preview and history:** render a short picture preview with the same
  effect as the export, then save. Previewing leaves the plan unchanged;
  undo/redo covers every saved transition choice.
- **Short joins stay readable:** blends take at most a quarter of the shorter
  scene, and a join too short for four frames becomes a cut.

### 3. A music library — local reuse and online discovery implemented

- **Your tracks, kept.** Library → Save a music track keeps an independent
  local copy, its title, credit and mood. Search and filter saved tracks, listen
  to them alone, or audition them under your voice from the Sound tab before
  applying. Any project can reuse them. Duplicate audio shares one copy.
- **Project-safe changes.** Each selection saves its credit into the project
  and participates in undo/redo. Editing library details affects future
  selections; hiding a track leaves saved projects and their history working.
- **Search online for openly licensed music** (Openverse), with previews and
  filters for length, mood and instrumental. You can hear a track under your
  own voice before choosing it. A separate **Allow online music search**
  switch avoids reusing consent given specifically for images:
  - **only if you've turned music search on**, and only the words you type
    are sent;
  - **the same licence rules:** nothing NonCommercial or NoDerivatives, and
    ShareAlike off unless you turn it on;
  - **every track is credited automatically:** title, author, licence and
    source;
  - **never chosen for you:** online music is only ever added by your click;
  - **a clear note** that some openly licensed music is also registered with
    YouTube's Content ID, so a video using it may still get a claim.
  - **metadata-based filters:** duration, mood tags and an instrumental tag
    apply to each result page; unknown tags are not guessed. Previewing
    downloads temporary audio; only Save or Use keeps it in the library.

The provider contract is covered with controlled responses and real audio
previews/exports. Live Openverse access remains unverified in this cloud
environment: its network proxy rejects the API connection with HTTP 403.

## Current: Shorts Producer

Short vertical videos with real editing, made from a recording **or a
video**. Built in this order, each step usable on its own:

### 1. Your video on screen

- **Use the speaker's own footage.** A video you add is no longer reduced to
  its sound: you are on screen, in sync to the frame, and the matched
  pictures cut in as cutaways when you mention something they show.
- **You choose:** "Use my video" or "Pictures only", and any scene can be
  switched between you and its picture in the studio.
- **Framed for the shape you chose.** A landscape recording made vertical
  keeps you in the frame rather than the middle of the room.

### 2. Framing that follows you

- **Face tracking**, on your computer, so a 9:16 crop follows you as you
  move, smoothly, without jitter. A download the first time it is used, like
  the music component.
- **Captions placed clear of your face.**

### 3. Jump cuts

**Delivered first:** the Pacing tab reviews long transcript gaps, including
pauses crossing scene boundaries. Choose cuts, listen around them and save;
voice, footage and captions move together. Undo/redo restores full timelines.
The Director now adds emphasis-driven punch-ins. Filler/false-start detection
remains next.

- **Silences, "um"s and false starts removed**, cut on the word timestamps,
  so sound and picture stay together. Every cut is shown and can be undone.
- **Zoom punch-ins** on the words you stress.

### 4. Clips from a long recording

**Delivered:** the Shorts tab offers up to three distinct passages with quoted
openings, full transcripts and visible reasons. Choose first/last words, preview
real voice/footage/captions in 9:16, then save a reversible short selection.
The original source clocks survive earlier pause cuts.

**Visual direction delivered:** the Director tab builds three editable looks:
Clean authority, High energy and Cinematic story. It adds word-boundary beats,
selective speaker punch-ins, existing matched-image cutaways, and opening,
emphasis and closing text quoted from the transcript. You can edit text,
placement and zoom, pin beats, preview without saving, and undo a whole pass.
Changing the look replaces the director's own joins while preserving source
cuts and manual choices. Auto text avoids captions and tracked faces; when
there is no room, the text is omitted. Added music is heard on final export.

**Export stage delivered:** the Export tab saves presets for YouTube Shorts,
TikTok, Instagram Reels and WhatsApp Status. Choose 1080 x 1920 or 720 x 1280,
adjust conservative text guides, and add a timed progress rail. Captions and
text beats use those margins; the draft shows guides without burning them into
the MP4. Each preset selects the existing destination loudness target while
retaining mix levels. Update video follows the saved output size; undo restores
both export and sound choices. The full browser flow finishes from pause cuts
through passage selection and direction to an actual downloaded MP4.

**Story auditions delivered:** Shorts now connects passage discovery to the
three visual directions. Review quoted hooks, surrounding context and the
actual ending; inspect a compact shot storyboard; audition different looks
and return to previously rendered previews. Choose the cut and direction in
one undoable edit. Speaker protection follows the actual first and last
spoken lines, including shorts with tiny wordless lead-in handles.

**Story music delivered:** Sound offers Steady bed, Cinematic rise and
Punch & breathe. Dynamics follow the edited speech clock: opening, build,
space around the final words and release into the ring-out. Hear the opening
or ending without saving, compare with voice-only, undo draft settings, then
apply a sound-only update. Works with uploaded tracks and generated scores;
older plans retain their original steady mix.

**Speech cleanup delivered:** Pacing now groups long pauses, standalone
hesitation tokens (um, uh, erm, euh), and closely repeated two-to-four-word
phrases. Review quoted removals in context; every choice starts unchecked.
Preview a 3–60 second edited story without saving, then save the selected
cuts in one undoable edit. Speech, footage, words and caption emphasis retain
their original source clocks. Corrected captions and ambiguous discourse
phrases are protected; timing overlaps are omitted rather than guessed.

**Finishing review delivered:** Export shows review cues from the saved edit:
caption alignment/pace/long words, brief spoken shots, text-layer placement,
source enlargement and recording-tail holds, printed-text imagery, and close
voice/music settings. Each cue quotes its evidence and opens the relevant
scene and controls. Filter the categories, mark intentional choices checked,
and refresh automatically after an edit. Output sizing follows saved export
settings; pending changes are clearly distinguished from the rendered video.
The review does not claim pixel inspection, semantic judgement or a quality
score. A clean report still asks for a watch and listen.

**Story comparison delivered:** After rendering two or more Shorts auditions
of the same word range and shape, compare any pair side by side. One playhead
starts, pauses and scrubs both, with drift correction and only one audible
preview. Switch the sound between versions, choose the stronger edit directly,
and save its passage, direction and captions as one undoable decision. Changing
the passage hides incompatible comparisons; closing it stops detached players.

Broader semantic false-start detection and story assessment remain later
improvements.

**Creative presets delivered:** Save a named caption treatment from an explicit
spoken scene and the project’s saved mix. Select it after a new upload; the
first render and saved plan receive a snapshot, independent of later preset
deletion. Words, cuts, footage, music files and export size stay project-specific.
Presets are local, bounded and atomically saved.

**Directed photo movement delivered:** Select zoom in/out or four pan
directions and strength for a still-image scene. Preview through the export
filter before saving; long scenes retain their original movement speed in
the bounded draft. Explicit pans use a slight crop so they actually travel
without empty edges. Automatic and hold-still choices remain available;
saves are revision checked, undoable and included in segment cache keys.

Reusable style kits now combine captions, photo motion, transitions and sound
(see Story production below).

- **Suggested Shorts:** 3 to 60 seconds, aiming near 30, with sentence-based
  candidates ranked by questions, numbers and explanation/contrast cues.
  You review the ending, trim and choose; no semantic story judgement is claimed.
- **Bold Shorts captions** (from 0.3.0's caption styles), a hook title over
  the first seconds, and a progress bar.
- **Export presets** for YouTube Shorts, TikTok, Reels and WhatsApp Status:
  a 3-60 second portrait edit, saved picture size, destination loudness, and
  adjustable guides for captions and text. App controls vary by device.

## Story production

- **Story Composer — implemented:** trim and reorder up to eight transcript
  passages, label their roles, inspect surrounding words, render a read-only
  preview and save one undoable 3–60 second edit. Source clocks keep speaker
  footage, narration and captions synchronized; overlapping speech is refused.
- **Visual story placement — implemented.** Pin speaker shots, project visuals
  and text treatments to spoken word spans. Preview the full edit and save up
  to twelve non-overlapping placements as one undoable change. Story duration,
  narration source clocks and caption timings stay intact.
- **Complete auditions with sound — implemented.** Render the current story
  with project music, a saved track or no added music; adjust the arc and mix,
  compare synchronized drafts, and choose the exact previewed treatment as one
  undoable edit. Changed source files expire old winners.
- **Signature style kits — implemented.** Save optional transition, photo and
  text treatments with captions and sound. Reuse on upload or preview the
  current short with its soundtrack, then save the exact snapshot with Undo.
- **Batch Shorts — implemented.** Shortlist up to six distinct word ranges,
  preview each with current music and a chosen look, review opening/ending
  context, and queue separate editable exports. Each clip has progress, Stop,
  Resume and download controls; repeat clicks reuse the same jobs.
- **Downloadable Shorts collections — implemented.** Name and order up to six
  finished exports, then download one ZIP containing their original MP4s,
  available subtitles, rendered credits and a manifest with file checksums.
  Missing, unfinished or changing outputs are refused; snapshots are cached.
- **Batch destination variants — implemented.** Select up to six finished clips
  and four destinations; preview each current saved edit with sound, portrait
  text guides and its loudness target. Review each immutable snapshot before
  queueing separate editable exports; collections retain rendered destinations.
- **Opening auditions — implemented.** Compare quiet confidence, word punch
  and slow reveal on the first spoken phrase. Keep current shots or choose
  speaker/project-picture framing, preview the full story with music, review
  the opening and return, then save the exact snapshot with Undo. Source clocks
  and narration stay intact; pinned opening text needs explicit replacement.
- **Next: reusable opening signatures.** Save a reviewed opening treatment and
  audition it on new recordings with their own words and pictures.

## Later

- **Music composed for your video.** A score written for each recording,
  following the speaker: it builds through sentences and swells into pauses,
  in styles such as inspiring, calm, reflective and cinematic. The engine
  exists; it is waiting on a download of its instrument sounds (openly
  licensed, CC0) for the installed app. After that come more styles and
  instruments.
- **More languages.** Voxframe is tested in English and French. A new
  language needs evidence more than code: a reference transcript and
  measurements of how well speech and pictures work in it.
- **Code-signed installers**, so Windows and macOS don't ask you to confirm
  the first time, and a Mac build for Intel Macs.
- **Install with `pip`** from PyPI.
- **Silent installs** on Windows, documented for schools and organisations.
- **A project website.**
- **Issue templates** on GitHub. (Always-on checks for pull requests are
  running: D-195.)

## Always

Whatever is added, Voxframe stays:

- **on your computer:** no account, no upload, no tracking;
- **free and open source**, under the Apache License 2.0;
- **plain to use:** it shows every choice it made, and lets you change any of
  them.
