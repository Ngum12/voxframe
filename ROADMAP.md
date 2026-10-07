# Roadmap

What's planned for Voxframe, roughly in order. Plans can change; the
[releases page](https://github.com/Ngum12/voxframe/releases) is the record of
what has shipped. If you'd like to help with any of this, see
[CONTRIBUTING.md](CONTRIBUTING.md), and please open an issue before starting
on anything large.

## Next: 0.3.0

Three additions to the studio, built in this order.

### 1. Caption styles and animations

- **Done** for Shorts and every other video (D-196): seven styles, caption
  transitions, emphasised words, placement and size, seen live in the
  studio.
- **Still to come:** each template's own default style, and a **suggest**
  button that marks the words you stressed most for emphasis; nothing
  changes until you click.

### 2. Transitions

- **Kinds:** cut, crossfade, dip to black, slide, push, zoom and soft blur.
- **Presets:** each template has its own transition, and any join between
  two scenes can be changed in the studio.
- **Frame-exact:** a transition never shifts the sound or the captions.
- **Quick to change:** changing a transition remakes only that join, not
  the scenes.

### 3. A music library

- **Your tracks, kept.** Every track you add is saved for reuse, with its
  credit, and any video can use it again.
- **Search online for openly licensed music** (Openverse), with previews and
  filters for length, mood and instrumental. You can hear a track under your
  own voice before choosing it. It follows the same rules as image search:
  - **only if you've turned online search on**, and only the words you type
    are sent;
  - **the same licence rules:** nothing NonCommercial or NoDerivatives, and
    ShareAlike off unless you turn it on;
  - **every track is credited automatically:** title, author, licence and
    source;
  - **never chosen for you:** online music is only ever added by your click;
  - **a clear note** that some openly licensed music is also registered with
    YouTube's Content ID, so a video using it may still get a claim.

## Now: Shorts that hook

Short vertical videos with real editing, made from a recording **or a
video**, that hook a viewer in the first seconds and keep them. Every choice
stays yours, and can be changed after the video is made. Built in this order,
each step usable on its own:

### Done: your video on screen, framing that follows you

- **Your own footage**, in sync to the frame, with matched pictures cut in
  as cutaways (D-192); **framing that follows your face** in a vertical crop
  (D-193); **captions that fit** the frame and stay clear of your face
  (D-193, D-194).

### 1. Caption styles, seen live (D-196) - done

- **Seven styles:** highlight, pop, bounce, spotlight, karaoke, typewriter
  and plain, every word moving exactly when it is said.
- **Transitions** for each caption: cut, fade, pop in, slide up, zoom in.
- **Emphasised words**, larger and in colour; **size**, **all caps**, and
  captions **dragged** to any height in the player.
- **Seen live in the studio,** drawn by the same engine that burns them into
  the video, before the video is made again.

### 2. Split screen and picture-in-picture (D-197) - done

- **A screen divider:** the picture or video clip of what you are talking
  about on top, you below, explaining it; the divide where you choose.
- **Picture-in-picture:** you in a rounded or circular frame over the
  picture, or the picture over you; any size, anywhere.
- **Per scene,** like the shot: full picture, full you, split or inset.
- **Your own clips** as a scene's picture, playing above you in a split.

### 3. Pop-ups on cue (D-198) - done

- **Text callouts, emoji and stickers, your own images, arrows and circles,
  number counters and a progress bar,** each tied to a word, so it appears
  when that word is said and moves with it if the timing changes.
- **Entrances and exits** (pop, slide, bounce, fade), placed by dragging in
  the player, and suggestions you accept with a click.

### 4. The hook and the pace (D-199) - done

- **A hook finder** that ranks your strongest opening lines by plain signals
  (a question, a number, "you", a short sentence, the energy in your voice),
  and a **cold open** that starts on the one you pick.
- **A hook title** over the first seconds.
- **Jump cuts:** silences, "um"s and false starts removed on the word
  timestamps, every cut shown and undoable; **zoom punch-ins** on stressed
  words.
- **A retention check** that marks long stretches where nothing changes.
- All in the studio's **Hook & pace** tab; the player and timeline follow
  the cut video, and the timeline marks every jump.

### Waiting: clips from a long recording

- Suggested 15 to 60 second Shorts from a long recording, and export presets
  for YouTube Shorts, TikTok and Reels. Held until the Shorts themselves are
  as good as they can be.

## Later

- **Music composed for your video.** A score written for each recording,
  following the speaker: it builds through sentences and swells into pauses,
  in styles such as inspiring, calm, reflective and cinematic. The engine
  exists; it is waiting on a download of its instrument sounds (openly
  licensed, CC0) for the installed app. After that come more styles and
  instruments.
- **Camera movement you can direct:** the direction and strength of the slow
  pan and zoom on each scene, not just on or off.
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
