# The web app

Voxframe's web app runs on your own computer and opens in your browser. It
does everything the command line does — turn a recording into a captioned
video — and lets you see and change every decision before the final render.
Nothing is uploaded anywhere: the "server" is a program on your machine, and
only you can reach it.

## Starting it

```bash
pip install "voxframe[app] @ git+https://github.com/Ngum12/voxframe"
voxframe web
```

Your browser opens at an address like `http://127.0.0.1:8765/?token=…`. The
token is a one-time pass for this session; once the page has loaded it is
swapped for a cookie and removed from the address bar. If the browser does
not open, copy the address from the terminal.

`voxframe web --no-open` starts it without opening a browser, and `--port`
chooses the port. It listens only on your own machine; it has no user accounts,
so `--allow-remote` exists only for use behind access control of your own.

Voxframe keeps its **library**, **cache** and **videos** in folders under the
directory you start it from (`library/`, `.voxframe_cache/`, `demo_output/`).
Set `VOXFRAME_LIBRARY_PATH`, `VOXFRAME_CACHE_PATH` or `VOXFRAME_OUTPUT_PATH`
to put them elsewhere.

## The first time

You are asked once whether Voxframe may **search online for images** — Pexels,
Pixabay and Openverse — for scenes your own library cannot fill. Nothing is
searched unless you say yes, and you can change your mind in **Settings** at
any time.

Pexels and Pixabay need a free key each. **Settings** has links to get them,
a field for each, and a **Check key** button. Keys are stored in your own
configuration folder, never in the project, and are shown masked once saved:

| system | configuration file |
|---|---|
| Windows | `%APPDATA%\voxframe\config.json` |
| macOS | `~/Library/Application Support/voxframe/config.json` |
| Linux | `~/.config/voxframe/config.json` |

With an empty library and no keys you still get a video: captions over a
calm background, and the page tells you how to add imagery.

## Your reusable music

In **Library**, open **Save a music track**, choose audio up to 200 MB, and
set its title, credit and mood. Voxframe keeps its own local copy. Search
by title or credit, filter by mood, and listen using each track's player.

After making a video, open **Sound → Choose from your music library**.
**Audition under my voice** plays a draft mix without changing the video.
**Apply to the video** fits the selected music to the full recording and
saves its credit. Render timing edits first before auditioning a new track.

Editing a track's details affects future selections. **Hide from library**
removes it from the list but keeps its audio for existing projects and undo
history. Importing the same audio again restores it with its existing details.
The Sound tab's quick upload still adds a track only to that project; save it
through Library to reuse it. Online music discovery is a later stage.

## Making a video

1. **Upload** a recording — WAV, MP3, M4A, FLAC, OGG, Opus, or a video with a
   sound track, up to 2 GB.
2. **Settings for this video** — style, shape (landscape, square, vertical),
   quality, an optional title card, chapter cards at long pauses (recordings
   over three minutes), a highlights version (recordings over five minutes),
   and optional **music**: your own track, edited to the speaker (cut on the
   beat, lowered whenever someone speaks, ending on the last word), with a
   credit if it needs one. An estimate of the render time is shown before you
   start.
3. **Render** — progress stage by stage. You can stop it. If the app or the
   computer stops mid-render, the job is marked *interrupted* and can be
   resumed from where it was.
4. **The studio** — the finished video, with everything you can change beside
   it. **Download** in its top bar has the video, its subtitles (SRT and VTT),
   the scene plan and the credits for every image and track it used.

## Changing the video: the studio

The video stays in view while you work, with a timeline of every scene and
word under it. Clicking the timeline, a word or a scene moves the video
there. Every change is kept and can be undone (**Ctrl+Z**) or redone
(**Ctrl+Shift+Z**). A changed picture shows over the video at once, marked
"not yet in the video", until you click **Update video**. Press **?** for the
keyboard shortcuts; `[` and `]` fold away the side panel and the timeline,
whose edges can also be dragged.

**Transitions.** Open the **Transitions** tab (keyboard **5**) and choose a
join between two scenes. Choose Cut, Crossfade, Dip to black, Slide, Push,
Zoom or Soft blur; Slide and Push also have a direction. Set the duration,
then **Preview this join** to see a short, muted picture preview before
saving. **Save for this join** changes one boundary; **Apply to all joins**
sets the video's default. Undo/redo covers both. **Use video default** clears
a scene override, and **Reset all to template** restores automatic choices.
Blends start at the join and keep the sound and caption clocks in place.
Short scenes cap the duration, which is shown in frames below the controls.
Save the choice, then click **Update video** to export it.

**Pacing.** Open **Pacing** (keyboard **6**) to review long gaps in the
transcript, including those crossing scene boundaries. Suggestions are
unchecked: a missing transcript word or dramatic pause is not proven silence.
**Listen at** seeks to before the gap. Choose up to 100 cuts and **Save selected
cuts**, then **Update video**. The length comparison shows what you remove.
Cuts leave about 0.18 seconds on each side, avoid all timed words, keep title
and chapter cards intact, and leave corrected-caption scenes alone. Undo/redo
restores the previous complete timeline. Listening is disabled while edits are
pending so the old video cannot be mistaken for the new timing.

