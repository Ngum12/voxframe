/**
 * Screen 1 — upload.
 *
 * One drop zone, and the two facts a person needs before committing: how long
 * the recording is, and roughly how long the render will take. The estimate
 * uses Phase 6's measured factors, not a guess.
 */

import { useCallback, useRef, useState } from "react";
import { ApiError, uploadAudio, type Capabilities, type UploadResult } from "../api";
import { Notice, formatBytes, formatDuration } from "../components";

const ACCEPTED = ".wav,.mp3,.m4a,.aac,.flac,.ogg,.opus,.mp4,.mov,.mkv";

export function Upload({
  capabilities,
  sourcingEnabled,
  onUploaded,
}: {
  capabilities: Capabilities | null;
  /** Whether renders will search online, which changes what the notice says. */
  sourcingEnabled: boolean;
  onUploaded: (result: UploadResult, file: File) => void;
}) {
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [fraction, setFraction] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const send = useCallback(
    async (file: File) => {
      setError(null);
      setBusy(true);
      setFraction(0);
      try {
        const result = await uploadAudio(file, setFraction);
        onUploaded(result, file);
      } catch (caught) {
        setError(
          caught instanceof ApiError ? caught.message : "Upload failed. Try again.",
        );
      } finally {
        setBusy(false);
      }
    },
    [onUploaded],
  );

  const ffmpegMissing = capabilities && !capabilities.ffmpeg.available;

  return (
    <>
      <header style={{ marginBottom: 20 }}>
        <h1>Turn a recording into a video</h1>
        <p className="muted" style={{ marginTop: 4 }}>
          Everything runs on this machine. Your audio is not uploaded anywhere.
        </p>
      </header>

      {ffmpegMissing && (
        <Notice tone="error">
          <strong>FFmpeg was not found.</strong> Voxframe cannot render without
          it. {capabilities?.ffmpeg.error}
        </Notice>
      )}

      {error && <Notice tone="error">{error}</Notice>}

      <div className="card">
        <div
          className="dropzone"
          data-over={over}
          role="button"
          tabIndex={0}
          aria-label="Choose an audio file, or drop one here"
          aria-describedby="upload-formats"
          onClick={() => inputRef.current?.click()}
          onKeyDown={(event) => {
            // A div acting as a button must answer to Enter and Space, or the
            // whole first screen is unreachable from the keyboard.
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDragOver={(event) => {
            event.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(event) => {
            event.preventDefault();
            setOver(false);
            const file = event.dataTransfer.files?.[0];
            if (file) void send(file);
          }}
        >
          {busy ? (
            <>
              <strong>Uploading…</strong>
              <span>{Math.round(fraction * 100)}%</span>
            </>
          ) : (
            <>
              <strong>Drop an audio file here</strong>
              <span>or click to choose one</span>
            </>
          )}
        </div>

        {busy && (
          <div className="bar" aria-hidden="true" style={{ marginTop: 12 }}>
            <i style={{ width: `${fraction * 100}%` }} />
          </div>
        )}

        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED}
          className="sr-only"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void send(file);
            event.target.value = "";
          }}
        />

        <p className="muted" id="upload-formats" style={{ marginTop: 14 }}>
          WAV, MP3, M4A, FLAC, OGG, Opus, and video files with an audio track.
          Up to 2 GB.
        </p>
      </div>

      {capabilities && capabilities.library.assets === 0 && sourcingEnabled && (
        <Notice tone="info">
          Your image library is empty, so Voxframe will search online for images
          while it makes your video.
        </Notice>
      )}

      {capabilities && capabilities.library.assets === 0 && !sourcingEnabled && (
        // The full advice, now that both halves work (D-131, D-146).
        <Notice tone="warn">
          <strong>Your image library is empty,</strong> so scenes will show a
          plain background unless you add your own photos in the Library or turn
          on “Search online for images” in Settings. After the video is made, you
          can also add your own photo to any scene.
        </Notice>
      )}
    </>
  );
}

/** A short summary of the uploaded file, shown on the settings screen. */
export function UploadedFile({ result }: { result: UploadResult }) {
  return (
    <dl className="facts" style={{ marginTop: 0, marginBottom: 18 }}>
      <div>
        <dt>File</dt>
        <dd style={{ fontSize: 14, overflowWrap: "anywhere" }}>{result.name}</dd>
      </div>
      <div>
        <dt>Length</dt>
        <dd>{formatDuration(result.duration_seconds)}</dd>
      </div>
      <div>
        <dt>Size</dt>
        <dd>{formatBytes(result.bytes)}</dd>
      </div>
    </dl>
  );
}
