"""Transcription with word-level timestamps, via faster-whisper.

Produces a :class:`~voxframe.models.transcript.Transcript` whose words carry
their own start and end times. Everything downstream — caption highlighting,
scene boundaries, transition placement — derives from that timing, so it is
requested from the model rather than interpolated.

Results are cached on a hash of the audio plus the transcription settings, so
re-rendering never re-transcribes. Transcription dominates the cost of a short
render, which makes this the difference between iterating on a style in seconds
and waiting minutes each time.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import structlog

from voxframe.models.transcript import Transcript, Word

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

__all__ = [
    "AVAILABLE_MODELS",
    "DEFAULT_MODEL",
    "Transcriber",
    "TranscriptionError",
    "audio_sha256",
]

log = structlog.get_logger(__name__)

#: Whisper sizes, smallest first. All MIT-licensed (Systran conversions).
#:
#: ``large-v3-turbo`` is a distilled ``large-v3`` with far fewer decoder layers:
#: roughly half the download and several times faster, at close to large-v3
#: accuracy. It sits last because it is a variant rather than a step up in size.
AVAILABLE_MODELS = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
    "large-v3-turbo",
)

#: `base` is the default: it runs comfortably on CPU and its word timings are
#: good enough for caption sync. Accuracy improves with size, so the CLI can
#: raise it, but the default must work on an ordinary laptop.
DEFAULT_MODEL = "base"

ComputeType = Literal["int8", "int8_float16", "float16", "float32"]

#: Below this detection probability, the language is treated as uncertain and
#: the user is told to consider ``--language`` (D-061).
#:
#: Chosen from measurement, not convention. Both LibriVox clips detect at 1.00.
#: A real accented English recording detected at 0.38 with ``base`` and was
#: misidentified as Yoruba at 0.37 by ``small``, which then dropped half the
#: audio. Clean speech in a supported language sits near 1.0, so anything below
#: 0.7 is worth a warning; the observed failures are far below it.
LANGUAGE_CONFIDENCE_THRESHOLD = 0.7

#: Number of 30-second windows sampled for language detection.
#:
#: Left at faster-whisper's default of 1 because sampling more windows was
#: measured and does not help. On a real accented recording that ``small``
#: misdetects as Yoruba, 1, 3 and 5 windows all returned ``yo`` at p=0.37 —
#: identical, with word counts varying erratically (170/127/143) and runtime up
#: 38%. Widening the window cannot fix a model that is confidently wrong about
#: the whole recording; only forcing the language does (D-061).
#:
#: Exposed anyway, since it costs nothing and a caller with audio whose opening
#: genuinely is atypical may still want it.
DETECTION_SEGMENTS = 1


class TranscriptionError(RuntimeError):
    """Raised when transcription cannot be performed."""


def audio_sha256(path: Path) -> str:
    """Hash an audio file's contents, for cache keying."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _select_device(use_gpu: bool) -> tuple[str, ComputeType]:
    """Choose device and precision, preferring GPU when it is usable.

    Falls back to CPU silently and logs the reason. A missing or CPU-only
    torch is normal, not an error: the CPU path is the tested default (D-004).
    """
    if not use_gpu:
        return "cpu", "int8"

    try:
        import torch
    except ImportError:
        log.debug("gpu.unavailable", reason="torch not installed")
        return "cpu", "int8"

    if not torch.cuda.is_available():
        log.debug("gpu.unavailable", reason="no CUDA in this torch build")
        return "cpu", "int8"

    # float16 on GPU is roughly twice as fast as float32 with no meaningful
    # accuracy cost for transcription.
    return "cuda", "float16"


