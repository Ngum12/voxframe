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
through Library to reuse it.

**Discover openly licensed music** is available in Library and the Sound
picker. Turn on **Allow online music search** there, type your own search,
and review the results. Image-search consent does not enable music search.
NonCommercial and NoDerivatives material is excluded; ShareAlike requires
its separate checkbox. Only the words you type go to Openverse. Your
recording stays on this computer.

Length, mood and instrumental filters use each page's source metadata, so
missing tags can mean fewer results. Use the page controls or loosen filters.
**Preview track** downloads temporary audio and plays up to 15 seconds;
**Hear under my voice** auditions it locally without saving a project edit.
**Save to library** keeps an owned copy. **Use this track** also chooses it
in Sound; **Apply to the video** saves the selection and updates the export.

Credits automatically include the title, creator, license and Openverse
source page, which links the original provider and license deed. Choosing an
online result whose audio is already saved refreshes its library credit from
that source; existing projects keep their credit snapshots. Source metadata
is retained beside the owned audio. Search results expire after 15 minutes;
old temporary downloads are cleared on later searches after 24 hours.
Saved tracks stay available with online search off. Some openly licensed
tracks are registered with YouTube Content ID and can still receive a claim.

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

## Editing on different screens

The studio shows all nine tools in a three-column grid. With a tool focused,
use Left/Right for adjacent tools, Up/Down for the row above/below, and
Home/End for the first/last tool. Number shortcuts 1–9 still select tools.

On larger screens, scroll the editor while the player stays in view. On
narrow screens or short windows, the player and editor stack: scroll the page,
and the tool grid stays at the top while you work. Switching tools returns to
the start of its controls. Saved panel sizes adapt to available space. Each
new screen opens at the top, and the first keyboard link skips to its content.

## Delete an unwanted project

On **Make a video**, find the project under **Recent videos** and click its
**Delete** control. The confirmation names the project; choose **Cancel** to
keep it or **Delete project** to permanently remove it.

Deletion removes the recent entry, its edits, previews and private working
files. It keeps your original recording, shared image/music libraries and
videos saved outside the project, including downloads and the videos-folder
copy. Delete those separate copies yourself if you no longer want them.

A rendering project cannot be deleted until its worker stops. If VoxFrame
cannot save the deletion, the project stays intact. If Windows or another
program locks working files, the app reports that some files remain in app
storage even though the recent entry was removed.

## When online previews cannot play

For online scene search, choose Photos or Video clips. Pexels and Pixabay
video searches require their free API keys in Settings. Openverse remains
available without a key for photos and openly licensed music.

A clip preview downloads a temporary source and plays up to 15 seconds with
sound muted. Choose **Use this** and then **Update video** to include it in
the export. The clip's original audio is excluded, so your voice stays clear.

Music previews download audio from the original host. Some hosts refuse
access or no longer provide the file even when a search result is listed.
Try another result, or download the track from its original source and upload
it yourself. If automatic playback is paused by your browser, press Play in
the visible player. Previewing does not save or apply a track.

If an older export has repeating background speech, install the updated
VoxFrame and choose **Update video**, then download the new MP4. Updating
regenerates the polished voice without looping samples of the recording;
a previously downloaded copy cannot change automatically.

## Fitting clips to scene time

Selected video clips fit the scene automatically. Longer clips are cut to
the scene's length. Shorter clips use gentle slow motion up to 2.5 times,
then hold the last frame if more time is needed. Small gaps are also filled
with the last frame. The narration and captions keep their original timing.

After installing this fix, choose **Update video** on a project that failed
with a transition frame-count error. Older cached segments are rebuilt
automatically; you do not need to delete the project or clear its cache.

## Auditioning a story

Open **Shorts** after your recording is made. Suggested passages quote your
opening and ending, show the opening's length, and include nearby transcript
context under **Read passage and ending**. Review context flags before
choosing: a question or number is a cue, not a guarantee that a passage works.
You can still select the first and last words yourself.

