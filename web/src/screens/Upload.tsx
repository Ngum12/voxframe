/**
 * Screen 1 — upload, in the studio's identity (D-181).
 *
 * One drop zone, the promises that are true of every video (on this
 * computer, online search only if turned on, captions in English and French
 * as D-070 and D-169 scope them, captions you can correct), and the recent
 * videos, which open in the studio.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  listJobs,
  thumbnailUrl,
  uploadAudio,
  type Capabilities,
  type Job,
  type UploadResult,
} from "../api";
import { Notice, formatBytes, formatDuration } from "../components";

const ACCEPTED = ".wav,.mp3,.m4a,.aac,.flac,.ogg,.opus,.mp4,.mov,.mkv";

function RecentVideos({ onOpen }: { onOpen: (job: Job) => void }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    listJobs()
      .then(({ jobs: all }) => {
        if (!cancelled) {
          setJobs(all.filter((job) => job.state === "succeeded" && job.artifacts.includes("video")).slice(0, 6));
        }
      })
      .catch(() => !cancelled && setJobs([]));
    return () => {
      cancelled = true;
    };
  }, []);

  if (!jobs || jobs.length === 0) return null;
  return (
    <section className="recent" aria-labelledby="recent-title">
      <h2 id="recent-title">Recent videos</h2>
      <ul>
        {jobs.map((job) => (
          <li key={job.id}>
            <button type="button" className="recent-video" onClick={() => onOpen(job)}>
              <span className="recent-thumb" aria-hidden="true">
                <img
                  src={thumbnailUrl(job.id, 1)}
                  alt=""
                  loading="lazy"
                  onError={(event) => {
                    event.currentTarget.style.visibility = "hidden";
                  }}
                />
              </span>
              <span className="recent-text">
                <strong>{job.audio_name}</strong>
                <small>
                  {job.summary?.scenes ?? 0} scenes
                  {" · "}
                  {new Date(job.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short" })}
                </small>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function Upload({
  capabilities,
  sourcingEnabled,
  onUploaded,
  onOpen,
}: {
  capabilities: Capabilities | null;
  /** Whether renders will search online, which changes what the notice says. */
  sourcingEnabled: boolean;
  onUploaded: (result: UploadResult, file: File) => void;
  /** Open a finished video in the studio. */
  onOpen: (job: Job) => void;
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
    <div className="welcome">
      <div className="welcome-main">
      <header className="hero">
        <h1>
          Turn a recording into a <em>video</em>
        </h1>
        <p className="lead">
          Voxframe listens to what is said, finds pictures for it and writes the captions, on
          this computer. Then you finish it in the studio. Your audio is not uploaded anywhere.
        </p>
      </header>

      {ffmpegMissing && (
        <Notice tone="error">
          <strong>FFmpeg was not found.</strong> Voxframe cannot render without
          it. {capabilities?.ffmpeg.error}
        </Notice>
      )}

      {error && <Notice tone="error">{error}</Notice>}

      <div className="card welcome-drop">
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
              <span className="drop-icon" aria-hidden="true">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M12 15V3M7 8l5-5 5 5M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4" />
                </svg>
              </span>
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

      <ul className="promises" aria-label="What is true of every video">
        <li>Runs on your computer</li>
        <li>Online image search only if you turn it on</li>
        <li>Captions in English and French</li>
        <li>Captions you can correct</li>
      </ul>

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
      </div>

      <RecentVideos onOpen={onOpen} />
    </div>
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