**Shorts.** Open **Shorts** (keyboard **7**) for up to three passage options.
Each quotes an opening from your transcript and shows a full passage, ending,
duration and visible reasons. Ranking uses sentence boundaries, questions,
numbers and explanation/contrast cues. Read and listen before choosing: these
signals do not establish that a story is complete or predict its popularity.
Use **Find a word** and the **First word** / **Last word** selectors to adjust
boundaries. Keep **Vertical 9:16** checked, or keep the current shape.

**Render short preview** renders the actual voice, footage and captions at
draft quality, without saving changes. The preview's source ranges show both
audio and footage positions, which can differ after highlights. Added music
and generated scores are heard after final export. **Use this short** saves
a 3–60 second selection to this project, omitting title/chapter cards; **Undo**
restores the full edit, and **Redo** reapplies it. Click **Update video** for
final export. Word positions are tied to the plan version: another edit makes
old selections stale. A recording without word timings has no fabricated
suggestions. Cards and text without word timings separate suggested passages.

**Sound.** Voice polish (Polished, or Original exactly as recorded); the
voice and music levels; how far the music drops while someone speaks; and
the loudness for where the video is going. Moving a slider plays 15 seconds
from where you paused. Your own track can be added, heard under the voice,
credited, switched or removed at any time. A sound change remakes only the
sound, and every video's sound is checked.

![The Sound tab](images/studio-sound-dark.png)

**Scenes and captions.** The studio's **Scenes** and **Captions** tabs, and
the scene-by-scene plan (**Download → The scene plan, scene by scene**), offer the same
changes. Choose a scene to change it:

- **Use a close match** — an image that came just short of the bar for
  automatic use. Anything already shown elsewhere in the video, or showing
  printed text that could clash with the captions, is marked before you choose.
- **Choose another** of the images the matcher considered.
- **Search online** for exactly what you want (when online search is on). Each
  result shows its author, source and licence before you use it.
- **Use your own photo.**
- **Show a plain background instead.**
- **Edit caption** — correct what the captions say. What was actually heard
  is kept beside your correction.
- **Cards** — change what a title or chapter card says, remove a card, add a
  title, or **start a chapter here** before any scene.

**Update video** renders again, and only the scenes you changed are rendered —
the rest are reused.

**Appearance.** **Settings → Appearance** chooses Dark, Light, or Follow the
system (the default).

*Atmospheric* scenes are ones where nothing matched: they show a calm image on
the recording's overall theme, labelled so it is never mistaken for a match.

## The Library

The photos and clips Voxframe chooses from.

- **Add your own** — photos (JPEG, PNG, WebP) and clips (MP4, MOV, WebM), up to
  50 at a time. They are credited to the name you give (asked once, then
  remembered) under the licence you choose, "Own work" by default. That is what
  the credits of any video using them will say.
- **Browse** — newest first, filterable by where things came from. Every item
  shows its author, source and licence, and how many videos show it.
- **Remove** — always confirmed. The confirmation says which videos show the
  item: they keep their finished files, but updating them will need another
  image for those scenes. Files you added in the Library are deleted; images
  downloaded by a search, or folders you added from the command line, keep
  their files and only leave the library.

**Director.** Open **Director** (keyboard **8**) after choosing a 3-60 second
passage. Preview Clean authority, High energy or Cinematic story; each uses
word-boundary cuts, selective speaker zooms and transcript text beats.
Apply direction saves one undoable pass. Matching the captions is optional.
Select a scene in the timeline, return to Director, and edit its text, role,
look, position or zoom. Save beat pins it for future passes. Use Scenes to
choose Speaker/Picture or replace a cutaway. Auto text avoids captions and
tracked faces and is hidden if there is no room; preview manual placement.
Previews include voice, footage and graphics. Added music is heard after
Update video, which exports your saved edit.

**Export.** Open **Export** (keyboard **9**) to finish a 3-60 second short.
Choose YouTube Shorts, TikTok, Instagram Reels or WhatsApp Status, then Full HD
(1080 x 1920) or a smaller file (720 x 1280). Presets reserve room for app
controls; adjust the top, bottom and side guides for your device. Captions and
text beats stay within those conservative margins. The preview can show guides
but the finished video contains only your captions, text and optional progress
rail. The rail follows the video's actual frame count and accepts your accent
colour. Presets retain voice/music levels and choose a destination loudness
of -14 LUFS/-1 dBTP, or -15 LUFS/-1.5 dBTP for WhatsApp.

Preview export look leaves the saved plan intact and omits added music.
Save export settings is one undoable edit. Update video uses the saved portrait
size, fits added music and writes the finished MP4; Download saves it. Use
project output settings removes the export size, guides and rail while keeping
the current shape and sound destination. Undo restores the whole previous plan.

## Privacy and security

- The app listens on `127.0.0.1` only and checks every request's host name,
  which stops a web page elsewhere from reaching it through DNS tricks.
- Every request needs the session cookie, which pages from other sites cannot
  send or read. Responses carry a strict content security policy and send no
  referrer.
- The browser never sends a file path: uploads, images and results are named
  by ids the server issued, and every file it serves is checked to be inside
  your library or the app's own folders.
- Online search runs only with your consent and a key, and error messages name
  a service but never repeat what it said: some services put the key in the
  address.

## Scene plans from before the first release

Scene plans record the version of the renderer that made them. Plans made
before the first release are **not supported**: rendering one shows a warning,
because its captions may be out of step after a title or chapter card. Make
the video again from the recording instead.
