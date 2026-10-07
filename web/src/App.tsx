/**
 * The app shell: which screen is showing, and the state shared between them.
 *
 * No router. The flow is linear — upload, settings, progress, result — with
 * Settings as a side panel reachable from the header. A router would add a
 * dependency and a URL scheme for four screens that have no meaningful deep
 * links: a job URL would be the only candidate, and a job is already reachable
 * from its own page.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  establishSession,
  getJob,
  type ClipReviewTarget,
  getCapabilities,
  getModelStatus,
  getSettings,
  listJobs,
  rerenderJob,
  resumeJob,
  type Capabilities,
  type Job,
  type RenderOptions,
  type UploadResult,
  submitJob,
} from "./api";
import { Notice, Steps } from "./components";
import { Consent } from "./screens/Consent";
import { Filmstrip } from "./screens/Filmstrip";
import { GettingReady } from "./screens/GettingReady";
import { Library } from "./screens/Library";
import { Preferences } from "./screens/Preferences";
import { Progress } from "./screens/Progress";
import { Settings } from "./screens/Settings";
import { Studio } from "./screens/Studio";
import { Upload } from "./screens/Upload";

type Screen =
  | "upload"
  | "settings"
  | "progress"
  | "result"
  | "plan"
  | "library"
  | "preferences";

const STEPS = [
  { id: "upload", label: "Recording" },
  { id: "settings", label: "Look and music" },
  { id: "progress", label: "Making" },
  { id: "result", label: "Studio" },
];

export function App() {
  const [ready, setReady] = useState(false);
  const [authorised, setAuthorised] = useState(true);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [needsConsent, setNeedsConsent] = useState(false);
  // The models are not here yet: the Getting-ready screen comes first (D-157).
  const [needsModels, setNeedsModels] = useState(false);
  const [sourcingEnabled, setSourcingEnabled] = useState(false);

  const [screen, setScreen] = useState<Screen>("upload");
  const [upload, setUpload] = useState<UploadResult | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const reviewRequest = useRef(0);
  const [reviewTarget, setReviewTarget] = useState<ClipReviewTarget | null>(null);
  const [reviewParent, setReviewParent] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const open = async (event: Event) => {
      const target = (event as CustomEvent<ClipReviewTarget>).detail;
      if (!target || !/^[a-f0-9]{32}$/.test(target.jobId) || !/^[a-f0-9]{32}$/.test(target.parent) ||
        !["captions", "director", "sound", "scenes", "export", "shorts"].includes(target.action) ||
        (target.scene !== null && (!Number.isSafeInteger(target.scene) || target.scene < 0))) return;
      const request = ++reviewRequest.current;
      try {
        const next = await getJob(target.jobId);
        if (!live || request !== reviewRequest.current) return;
        setReviewTarget(target); setReviewParent(target.parent);
        setJobId(next.id); setJob(next); setScreen("result");
      } catch (e) { if (live) setError(e instanceof Error ? e.message : "Could not open the clip."); }
    };
    document.addEventListener("voxframe:review-clip", open);
    return () => { live = false; document.removeEventListener("voxframe:review-clip", open); };
  }, []);
  const [error, setError] = useState<string | null>(null);

  // Remounts the progress screen after a resume or re-render, so it follows
  // the job's new run from the start rather than its finished old one.
  const [progressRun, setProgressRun] = useState(0);
  const [rerendering, setRerendering] = useState(false);

  // A render that was interrupted before this page was opened -- Voxframe was
  // closed, or its worker died. Offered on the first screen so it is not
  // silently lost (D-129).
  const [interrupted, setInterrupted] = useState<Job | null>(null);

  // Stable, so the progress screen's subscription is not torn down and
  // rebuilt every time this component re-renders.
  const onDone = useCallback((finished: Job) => {
    setJob(finished);
    setScreen("result");
  }, []);
  const onCancelled = useCallback(() => {
    setJobId(null);
    setRerendering(false);
    setScreen("upload");
  }, []);

  const refreshSettings = useCallback(async () => {
    const settings = await getSettings();
    setSourcingEnabled(settings.sourcing.enabled);
    setNeedsConsent(!settings.sourcing.has_been_asked);
  }, []);

  // Only one audible player owns playback. Muted visual sketches may keep looping.
  useEffect(() => {
    const exclusive = (event: Event) => {
      const active = event.target;
      if (!(active instanceof HTMLMediaElement) || active.paused || active.muted || active.volume === 0) return;
      document.querySelectorAll<HTMLMediaElement>("audio, video").forEach((other) => {
        if (other !== active && !other.muted && other.volume > 0) other.pause();
      });
    };
    document.addEventListener("play", exclusive, true);
    document.addEventListener("volumechange", exclusive, true);
    return () => {
      document.removeEventListener("play", exclusive, true);
      document.removeEventListener("volumechange", exclusive, true);
    };
  }, []);

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  }, [screen]);

  useEffect(() => {
    void (async () => {
      const ok = await establishSession();
      setAuthorised(ok);
      if (ok) {
        try {
          setCapabilities(await getCapabilities());
          await refreshSettings();
          try {
            setNeedsModels(!(await getModelStatus()).ready);
          } catch {
            // Not knowing is not a reason to block the app; a render will say.
          }
          const { jobs } = await listJobs();
          const latest = jobs[0];
          if (latest && latest.state === "interrupted") setInterrupted(latest);
        } catch {
          setAuthorised(false);
        }
      }
      setReady(true);
    })();
  }, [refreshSettings]);

  const start = async (options: Omit<RenderOptions, "upload_id">) => {
    if (!upload) return;
    setError(null);
    try {
      const created = await submitJob({ ...options, upload_id: upload.upload_id });
      setJobId(created.id);
      setScreen("progress");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start the render.");
    }
  };

  if (!ready) {
    return (
      <main>
        <p className="muted">Starting…</p>
      </main>
    );
  }

  if (!authorised) {
    return (
      <main>
        <div className="center-narrow">
          <Notice tone="error">
            <strong>This page is not authorised.</strong> Open the link printed
            by <code>voxframe web</code> in your terminal — it carries a session
            token that is generated fresh each time the server starts.
          </Notice>
        </div>
      </main>
    );
  }

  if (needsModels) {
    return <GettingReady onDone={() => setNeedsModels(false)} />;
  }

  if (needsConsent) {
    return (
      <Consent
        onDecided={(enabled) => {
          setSourcingEnabled(enabled);
          setNeedsConsent(false);
        }}
      />
    );
  }

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>

      <header className="masthead">
        <span className="wordmark">
          <svg className="brand-symbol" viewBox="0 0 40 40" fill="none" aria-hidden="true">
            <path d="M13 7H7v6m20-6h6v6M7 27v6h6m20-6v6h-6" />
            <path className="brand-wave" d="M12 18v4m4-8v12m4-16v20m4-16v12m4-8v4" />
          </svg>
          <span className="brand-name">Vox<span>Frame</span></span>
          <small>LOCAL STUDIO</small>
        </span>
        <nav aria-label="Sections">
          <button
            type="button"
            aria-current={
              screen !== "preferences" && screen !== "library" ? "page" : undefined
            }
            onClick={() => setScreen(upload ? "settings" : "upload")}
          >
            Make a video
          </button>
          <button
            type="button"
            aria-current={screen === "library" ? "page" : undefined}
            onClick={() => setScreen("library")}
          >
            Library
          </button>
          <button
            type="button"
            aria-current={screen === "preferences" ? "page" : undefined}
            onClick={() => setScreen("preferences")}
          >
            Settings
          </button>
        </nav>
      </header>

      <main id="main" tabIndex={-1} className={screen === "result" ? "main-studio" : undefined}>
        {screen !== "preferences" && screen !== "plan" && screen !== "library" && screen !== "result" && (
          <Steps steps={STEPS} current={screen} />
        )}
        {error && <Notice tone="error">{error}</Notice>}

        {screen === "upload" && interrupted && (
          <Notice tone="warn">
            <strong>A render was interrupted.</strong> “{interrupted.audio_name}”
            stopped before it finished. Scenes already rendered are kept.{" "}
            <button
              type="button"
              className="btn"
              style={{ marginLeft: 8 }}
              onClick={async () => {
                try {
                  await resumeJob(interrupted.id);
                  setJobId(interrupted.id);
                  setRerendering(false);
                  setInterrupted(null);
                  setProgressRun((run) => run + 1);
                  setScreen("progress");
                } catch (caught) {
                  setError(caught instanceof Error ? caught.message : "Could not resume.");
                }
              }}
            >
              Resume it
            </button>
          </Notice>
        )}

        {screen === "upload" && (
          <Upload
            capabilities={capabilities}
            sourcingEnabled={sourcingEnabled}
            onUploaded={(result) => {
              setUpload(result);
              setScreen("settings");
            }}
            onOpen={(opened) => {
              reviewRequest.current++; setReviewTarget(null); setReviewParent(null);
              setJobId(opened.id);
              setJob(opened);
              setScreen("result");
            }}
          />
        )}

        {screen === "settings" && upload && (
          <Settings
            upload={upload}
            capabilities={capabilities}
            sourcingEnabled={sourcingEnabled}
            onBack={() => setScreen("upload")}
            onStart={start}
          />
        )}

        {screen === "progress" && jobId && (
          <Progress
            key={progressRun}
            jobId={jobId}
            rerender={rerendering}
            onDone={onDone}
            onCancelled={onCancelled}
            onResume={async () => {
              await resumeJob(jobId);
              setProgressRun((run) => run + 1);
            }}
          />
        )}

        {screen === "result" && job && jobId && (
          // The studio (D-180): the video is edited in place, and updating it
          // stays here rather than moving to the progress screen.
          <Studio key={jobId}
            onBackCollection={reviewParent ? () => {
              if (!reviewParent) return;
              const request = ++reviewRequest.current;
              void getJob(reviewParent).then(next => {
                if (request !== reviewRequest.current) return;
                setReviewTarget({jobId: next.id, parent: next.id, scene: null, action: "director"});
                setReviewParent(null); setJobId(next.id); setJob(next);
              }).catch(e => { if (request === reviewRequest.current) setError(e instanceof Error ? e.message : "Could not return to the collection."); });
            } : undefined}
            initialReview={reviewTarget?.jobId === jobId ? reviewTarget : null}
            openCollection={reviewTarget?.jobId === jobId && !reviewParent}
            jobId={jobId}
            initialJob={job}
            sourcingEnabled={sourcingEnabled}
            onShowPlan={() => setScreen("plan")}
            onAgain={() => {
              reviewRequest.current++; setReviewTarget(null); setReviewParent(null);
              setUpload(null);
              setJob(null);
              setJobId(null);
              setScreen("upload");
            }}
          />
        )}

        {screen === "plan" && jobId && (
          <>
            <button
              type="button"
              className="btn btn-quiet"
              style={{ padding: 0, marginBottom: 14 }}
              onClick={() => setScreen("result")}
            >
              &larr; Back to the studio
            </button>
            <Filmstrip
              jobId={jobId}
              pendingEdits={job?.summary.pending_edits ?? 0}
              sourcingEnabled={sourcingEnabled}
              onRerender={async () => {
                await rerenderJob(jobId);
                setRerendering(true);
                setProgressRun((run) => run + 1);
                setScreen("progress");
              }}
            />
          </>
        )}

        {screen === "library" && (
          <Library onChanged={() => void getCapabilities().then(setCapabilities)} />
        )}

        {screen === "preferences" && (
          <Preferences
            onChanged={() => {
              void refreshSettings();
              void getCapabilities().then(setCapabilities);
            }}
          />
        )}
      </main>
    </div>
  );
}