Under **Audition a creative direction**, choose Clean authority, High energy
or Cinematic story. Optionally match captions to the look. **The edit, beat by
beat** shows the planned speaker shots, supporting visuals, text beats and
framing. Expand all beats or inspect why a shot was chosen. A video recording
is needed for speaker shots; an audio-only project keeps its visual scenes.

Use **Render short preview** for each look you want to try. Switching back
shows that look's already-rendered preview. Changing words, orientation or
caption matching selects a different audition. Previewing leaves the saved
plan intact. Music is heard after the final Update video.

**Use this short** saves the selected passage and direction together. One
**Undo** restores the original edit. Choose **Update video** for the final
export, then download your MP4.

### Shape music around the story

In the studio's Sound tab, choose **Steady bed**, **Cinematic rise**, or
**Punch & breathe**. The selected music gets a level arc across the edited
speech, with room around the final words; the existing voice protection and
music timing still apply. Choose **Hear the opening** or **Hear the ending**
to compare, and use **Voice only** to check the recording. These previews do
not save changes. **Undo** and **Redo** restore draft choices; **Apply to the
video** saves the arc and updates the sound while keeping the pictures.

The arc works with an uploaded track or an already generated score. Apply a
new score style or variation first to hear its finished story mix. Older
projects default to Steady bed. These controls shape loudness through the
story; they do not infer meaning or rewrite the music.

### Review hesitation and repeated phrases

The studio's **Pacing** tab groups long pauses, hesitation words and repeated
phrases. Every suggestion starts unchecked. Read the words that would be
removed, check the surrounding context, and use **Listen at** to hear the
original. A repetition may be intentional; keep it when it serves the story.

**Preview selected cuts** renders the shortened 3–60 second story without
saving. Changing the selection clears the previous preview. Added music is
heard after updating the video. **Save selected cuts** records the batch;
**Undo** restores the original recording clock and captions, and **Redo**
reapplies it. Update the video to make the finished mix and continue listening
on the new timeline. Corrected captions and overlapping word timings are
protected rather than guessed.

### Give the finished edit one last review

Open **Export** to see **Finishing review**. It checks the saved edit's caption
timings and font width, source size and duration, text placement and added
music settings. Every cue explains its evidence; **Review in…** opens the
relevant scene and controls. Filter by captions, framing, timing or sound.
Mark an intentional choice checked; those marks survive panel navigation in
this browser session and reset when the saved edit changes.

Save export settings before reviewing a different output size. When edits
are pending, update the video before judging picture and sound playback.
The report uses saved metadata and does not inspect pixels or understand the
story. Even when no cues appear, watch and listen before sharing the MP4.

### Compare story edits side by side

In **Shorts**, render at least two creative directions for the same passage
and picture shape, then choose **Compare rendered edits**. Pick versions A
and B. **Play comparison** and the shared playhead control both pictures;
**Hear** chooses which version supplies sound. One preview is always silent.
Changing a version resets the comparison to the opening.

Choose **Use version A** or **Use version B** to save that exact passage,
direction and caption choice. Undo restores your previous edit. Update video
for the finished music mix and export. Different word ranges or shapes need
their own auditions; closing the comparison stops both players.

### Reuse your signature look

In **Export**, expand **Keep this as your signature preset**, name it and
choose the spoken scene whose caption look you want. **Save creative preset**
keeps that treatment and the project’s saved sound mix on this computer.

After uploading a new recording, choose it under **Your signature look**.
Caption sizing follows the chosen style. Choose a track or generated score
separately; words, emphasis, cuts, footage and export size are not reused.
**Manage this preset** lets you delete a choice. Existing projects keep their
snapshotted settings, including when resumed.

### Direct the camera over a photo

In **Scenes**, choose a scene showing a still photo. **Direct the camera**
offers automatic movement, hold still, zoom in/out and pans in four directions.
Explicit moves have a strength control; zero holds the full frame. Pans crop
slightly so there is room to travel.

**Preview camera movement** shows a silent picture draft through the export
filter. It includes up to the first 12 seconds at the scene’s original speed.
Captions and sound appear in the finished video. **Save camera movement**,
then **Update video** to render it. Undo restores your previous choice.
Video clips and speaker shots use their separate motion controls.

