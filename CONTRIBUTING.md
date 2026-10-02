# Contributing to Voxframe

Thank you for helping. Voxframe turns a recording into a captioned video on the
person's own computer. Every contribution should keep it **private**, **free**
and **plain to use**.

## Where to start

- **Issues labelled [`good first issue`](https://github.com/Ngum12/voxframe/labels/good%20first%20issue)**
  are small and self-contained, and say which files to look at.
- **[ROADMAP.md](ROADMAP.md)** says what is planned. If you want to work on
  something there, or anything larger than a small fix, open an issue first.
  That way we can agree on the approach before you spend time on it.
- **Bugs:** please include your system, the Voxframe version (the installer's
  file name, or `voxframe version`), what you did, and what happened. Leave out your recording and your
  API keys.
- **Security problems:** report them privately, as [SECURITY.md](SECURITY.md)
  describes, never in a public issue.

## Setting up

```bash
git clone https://github.com/Ngum12/voxframe
cd voxframe
pip install -e ".[dev,app,music]"
python -m playwright install chromium
voxframe doctor        # what is installed, and what is missing
voxframe web           # the app in your browser
```

You need Python 3.11 to 3.13, and FFmpeg with libass. The web app is in
`web/` (React and TypeScript). Its built files are committed, so you only
need Node to change it:

```bash
cd web && npm install && npm run build
```

Commit the rebuilt files in `src/voxframe/api/static/` together with your
change. A test checks that they match the source.

## Tests

```bash
pytest                 # everything, browser tests included
pytest tests/unit      # the quick ones
```

Every change comes with tests. **The full suite must pass, browser tests
included**, before a pull request is merged. Tests that need something
missing (models, a browser, large samples) skip with a reason; they never
fail for that.

## What every change keeps true

These come from [DECISIONS.md](DECISIONS.md), which records every significant
choice and why it was made. Read the entries near what you're changing.

- **Nothing leaves the computer without the person's say.**
  - Online image search runs only with their consent.
  - Recordings are never uploaded.
  - API keys never go into the repository, a video, a scene plan or a log.

  [docs/privacy.md](docs/privacy.md) lists every time Voxframe goes online. A
  change that adds one updates it.
- **Licences are permissive.**
  - A new dependency must allow free commercial use, and is added to
    [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES). A test walks every
    dependency and fails otherwise.
  - Images and music are always credited with their author and licence.
  - Ask in an issue before adding any dependency.
- **The frame grid is exact.** Captions, pictures and sound line up to the
  frame, and tests hold that. Time conversions go through `timeline/`. FFmpeg
  is run only through `render/ffpath/`, which handles paths with apostrophes
  and long command lines.
- **Plain words in the app.** Say what happened and what the person can do,
  without jargon. If something fails, say so, never silently.
- **Accessible.** Everything works from the keyboard. Every colour pair meets
  WCAG AA in both the dark and light looks; `tests/unit/test_contrast.py`
  checks them.

## Pull requests

- Keep each one to a single change, with a title that says what it does.
- Describe what changed and how you tested it. For anything visible, include
  a screenshot.
- Keep each file's line endings as they are.
- By contributing, you agree that your work is licensed under the project's
  [Apache License 2.0](LICENSE).

## Languages

Voxframe is tested in English and French. Adding a language is mostly a
matter of evidence: a reference transcript, a measured transcription error
rate, and how well pictures match in that language. If you'd like to help
with one, please open an issue first.