class Transcriber:
    """Transcribes audio to words with timings, caching the result.

    The model is loaded lazily on first use, so constructing a Transcriber is
    cheap and a cache hit never pays the load cost — which for `base` is
    several seconds on CPU.

    Args:
        model_size: One of :data:`AVAILABLE_MODELS`.
        cache_dir: Where to store transcripts. ``None`` disables caching.
        use_gpu: Use CUDA when available. Never required.
        language: Force a language code. ``None`` auto-detects.
        initial_prompt: Text priming the decoder's context. Useful for domain
            vocabulary — names, jargon, spellings — that the model would
            otherwise render phonetically.
        hotwords: Words to bias toward. Narrower than ``initial_prompt`` and
            applied throughout rather than only at the start.
        detection_segments: How many 30-second windows to sample when
            detecting the language. Raising it was measured and did not improve
            a misdetection; prefer ``language`` when detection is wrong
            (D-061).
        allowed_languages: Restrict detection to these codes, choosing the most
            probable among them. Narrower than forcing one language, and it
            keeps multilingual work possible (D-068). Ignored when ``language``
            is set, since that already decides the answer.
        chunk_long_audio: Split long recordings at silence and transcribe each
            piece. Transcription degrades sharply with file length regardless
            of model size (D-104), and chunking bounds that. Off restores the
            single-call behaviour.
    """

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL,
        *,
        cache_dir: Path | None = None,
        use_gpu: bool = True,
        language: str | None = None,
        initial_prompt: str | None = None,
        hotwords: str | None = None,
        detection_segments: int = DETECTION_SEGMENTS,
        allowed_languages: Sequence[str] = (),
        chunk_long_audio: bool = True,
    ) -> None:
        if model_size not in AVAILABLE_MODELS:
            raise TranscriptionError(
                f"Unknown model size {model_size!r}. "
                f"Available: {', '.join(AVAILABLE_MODELS)}"
            )

        self.model_size = model_size
        self.cache_dir = cache_dir
        self.use_gpu = use_gpu
        self.language = language
        self.initial_prompt = initial_prompt
        self.hotwords = hotwords
        self.detection_segments = max(1, detection_segments)
        self.allowed_languages = tuple(
            code.strip().lower() for code in allowed_languages if code.strip()
        )
        self.chunk_long_audio = chunk_long_audio
        self._model: WhisperModel | None = None
        self._device, self._compute_type = _select_device(use_gpu)

    @property
    def model_id(self) -> str:
        """Identifier recorded in the transcript and used in the cache key."""
        return f"faster-whisper/{self.model_size}/{self._compute_type}"

    def _load_model(self) -> WhisperModel:
        """Load the model, downloading weights on first use."""
        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionError(
                "faster-whisper is not installed.\n"
                "  pip install 'voxframe[transcribe]'\n"
                "or\n"
                "  pip install faster-whisper"
            ) from exc

        log.info(
            "transcribe.model.loading",
            model=self.model_size,
            device=self._device,
            compute_type=self._compute_type,
        )

        try:
            self._model = WhisperModel(
                self.model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
        except Exception as exc:
            if self._device == "cuda":
                # 4 GB cards are easy to exhaust; CPU always works.
                log.warning("transcribe.gpu.failed", error=str(exc))
                self._device, self._compute_type = "cpu", "int8"
                self._model = WhisperModel(
                    self.model_size, device="cpu", compute_type="int8"
                )
            else:
                raise TranscriptionError(
                    f"Could not load Whisper model {self.model_size!r}: {exc}\n"
                    "The first run downloads weights and needs internet access."
                ) from exc

        return self._model

    def _cache_path(self, audio_hash: str) -> Path | None:
        """Where this audio's transcript is cached, if caching is enabled.

        The key includes the model and language, so changing either produces a
        different entry rather than silently reusing an inapplicable result.
        """
        if self.cache_dir is None:
            return None

        key = (
            f"{audio_hash}/{self.model_id}/{self.language or 'auto'}"
            f"/{self.initial_prompt or ''}/{self.hotwords or ''}"
            f"/{self.detection_segments}"
            # The allowed set changes which language is chosen, so a restricted
            # run must not return an unrestricted cached result.
            f"/{','.join(self.allowed_languages) or 'any'}"
            # Chunked and single-call runs produce different word timings at
            # the boundaries, so they are different cache entries.
            f"/{'chunked' if self.chunk_long_audio else 'single'}"
        )
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self.cache_dir / "transcripts" / f"{digest}.json"

    def _read_cache(self, path: Path | None) -> Transcript | None:
        if path is None or not path.is_file():
            return None
        try:
            return Transcript.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            # A corrupt or outdated cache entry must never break a render.
            log.warning("transcribe.cache.unreadable", path=str(path), error=str(exc))
            return None

    def _write_cache(self, path: Path | None, transcript: Transcript) -> None:
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("transcribe.cache.unwritable", path=str(path), error=str(exc))

    def _detect_allowed_language(self, audio_path: Path) -> tuple[str, float]:
        """Pick the most probable language from the allowed set.

        Runs detection once and takes the best *allowed* candidate rather than
        the best overall. This is narrower than forcing a single language and
        keeps multilingual work possible, which matters for a tool that handles
        French as well as English (D-068).

        Returns:
            ``(code, probability)``. The probability is the model's own for
            that language, not renormalised over the allowed set: reporting a
            renormalised 0.99 when the model actually gave 0.37 would hide
            exactly the uncertainty the warning exists to surface.
        """
        from faster_whisper.audio import decode_audio

        model = self._load_model()

        # detect_language wants decoded samples, not a path: passing a path
        # fails inside the VAD with an unhelpful AttributeError on `.shape`.
        samples = decode_audio(str(audio_path), sampling_rate=16000)

        _, _, all_probabilities = model.detect_language(
            audio=samples,
            vad_filter=True,
            language_detection_segments=self.detection_segments,
        )

        ranked = dict(all_probabilities)

        allowed = [
            (code, ranked.get(code, 0.0))
            for code in self.allowed_languages
            if code in ranked
        ]

        if not allowed:
            # The model knows none of the requested codes. Better to say so
            # than to fall back silently to a language the user excluded.
            raise TranscriptionError(
                f"None of the requested languages {list(self.allowed_languages)} "
                f"are supported by {self.model_size}. "
                f"Supported codes include: {', '.join(sorted(ranked)[:12])}..."
            )

        allowed.sort(key=lambda pair: pair[1], reverse=True)
        chosen, probability = allowed[0]

        detected, detected_probability = max(ranked.items(), key=lambda p: p[1])

        log.info(
            "transcribe.language.restricted",
            chosen=chosen,
            probability=round(probability, 3),
            unrestricted=detected,
            unrestricted_probability=round(detected_probability, 3),
            allowed=list(self.allowed_languages),
        )

        if detected not in self.allowed_languages:
            # Worth saying out loud: the restriction changed the answer, which
            # is the whole point, but a user should know it happened.
            log.warning(
                "transcribe.language.overridden",
                would_have_been=detected,
                would_have_been_probability=round(detected_probability, 3),
                chosen=chosen,
                probability=round(probability, 3),
            )

        return chosen, probability

    def _transcribe_chunked(
        self, audio_path: Path, audio_hash: str
    ) -> Transcript | None:
        """Transcribe a long file in chunks, or ``None`` if it is short.

        Returns ``None`` rather than raising when the file is below the
        threshold or when chunking cannot be planned, so the caller falls
        through to the ordinary single-call path.
        """
        from voxframe.render.encode.probe import FFmpegNotFound, probe_media
        from voxframe.render.ffpath import run_ffmpeg
        from voxframe.transcribe.chunked import (
            MIN_SECONDS_FOR_CHUNKING,
            find_split_points,
            merge_transcripts,
            plan_chunks,
        )

        try:
            from voxframe.render.encode.probe import probe_capabilities

            caps = probe_capabilities()
            duration = probe_media(audio_path, caps).duration
        except (FFmpegNotFound, OSError) as exc:
            log.debug("transcribe.chunk.probe_failed", error=str(exc))
            return None

        if duration < MIN_SECONDS_FOR_CHUNKING:
            return None

        chunks = plan_chunks(duration, find_split_points(audio_path))
        if len(chunks) < 2:
            return None

        log.info(
            "transcribe.chunked.start",
            audio=audio_path.name,
            duration=round(duration, 1),
            chunks=len(chunks),
        )

        import tempfile

        pieces: list[tuple[float, Transcript]] = []
        language = self.language or ""
        probability = 1.0

        with tempfile.TemporaryDirectory(prefix="voxframe_chunks_") as scratch:
            scratch_dir = Path(scratch)

            for index, (start, end) in enumerate(chunks):
                piece_path = scratch_dir / f"chunk_{index:04d}.wav"

                run_ffmpeg(
                    caps.ffmpeg_path,
                    [
                        "-loglevel", "error",
                        "-ss", f"{start:.3f}",
                        "-to", f"{end:.3f}",
                        "-i", str(audio_path.resolve()),
                        "-ar", "16000", "-ac", "1",
                        "-y", str(piece_path.resolve()),
                    ],
                )

                # Each chunk transcribes in one call, on a file short enough
                # to stay in the fast regime (D-104). Language is fixed after
                # the first chunk: re-detecting per chunk risks a quiet
                # passage being called a different language mid-recording.
                piece_transcriber = Transcriber(
                    self.model_size,
                    cache_dir=None,
                    use_gpu=self.use_gpu,
                    language=language or self.language,
                    initial_prompt=self.initial_prompt,
                    hotwords=self.hotwords,
                    allowed_languages=(
                        self.allowed_languages if not language else ()
                    ),
                    chunk_long_audio=False,
                )
                piece_transcriber._model = self._model

                piece = piece_transcriber.transcribe(piece_path)
                self._model = piece_transcriber._model

                if not language:
                    language = piece.language
                    probability = piece.language_probability

                pieces.append((start, piece))

                log.info(
                    "transcribe.chunk.done",
                    chunk=index + 1,
                    of=len(chunks),
                    words=piece.word_count,
                )

        return merge_transcripts(
            pieces,
            language=language or "en",
            language_probability=probability,
            duration=duration,
            model_id=self.model_id,
            audio_sha256=audio_hash,
        )

    def transcribe(self, audio_path: Path, *, force: bool = False) -> Transcript:
        """Transcribe an audio file.

        Args:
            audio_path: Audio to transcribe. Any format FFmpeg can decode.
            force: Ignore any cached result and re-transcribe.

        Returns:
            The transcript, with word-level timings.

        Raises:
            TranscriptionError: If the file is missing or unreadable, or the
                model cannot be loaded.
        """
        if not audio_path.is_file():
            raise TranscriptionError(f"Audio file not found: {audio_path}")

        audio_hash = audio_sha256(audio_path)
        cache_path = self._cache_path(audio_hash)

        if not force:
            cached = self._read_cache(cache_path)
            if cached is not None:
                log.info(
                    "transcribe.cache.hit",
                    audio=audio_path.name,
                    words=cached.word_count,
                    language=cached.language,
                )
                return cached

        if self.chunk_long_audio:
            chunked = self._transcribe_chunked(audio_path, audio_hash)
            if chunked is not None:
                self._write_cache(cache_path, chunked)
                return chunked

        model = self._load_model()

        # A candidate set decides the language before transcription, so the
        # decoder runs with it fixed rather than re-deciding internally.
        language = self.language
        restricted_probability: float | None = None
        if language is None and self.allowed_languages:
            language, restricted_probability = self._detect_allowed_language(
                audio_path
            )

        log.info("transcribe.start", audio=audio_path.name, model=self.model_size)

        options: dict[str, Any] = {
            "language": language,
            "word_timestamps": True,
            # VAD trims silence, which both speeds up transcription and stops
            # the model hallucinating text during long pauses.
            "vad_filter": True,
            "vad_parameters": {"min_silence_duration_ms": 500},
        }

        if language is None:
            # Passed through for callers who raise it; the default of 1 matches
            # faster-whisper because more windows was measured not to help
            # (D-061).
            options["language_detection_segments"] = self.detection_segments

        if self.initial_prompt:
            options["initial_prompt"] = self.initial_prompt
        if self.hotwords:
            options["hotwords"] = self.hotwords

        segments, info = model.transcribe(str(audio_path), **options)

        words: list[Word] = []
        for segment in segments:  # generator: iterating performs the work
            for word in segment.words or ():
                text = word.word
                if not text.strip():
                    continue
                words.append(
                    Word(
                        text=text,
                        start=float(word.start),
                        end=float(word.end),
                        probability=float(getattr(word, "probability", 1.0)),
                    )
                )

        if not words:
            raise TranscriptionError(
                f"No speech found in {audio_path.name}. "
                "The file may be silent, music-only, or too quiet."
            )

        # With a candidate set, the decoder was handed a fixed language and
        # reports 1.0. Keeping the real detection probability instead means the
        # low-confidence warning below still fires — restricting the candidates
        # narrows the choice, it does not make the model certain (D-068).
        probability = (
            restricted_probability
            if restricted_probability is not None
            else float(info.language_probability)
        )

        transcript = Transcript(
            words=tuple(words),
            language=info.language,
            language_probability=probability,
            # Prefer the words' own extent: with VAD enabled, info.duration can
            # describe the trimmed audio rather than the original file.
            duration=max(float(info.duration), words[-1].end),
            model_id=self.model_id,
            audio_sha256=audio_hash,
        )

        log.info(
            "transcribe.done",
            words=transcript.word_count,
            language=transcript.language,
            probability=round(transcript.language_probability, 3),
            duration=round(transcript.duration, 2),
        )

        # A low detection probability is not merely uncertainty: a wrong
        # language makes the decoder produce truncated or garbled output while
        # reporting success (D-061).
        if (
            self.language is None
            and transcript.language_probability < LANGUAGE_CONFIDENCE_THRESHOLD
        ):
            advice = (
                f"Re-run with --language {transcript.language} to skip "
                f"detection."
                if self.allowed_languages
                else "Re-run with --language en (or your language code) to "
                "skip detection."
            )
            log.warning(
                "transcribe.language.uncertain",
                detected=transcript.language,
                probability=round(transcript.language_probability, 2),
                threshold=LANGUAGE_CONFIDENCE_THRESHOLD,
                allowed=list(self.allowed_languages) or None,
                advice=(
                    f"Language detected as {transcript.language!r} with only "
                    f"{transcript.language_probability:.0%} confidence. If that "
                    f"is wrong, the transcript may be truncated or garbled. "
                    f"{advice}"
                ),
            )

        self._write_cache(cache_path, transcript)
        return transcript

    def to_dict(self) -> dict[str, Any]:
        """Configuration summary, for logs and phase reports."""
        return {
            "model": self.model_size,
            "device": self._device,
            "compute_type": self._compute_type,
            "language": self.language or "auto",
            "caching": self.cache_dir is not None,
        }
