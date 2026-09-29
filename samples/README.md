# Sample assets

Small inputs for tests and demos. Kept minimal — this directory is committed,
so nothing large belongs here (D-019).

## Generating a test tone

No sample audio ships with the repo yet. Generate one with FFmpeg:

```bash
ffmpeg -f lavfi -i "sine=frequency=440:duration=10" -ar 16000 samples/tone.wav
```

Phase 2 adds a short spoken-word sample with a permissive license for
end-to-end transcription tests.

## Licensing

Every sample must be public domain or permissively licensed, with its source
recorded here. Samples are redistributed with the repository, so the same
license rules that apply to dependencies apply to them.

## The one committed recording

`public/en_sonnet_january_45s.wav` is committed, as the single exception to
the rule that sample media is fetched rather than committed (D-019, D-151). It
is the 45-second public-domain LibriVox excerpt recorded in `PROVENANCE.md`
(SHA-256 `cd927d85f09c3bde1422529afd66488302fea67d264a784f5453342e7fad4b2d`),
and it is here so the A/V sync test runs on every machine. A test fails if any
other media file under `samples/` is tracked.
