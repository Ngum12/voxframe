/**
 * Screen 3 — progress.
 *
 * The pipeline's real stages, each shown separately, because their durations
 * differ by an order of magnitude: transcription was 88% of wall time before
 * chunking (D-104). One bar would sit at the same number and then jump, which
 * reads as a hang.
 *
 * **A render that stops must say so, and say what to do.** An interrupted job
 * (Voxframe closed, or a worker died) and a failed one both end here with a
 * plain message and a Resume button, which reuses every scene already rendered
 * (D-101, D-129). The screen never waits forever: the server marks a job with
 * no live worker as interrupted, and this page polls as a backstop.
 */

import { useEffect, useRef, useState } from "react";
import { cancelJob, followJob, getJob, type Job } from "../api";
import { Notice } from "../components";

const STAGES = [
  { id: "transcribing", label: "Transcribing", note: "The longest step" },
  { id: "segmenting", label: "Finding scenes", note: "" },
  { id: "matching", label: "Choosing imagery", note: "" },
  { id: "planning", label: "Placing cards", note: "" },
  { id: "rendering", label: "Rendering", note: "" },
];

/** A job that stopped without finishing, and what the page should say. */
interface Stopped {
  state: Job["state"];
  message: string;
  resumable: boolean;
}

export function Progress({
  jobId,
  rerender = false,
  onDone,
  onCancelled,
  onResume,
}: {
  jobId: string;
  /** Rendering an edited plan: the early stages do not run. */
  rerender?: boolean;
  onDone: (job: Job) => void;
  onCancelled: () => void;
  onResume: () => Promise<void>;
}) {
  const [stage, setStage] = useState(rerender ? "rendering" : "transcribing");
  const [message, setMessage] = useState(rerender ? "Rendering your changes" : "Starting…");
  const [cancelling, setCancelling] = useState(false);
  const [stopped, setStopped] = useState<Stopped | null>(null);
  const [resuming, setResuming] = useState(false);
  const settled = useRef(false);

  useEffect(() => {
    settled.current = false;

    const finish = (job: Job) => {
      if (settled.current) return;
      if (job.state === "succeeded") {
        settled.current = true;
        onDone(job);
      } else if (job.state === "cancelled" && !job.resumable) {
        settled.current = true;
        onCancelled();
      } else if (job.state !== "running" && job.state !== "queued") {
        settled.current = true;
        setStopped({
          state: job.state,
          message: job.error || job.message,
          resumable: job.resumable,
        });
      }
    };

    const stop = followJob(jobId, {
      onProgress: (event) => {
        setStage(event.stage);
        setMessage(event.message);
      },
      onState: finish,
    });

    // A job that finished before the stream attached would otherwise leave this
    // screen waiting, so its state is also polled as a backstop.
    const poll = window.setInterval(async () => {
      try {
        finish(await getJob(jobId));
      } catch {
        /* transient; the stream is the primary path */
      }
    }, 4000);

    return () => {
      stop();
      window.clearInterval(poll);
    };
  }, [jobId, onDone, onCancelled]);

  const stages = rerender ? STAGES.filter((entry) => entry.id === "rendering") : STAGES;
  const index = stages.findIndex((entry) => entry.id === stage);

  return (
    <>
      <header style={{ marginBottom: 20 }}>
        <h1>{rerender ? "Updating your video" : "Making your video"}</h1>
        <p className="muted" style={{ marginTop: 4 }}>
          {rerender
            ? "Only the scenes you changed are rendered again; the rest are reused."
            : "You can close this tab. The render keeps going, and an interrupted one resumes from where it stopped."}
        </p>
      </header>

      {stopped && (
        <Notice tone={stopped.state === "interrupted" ? "warn" : "error"}>
          <strong>
            {stopped.state === "interrupted"
              ? "This render was interrupted."
              : stopped.state === "cancelled"
                ? "You stopped this render."
                : "This render failed."}
          </strong>{" "}
          {stopped.message}
        </Notice>
      )}

      <div className="card">
        {/* One polite live region for the whole list: announcing every stage
            row on each update would be unusable with a screen reader. */}
        <p className="sr-only" aria-live="polite">
          {stopped ? stopped.message : message}
        </p>

        <ul className="stages">
          {stages.map((entry, position) => {
            const state =
              stopped && position === index
                ? "failed"
                : position < index
                  ? "done"
                  : position === index && !stopped
                    ? "active"
                    : "todo";
            return (
              <li key={entry.id} data-state={state}>
                <span className="dot" aria-hidden="true" />
                <span>{entry.label}</span>
                {state === "active" && <span className="detail">{message}</span>}
                {state === "todo" && entry.note && !stopped && (
                  <span className="detail">{entry.note}</span>
                )}
                {state === "done" && (
                  <span className="detail" aria-hidden="true">
                    done
                  </span>
                )}
                <span className="sr-only">
                  {state === "done"
                    ? " completed"
                    : state === "active"
                      ? " in progress"
                      : state === "failed"
                        ? " stopped here"
                        : " waiting"}
                </span>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="actions">
        {!stopped && (
          <>
            <button
              type="button"
              className="btn"
              disabled={cancelling}
              onClick={async () => {
                setCancelling(true);
                try {
                  await cancelJob(jobId);
                } catch {
                  setCancelling(false);
                }
              }}
            >
              {cancelling ? "Stopping…" : "Stop"}
            </button>
            {cancelling && (
              <span className="muted">
                Finishing the current step first — a render cannot be cut mid-frame.
              </span>
            )}
          </>
        )}

        {stopped && (
          <>
            <button type="button" className="btn" onClick={onCancelled}>
              Start over with a new recording
            </button>
            <span className="spacer" />
            {stopped.resumable && (
              <button
                type="button"
                className="btn btn-primary"
                disabled={resuming}
                onClick={async () => {
                  setResuming(true);
                  try {
                    await onResume();
                  } finally {
                    setResuming(false);
                  }
                }}
              >
                {resuming
                  ? "Resuming…"
                  : stopped.state === "failed"
                    ? "Try again"
                    : "Resume"}
              </button>
            )}
          </>
        )}
      </div>
    </>
  );
}
