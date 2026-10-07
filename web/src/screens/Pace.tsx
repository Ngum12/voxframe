/**
 * The hook and the pace (D-199): what makes someone stop scrolling, and what
 * keeps them watching.
 *
 * - The hook: open on the strongest line from later in the video (a cold
 *   open), with a title over its first seconds.
 * - Jump cuts: the long pauses, the "um"s and the restarts taken out. Every
 *   cut is listed and can be put back on its own.
 * - Punch-ins: the camera moves in on the words said with most weight.
 * - Holding attention: the stretches where nothing new comes on screen.
 *
 * Nothing is lost: the plan keeps the whole recording, and the video is
 * made from it with these applied, so any of them can be undone.
 */

import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  type CutKind,
  type FindCutsSettings,
  type HookLine,
  type PaceState,
  type PlanHistory,
  addCut,
  findCuts,
  findPunchIns,
  getPace,
  removeCut,
  setAlternateZoom,
  setColdOpen,
  setHookTitle,
  switchCut,
  switchPunchIn,
} from "../api";
import { Notice } from "../components";

const TIGHTNESS: { value: number; label: string; hint: string }[] = [
  { value: 0.12, label: "Tight", hint: "barely a breath between lines: fast, punchy" },
  { value: 0.25, label: "Natural", hint: "a short breath between lines" },
  { value: 0.45, label: "Relaxed", hint: "only the long pauses go" },
];

const KIND_LABELS: Record<CutKind, string> = {
  silence: "Pause",
  filler: "Filler",
  repeat: "Restart",
  edge: "Silent edge",
  manual: "Your cut",
};

function clock(seconds: number): string {
  const s = Math.max(0, seconds);
  const minutes = Math.floor(s / 60);
  return `${minutes}:${(s % 60).toFixed(1).padStart(4, "0")}`;
}

function same(a: [number, number] | null, hook: HookLine): boolean {
  return !!a && Math.abs(a[0] - hook.start) < 0.01 && Math.abs(a[1] - hook.end) < 0.01;
}

