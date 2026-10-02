# Roadmap

What's planned for Voxframe, roughly in order. Plans can change; the
[releases page](https://github.com/Ngum12/voxframe/releases) is the record of
what has shipped. If you'd like to help with any of this, see
[CONTRIBUTING.md](CONTRIBUTING.md), and please open an issue before starting
on anything large.

## Next: 0.3.0

Three additions to the studio, built in this order.

### 1. Caption styles and animations

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

## Then: Shorts

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

- **Silences, "um"s and false starts removed**, cut on the word timestamps,
  so sound and picture stay together. Every cut is shown and can be undone.
- **Zoom punch-ins** on the words you stress.

### 4. Clips from a long recording

- **Suggested Shorts:** 15 to 60 seconds each, starting and ending on a
  sentence, scored by the same plain signals as highlights. You preview,
  trim and choose; nothing is posted or chosen for you.
- **Bold Shorts captions** (from 0.3.0's caption styles), a hook title over
  the first seconds, and a progress bar.
- **Export presets** for YouTube Shorts, TikTok, Reels and WhatsApp Status:
  length, loudness, and captions kept clear of each app's buttons.

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
- **Always-on checks** for pull requests on GitHub, and issue templates.

## Always

Whatever is added, Voxframe stays:

- **on your computer:** no account, no upload, no tracking;
- **free and open source**, under the Apache License 2.0;
- **plain to use:** it shows every choice it made, and lets you change any of
  them.
