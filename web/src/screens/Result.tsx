/**
 * Screen 4 — the finished video.
 *
 * Shows the video, the facts about it, the warnings, and every file the render
 * produced. Credits appear on the page rather than only in a file, because
 * per-asset attribution is a licence obligation for most sourced imagery
 * (D-071..D-082) and a file nobody opens does not discharge it.
 */

import { artifactUrl, openFolder, type Job } from "../api";
import { Notice } from "../components";

const ARTIFACT_LABELS: Record<string, string> = {
  video: "Video (MP4)",
  plan: "Scene plan (JSON)",
  srt: "Subtitles (SRT)",
  vtt: "Subtitles (VTT)",
};

export function Result({
  job,
  onShowPlan,
  onAgain,
}: {
  job: Job;
  onShowPlan: () => void;
  onAgain: () => void;
}) {
  const summary = job.summary ?? {};
  const credits = summary.credits ?? [];
  const hasVideo = job.artifacts.includes("video");
  const oneClick = summary.close_match_scenes ?? 0;

  return (
    <>
      <header style={{ marginBottom: 20 }}>
        <h1>Your video is ready</h1>
      </header>

      {job.warnings.map((warning, index) => (
        <Notice key={index} tone="warn">
          {warning}
        </Notice>
      ))}

      {oneClick > 0 && (
        // The fastest improvement available, stated where it will be seen
        // rather than left inside the scene plan to be discovered (D-138).
        <Notice tone="info">
          <strong>
            {oneClick === 1
              ? "1 plain scene is one click from an image."
              : `${oneClick} plain scenes are one click from an image.`}
          </strong>{" "}
          Nothing matched closely enough to use automatically, but close matches
          were found.{" "}
          <button type="button" className="link-button" onClick={onShowPlan}>
            Show me
          </button>
        </Notice>
      )}

      <div className="card">
        {hasVideo && (
          <video
            className="player"
            controls
            preload="metadata"
            src={artifactUrl(job.id, "video")}
          >
            Your browser cannot play this video. Download it below.
          </video>
        )}

        <dl className="facts">
          <div>
            <dt>Resolution</dt>
            <dd>
              {summary.width}&times;{summary.height}
            </dd>
          </div>
          <div>
            <dt>Scenes</dt>
            <dd>{summary.scenes ?? 0}</dd>
          </div>
          <div>
            <dt>With imagery</dt>
            <dd>
              {summary.matched_scenes ?? 0}
              <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>
                {" "}
                / {summary.illustratable_scenes ?? summary.scenes ?? 0}
              </span>
            </dd>
          </div>
          <div>
            <dt>Words</dt>
            <dd>{summary.words ?? 0}</dd>
          </div>
          <div>
            <dt>Render time</dt>
            <dd>{Math.round(summary.elapsed_seconds ?? 0)}s</dd>
          </div>
        </dl>

        {summary.saved_to && (
          // An installed app also puts the video in the videos folder (D-156).
          <p className="muted">
            Saved to <code>{summary.saved_to}</code>{" "}
            <button
              type="button"
              className="link-button"
              onClick={() => void openFolder("videos").catch(() => undefined)}
            >
              Open folder
            </button>
          </p>
        )}

        <div className="downloads">
          {job.artifacts.map((name) => (
            <a
              key={name}
              className={`btn ${name === "video" ? "btn-primary" : ""}`}
              href={artifactUrl(job.id, name)}
              download
            >
              {ARTIFACT_LABELS[name] ?? name}
            </a>
          ))}
        </div>
      </div>

      {credits.length > 0 && (
        <div className="card">
          <header>
            <h2>Credits</h2>
            <p>
              Most sources require attribution. These lines describe exactly what
              was used in this video, and are included in the download.
            </p>
          </header>
          <ul className="list-plain">
            {credits.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="actions">
        <button type="button" className="btn" onClick={onShowPlan}>
          See how it was made
        </button>
        <span className="spacer" />
        <button type="button" className="btn btn-primary" onClick={onAgain}>
          Make another
        </button>
      </div>
    </>
  );
}
