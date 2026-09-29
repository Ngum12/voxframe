# Private samples

Your own recordings for accent and real-world testing. **Nothing here is
committed** — the `.gitignore` in this directory excludes everything except
itself and this README.

## Usage

Drop an audio file in here:

```
samples/private/my_recording.wav
```

Tests that use private samples skip cleanly when the directory is empty, so the
suite passes for contributors who have none.

## Naming

Any audio extension is picked up (`.wav`, `.mp3`, `.m4a`, `.flac`, `.ogg`).
If a file named `accent_test.*` exists it is used as the primary accent sample
in phase reports; otherwise the first file found alphabetically is used.

## Why these stay private

Voice recordings are personal data. They are excluded from the repository so
they cannot be published accidentally, and results from them are reported
separately in phase reports rather than committed as fixtures.