export function PacePanel({
  jobId,
  time,
  refresh,
  footage,
  onSeek,
  onSaved,
}: {
  jobId: string;
  /** The playhead, on the video's clock. */
  time: number;
  /** Changes when the plan does, so the panel reloads. */
  refresh: number;
  /** Whether the video shows the speaker: punch-ins need a face to move in on. */
  footage: boolean;
  onSeek: (seconds: number) => void;
  onSaved: (history: PlanHistory) => void;
}) {
  const [pace, setPace] = useState<PaceState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [settings, setSettings] = useState<FindCutsSettings>({ keep_pause: 0.25, fillers: true, repeats: true, edges: true });
  const [title, setTitle] = useState("");
  const [mark, setMark] = useState<number | null>(null);
  const [searched, setSearched] = useState(false);
  const titleTimer = useRef<number | undefined>(undefined);
  const typing = useRef(false);

  useEffect(() => {
    let current = true;
    void getPace(jobId)
      .then((loaded) => {
        if (!current) return;
        setPace(loaded);
        if (!typing.current) setTitle(loaded.hook_title);
      })
      .catch((caught: unknown) => current && setError(caught instanceof ApiError ? caught.message : "The pace could not be read."));
    return () => {
      current = false;
    };
  }, [jobId, refresh]);

  const run = async (what: string, action: () => Promise<PaceState & PlanHistory>) => {
    setError(null);
    setBusy(what);
    try {
      const result = await action();
      setPace(result);
      onSaved(result);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "That could not be changed.");
    } finally {
      setBusy(null);
    }
  };

  const changeTitle = (text: string) => {
    setTitle(text);
    typing.current = true;
    window.clearTimeout(titleTimer.current);
    titleTimer.current = window.setTimeout(() => {
      typing.current = false;
      void run("title", () => setHookTitle(jobId, text));
    }, 500);
  };
  useEffect(() => () => window.clearTimeout(titleTimer.current), []);

  if (!pace) return error ? <Notice tone="error">{error}</Notice> : <p className="muted">Reading the pace…</p>;

  const saved = pace.recording_seconds - pace.video_seconds;
  const cutsOn = pace.cuts.filter((c) => c.on).length;
  const found = pace.cuts.some((c) => c.kind !== "manual");
  const opening = pace.cold_open;

  return (
    <div className="pace">
      {error && <Notice tone="error">{error}</Notice>}

      <div className="card pace-hook">
        <header>
          <h2>The hook</h2>
          <p>The first two seconds decide whether someone stays. Open on your strongest line, then tell the story.</p>
        </header>
        <label className="field">
          <span className="label">Hook title</span>
          <input
            type="text"
            value={title}
            maxLength={80}
            placeholder="e.g. Three towns flooded overnight"
            aria-describedby="hook-title-hint"
            onChange={(event) => changeTitle(event.target.value)}
          />
          <span className="hint" id="hook-title-hint">
            Big and bold over the first seconds. Leave it empty for none.
          </span>
        </label>

        {opening && (
          <div className="pace-opening" role="status">
            <span>
              Opens with {(opening[1] - opening[0]).toFixed(1)} s from {clock(opening[0])} in your recording,
              then plays from the start.
            </span>
            <button type="button" className="btn btn-small" onClick={() => onSeek(0)}>
              Watch it
            </button>
            <button type="button" className="btn btn-quiet btn-small" disabled={!!busy} onClick={() => void run("open", () => setColdOpen(jobId, null))}>
              Remove
            </button>
          </div>
        )}

        <h3>Lines that could open the video</h3>
        {pace.hooks.length === 0 ? (
          <p className="muted">No line stands out. A question, a number or a surprise makes a strong opener.</p>
        ) : (
          <ol className="pace-hooks">
            {pace.hooks.map((hook) => {
              const chosen = same(opening, hook);
              return (
                <li key={`${hook.start}`} aria-current={chosen || undefined}>
                  <q>{hook.text}</q>
                  <span className="pace-reasons">
                    {hook.reasons.map((reason) => (
                      <span key={reason} className="pace-reason">
                        {reason}
                      </span>
                    ))}
                  </span>
                  <span className="pace-actions">
                    {hook.video_at !== null && (
                      <button type="button" className="btn btn-quiet btn-small" onClick={() => onSeek(hook.video_at ?? 0)}>
                        Play
                      </button>
                    )}
                    <button
                      type="button"
                      className={`btn btn-small${chosen ? "" : " btn-primary"}`}
                      disabled={chosen || !!busy}
                      onClick={() => void run("open", () => setColdOpen(jobId, { start: hook.start, end: hook.end }))}
                    >
                      {chosen ? "Opens the video" : "Open with this"}
                    </button>
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </div>

      <div className="card pace-cuts">
        <header>
          <h2>Jump cuts</h2>
          <p>
            {saved > 0.05 ? (
              <>
                <strong>{clock(pace.recording_seconds)}</strong> of recording plays in{" "}
                <strong>{clock(pace.video_seconds)}</strong>: {saved.toFixed(1)} s of waiting taken out.
              </>
            ) : (
              "Take out the long pauses, the “um”s and the restarts. Each cut can be put back."
            )}
          </p>
        </header>
        <fieldset className="pace-tightness">
          <legend className="label">How tight</legend>
          {TIGHTNESS.map((option) => (
            <label key={option.value} title={option.hint}>
              <input
                type="radio"
                name="tightness"
                checked={settings.keep_pause === option.value}
                onChange={() => setSettings((s) => ({ ...s, keep_pause: option.value }))}
              />
              {option.label}
            </label>
          ))}
        </fieldset>
        <div className="pace-switches">
          <label>
            <input type="checkbox" checked={settings.fillers} onChange={(event) => setSettings((s) => ({ ...s, fillers: event.target.checked }))} />
            “Um”, “uh”, “er”
          </label>
          <label>
            <input type="checkbox" checked={settings.repeats} onChange={(event) => setSettings((s) => ({ ...s, repeats: event.target.checked }))} />
            Words said twice
          </label>
          <label>
            <input type="checkbox" checked={settings.edges} onChange={(event) => setSettings((s) => ({ ...s, edges: event.target.checked }))} />
            Silence at the start and end
          </label>
        </div>
        <button type="button" className="btn btn-primary" disabled={!!busy} onClick={() => void run("cuts", () => findCuts(jobId, settings)).then(() => setSearched(true))}>
          {busy === "cuts" ? "Finding…" : found ? "Find them again" : "Find jump cuts"}
        </button>

        {searched && !found && !busy && (
          <p className="muted pace-count" role="status">
            Nothing to cut: no long pauses, “um”s or restarts. Try “Tight”, or cut a stretch yourself.
          </p>
        )}
        {pace.cuts.length > 0 && (
          <>
            <p className="muted pace-count">
              {cutsOn} of {pace.cuts.length} cuts made. Untick one to put it back. Update the video to watch it
              with the cuts.
            </p>
            <ul className="list-plain pace-list" aria-label="Cuts">
              {pace.cuts.map((cut) => (
                <li key={cut.index} data-on={cut.on}>
                  <label>
                    <input
                      type="checkbox"
                      checked={cut.on}
                      disabled={!!busy}
                      onChange={(event) => void run("cut", () => switchCut(jobId, cut.index, event.target.checked))}
                    />
                    <span className="pace-kind">{KIND_LABELS[cut.kind]}</span>
                    <span>{cut.kind === "manual" || !cut.label ? `${(cut.end - cut.start).toFixed(1)} s` : cut.label}</span>
                  </label>
                  {cut.video_at !== null && (
                    <button type="button" className="link-button" onClick={() => onSeek(Math.max(0, (cut.video_at ?? 0) - 1))}>
                      {clock(cut.video_at)}
                    </button>
                  )}
                  {cut.kind === "manual" && (
                    <button
                      type="button"
                      className="btn btn-quiet btn-small"
                      aria-label={`Remove your cut at ${clock(cut.video_at ?? 0)}`}
                      onClick={() => void run("cut", () => removeCut(jobId, cut.index))}
                    >
                      Remove
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}

        <h3>Cut a stretch yourself</h3>
        <p className="muted">Play to where it starts and mark it, then to where it ends.</p>
        <div className="pace-mark">
          {mark === null ? (
            <button type="button" className="btn btn-small" onClick={() => setMark(time)}>
              Mark the start ({clock(time)})
            </button>
          ) : (
            <>
              <span>
                From {clock(mark)} to {clock(time)}
              </span>
              <button
                type="button"
                className="btn btn-small btn-primary"
                disabled={time - mark < 0.1 || !!busy}
                onClick={() => {
                  const start = mark;
                  setMark(null);
                  void run("cut", () => addCut(jobId, start, time)).then(() => onSeek(start));
                }}
              >
                Cut it
              </button>
              <button type="button" className="btn btn-quiet btn-small" onClick={() => setMark(null)}>
                Cancel
              </button>
            </>
          )}
        </div>
      </div>

      <div className="card pace-punch">
        <header>
          <h2>Punch-ins</h2>
          <p>The camera moves in on the words you stress, and back out: the energy of a cut without one.</p>
        </header>
        {!footage ? (
          <Notice tone="info">Punch-ins move in on you: they need a video made with your camera recording.</Notice>
        ) : (
          <>
            <button type="button" className="btn" disabled={!!busy} onClick={() => void run("punch", () => findPunchIns(jobId))}>
              {busy === "punch" ? "Listening…" : pace.punch_ins.length ? "Find them again" : "Find the stressed words"}
            </button>
            {pace.punch_ins.length > 0 && (
              <ul className="list-plain pace-list" aria-label="Punch-ins">
                {pace.punch_ins.map((punch) => (
                  <li key={punch.index} data-on={punch.on}>
                    <label>
                      <input
                        type="checkbox"
                        checked={punch.on}
                        disabled={!!busy}
                        onChange={(event) => void run("punch", () => switchPunchIn(jobId, punch.index, event.target.checked))}
                      />
                      <span>“{punch.word}”</span>
                      <span className="muted">{Math.round((punch.zoom - 1) * 100)}% closer</span>
                    </label>
                    {punch.video_at !== null && (
                      <button type="button" className="link-button" onClick={() => onSeek(Math.max(0, (punch.video_at ?? 0) - 0.8))}>
                        {clock(punch.video_at)}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <label className="pace-switch">
              <input
                type="checkbox"
                checked={pace.alternate_zoom}
                disabled={!!busy}
                onChange={(event) => void run("zoom", () => setAlternateZoom(jobId, event.target.checked))}
              />
              Move in a little at every other jump cut, so cuts do not jump on the spot
            </label>
          </>
        )}
      </div>

      <div className="card pace-still">
        <header>
          <h2>Holding attention</h2>
          <p>Where nothing new comes on screen for a while: add a pop-up, a picture or a punch-in there.</p>
        </header>
        {pace.stillness.length === 0 ? (
          <p className="muted">Something new comes on screen at least every four seconds.</p>
        ) : (
          <ul className="list-plain pace-list" aria-label="Still stretches">
            {pace.stillness.map((still) => (
              <li key={still.start}>
                <span>
                  {clock(still.start)}–{clock(still.end)}: {(still.end - still.start).toFixed(0)} s with nothing new
                </span>
                <button type="button" className="link-button" onClick={() => onSeek(still.start + Math.min(2, (still.end - still.start) / 2))}>
                  Go there
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