## Story Composer

Open **Shorts → Story Composer · build your sequence** after transcription.
Start with the proposed transcript order, or add individual sentence passages.
Assign Hook, Key point, Payoff or Ending, trim the first and last word numbers,
then use Move up, Move down or Remove to shape the sequence. Roles are your
labels, not a promise of automatic story understanding. Review surrounding
words so moving a passage does not change what the speaker meant.

A sequence contains one to eight non-overlapping passages and lasts 3–60
seconds. Duplicate words are refused; adjacent cuts divide any shared audio
padding so it cannot repeat. Speaker footage, narration and captions retain
their source timing when passages move. Title and chapter cards are omitted.
The composer uses the current edited transcript; Undo restores the previous
sequence when you need a passage removed by a saved edit.

**Preview story sequence** renders actual footage, voice and captions without
saving. Added music is heard after **Update video**. **Use this sequence** saves
one undoable edit, keeping the original upload. Update video, review and
download the result. Reopening the composer restores the saved passage roles.

## Placing visuals on your story

After composing a 3–60 second story, open **Director → Place visuals · choose
when they appear**. Add a placement and select its first and last words. The
quoted passage confirms what you selected. Choose **Speaker** to return to
your recording, **Supporting visual** to show an image or clip already selected
in the project, or **Keep current shots** to change only the text treatment.
Speaker requires footage; supporting visuals must first be added in Scenes.
The selected visual's thumbnail and credit appear before you commit.

Enable **Set a text treatment for this span** to write text, select its look,
position and role. Leaving text empty explicitly clears text for the selected
span while captions keep playing. Disabling the treatment preserves the
existing text. Auto placement can hide text if there is no room; inspect the
preview before saving. Speaker zoom can be refined in **Edit selected beat**.

Up to twelve placements may be saved together. Placements cannot overlap or
cross title/chapter cards. Tight word timings without a safe frame boundary
are refused; choose a wider passage. Adjacent placements share their padding.
The story keeps its duration, voice and caption timestamps. Your chosen shot
and text are pinned for later direction passes.

**Preview visual placements** renders the full story without changing the
project. Expand **Review rendered shot sequence** to inspect the draft's shot
and text choices. Added music is heard after **Update video**. **Use visual
placements** saves one undoable edit; Update video then renders it. Review
current shot sequence shows saved choices when you reopen the panel.

## Complete auditions with sound

Compose a 3–60 second story, then open **Director → Complete auditions ·
picture, captions and sound**. **Current picture edit** keeps your visual
sequence. Clean authority, High energy and Cinematic story suggest a visual
look, matching captions, music arc and starting level. Pinned shots and text
survive; review the captions checkbox and adjust the sound before rendering.
Auditions use the entire current story, including any reordered passages.

Choose the project's track or generated score, **Saved track** from your music
library, or **No added music**. Search saved tracks by title or credit; results
show up to 60 tracks. Adjust Steady, Rise or Punch, music level, distance under
speech and voice polish. Other mix settings retain the project's choices.
A new track must first be imported or saved in Library. No added music removes
an added soundtrack; music already embedded in your recording remains.

**Render complete audition** produces a draft with actual voice, visuals,
captions and the chosen music. Its sound checks and any simple-loop fallback
are shown. A requested soundtrack that cannot be produced is reported as an
error, rather than presenting a silent draft as music. Generated scores need
the installed music component and sample pack.

Render at least two treatments, then **Compare complete auditions**. Both
videos share a playhead; **Hear** selects the one audible soundtrack. The last
six drafts stay available while this panel remains in the studio session.
Changing controls prepares a new treatment and does not alter a cached draft.

**Use this complete audition** or **Use version A/B** saves the exact previewed
plan, track, credits and mix as one undoable edit. No fresh treatment is built
from whatever controls now show. A changed project or source file expires old
winners; render again. Previewing does not save or use the previous export's
audio stems, so timing edits can be auditioned before Update video. After
choosing, **Update video** makes the final export at your saved export settings.
The older Shorts passage and direction previews still defer added music until
Update video; Complete auditions is the place to compare the full soundtrack.
