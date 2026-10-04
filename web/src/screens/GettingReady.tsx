/**
 * Getting ready — the models, before the first video (D-157).
 *
 * Voxframe needs two models, downloaded once. This screen says what they are,
 * what they are for and how big, downloads nothing until the person clicks
 * Download, and shows progress measured from the bytes actually received.
 * Used on first run, and as a card in Settings.
 */

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  downloadModels,
  getModelStatus,
  type ModelChoice,
  type ModelStatus,
} from "../api";
import { Choice, Notice } from "../components";

const PROFILE_TEXT: Record<ModelChoice["profile"], { title: string; description: string }> = {
  standard: {
    title: "Standard — best accuracy",
    description: "The most accurate captions and picture matching, in English and French.",
  },
  lite: {
    title: "Lite — smallest download",
    description:
      "For slow connections or small disks. English is nearly as good; French picture matching is noticeably weaker.",
  },
};

function size(mb: number): string {
  return mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

function timeLeft(seconds: number | null): string {
  if (seconds === null) return "";
  if (seconds < 60) return "less than a minute left";
  const minutes = Math.round(seconds / 60);
  return `about ${minutes} minute${minutes === 1 ? "" : "s"} left`;
}

export function ModelSetup({ onReady }: { onReady?: () => void }) {
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [profile, setProfile] = useState<ModelChoice["profile"]>("standard");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const loaded = await getModelStatus();
      setStatus(loaded);
      return loaded;
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The model status could not be read.");
      return null;
    }
  }, []);

  useEffect(() => {
    void refresh().then((loaded) => {
      if (loaded) setProfile(loaded.profile);
    });
  }, [refresh]);

  // Poll while downloading: the server measures the bytes on disk.
  const downloading = status?.download.state === "downloading";
  const updating = status?.download.state === "updating";
  const busy = downloading || updating;
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [busy, refresh]);

  if (!status) return error ? <Notice tone="error">{error}</Notice> : <p className="muted">Checking…</p>;

  const chosen = status.choices.find((choice) => choice.profile === profile) ?? status.choices[0];
  const download = status.download;
  const done = status.profile === profile && status.ready && !busy
    && download.state !== "failed";
  const percent = download.total_mb ? Math.round((download.received_mb / download.total_mb) * 100) : 0;

  const start = async () => {
    setError(null);
    try {
      await downloadModels(profile);
      await refresh();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The download could not start.");
    }
  };

  return (
    <div className="model-setup">
      {!status.profile_from_environment && !busy && (
        <fieldset className="choices">
          <legend className="sr-only">Which models</legend>
          {status.choices.map((choice) => (
            <Choice
              key={choice.profile}
              name="profile"
              value={choice.profile}
              checked={profile === choice.profile}
              onChange={(value) => setProfile(value as ModelChoice["profile"])}
              title={PROFILE_TEXT[choice.profile].title}
              description={`${PROFILE_TEXT[choice.profile].description} ${
                choice.ready
                  ? "Already on this computer."
                  : choice.to_download_mb < choice.total_mb
                    ? `${size(choice.to_download_mb)} left to download.`
                    : `About ${size(choice.total_mb)} to download.`
              }`}
            />
          ))}
        </fieldset>
      )}

      <ul className="model-list">
        {chosen.models.map((model) => (
          <li key={model.label}>
            <strong>{model.label}</strong> · {size(model.mb)}
            {model.ready ? " · on this computer" : ""}
            <span className="muted"> — {model.purpose}</span>
          </li>
        ))}
      </ul>

      {downloading && (
        <div className="download-progress" role="status" aria-live="polite">
          <progress max={download.total_mb} value={download.received_mb} aria-label="Download progress" />
          <p>
            {download.current ? `${download.current}: ` : ""}
            {size(download.received_mb)} of {size(download.total_mb)} ({percent}%)
            {download.seconds_left !== null ? ` — ${timeLeft(download.seconds_left)}` : ""}
          </p>
        </div>
      )}

      {updating && (
        <div role="status" aria-live="polite">
          <p>Updating picture matching in your library…</p>
          <progress max={download.total_assets || 1} value={download.completed_assets}
            aria-label="Library update progress" />
          <p>{download.completed_assets} of {download.total_assets} items.</p>
        </div>
      )}
      {!busy && !done && (
        <p className="muted">Using these models also updates picture matching for your library.</p>
      )}

      {download.state === "failed" && <Notice tone="error">{download.message}</Notice>}
      {error && <Notice tone="error">{error}</Notice>}

      <div className="actions">
        {done ? (
          <>
            <span className="muted" role="status">Ready. Everything now runs on this computer.</span>
            {onReady && (
              <button type="button" className="btn btn-primary" onClick={onReady}>
                Continue
              </button>
            )}
          </>
        ) : (
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => void start()}
          >
            {updating
              ? "Updating library…"
              : downloading
              ? "Downloading…"
              : download.state === "failed"
                ? "Try again"
                : chosen.ready
                  ? `Use ${profile === "standard" ? "Standard" : "Lite"}`
                  : `Download ${size(chosen.to_download_mb)}`}
          </button>
        )}
      </div>

      <details className="scene-details">
        <summary>No internet on this computer?</summary>
        <p>
          Copy the models from another computer where Voxframe has already downloaded
          them, into this folder, then open Voxframe again:
        </p>
        <p>
          <code>{status.models_folder}</code>
        </p>
      </details>
    </div>
  );
}

/** The first-run screen: shown before anything else until the models are here. */
export function GettingReady({ onDone }: { onDone: () => void }) {
  return (
    <main>
      <div className="center-narrow card">
        <header>
          <h1>Getting ready</h1>
          <p>
            Voxframe needs two models to turn speech into a video. They are downloaded
            once; after that, everything runs on this computer, without the internet.
            Nothing is downloaded until you click Download.
          </p>
        </header>
        <ModelSetup onReady={onDone} />
        <p className="muted" style={{ marginTop: 16 }}>
          <button type="button" className="link-button" onClick={onDone}>
            Not now
          </button>{" "}
          — the first video will download them instead. You can also do this later in
          Settings.
        </p>
      </div>
    </main>
  );
}
