import { useEffect, useRef, useState } from "react";
import { previewTrim, saveTrim, type PlanEditResult, type ScenePlan, type ShortPreview } from "../api";
import { Notice } from "../components";

export function ManualTrim({jobId, plan, revision, time, canListen, onEdited, onSeek}: {
  jobId: string; plan: ScenePlan; revision: string; time: number; canListen: boolean;
  onEdited: (result: PlanEditResult) => void; onSeek: (seconds: number) => void;
}) {
  const duration = plan.total_frames / plan.fps;
  const [start, setStart] = useState("0"), [end, setEnd] = useState(duration.toFixed(3));
  const [mode, setMode] = useState<"keep" | "remove">("remove");
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ShortPreview | null>(null);
  const player = useRef<HTMLVideoElement>(null), generation = useRef(0);
  useEffect(() => () => { generation.current++; player.current?.pause(); }, []);
  const first = Math.round(Number(start) * plan.fps), last = Math.round(Number(end) * plan.fps);
  const valid = start.trim() !== "" && end.trim() !== "" && Number.isFinite(first) && Number.isFinite(last) && first >= 0 && last > first && last <= plan.total_frames;
  const frames = mode === "keep" ? last - first : plan.total_frames - (last - first);
  const seconds = frames / plan.fps;
  function reset() { player.current?.pause(); setPreview(null); setError(null); generation.current++; }
  async function run(save: boolean) {
    if (!valid || busy || frames <= 0) return;
    const version = ++generation.current; setBusy(true); setError(null);
    const choice = {revision, start_frame: first, end_frame: last, mode};
    try {
      if (save) { const result = await saveTrim(jobId, choice); if (version === generation.current) onEdited(result); }
      else { const result = await previewTrim(jobId, choice); if (version === generation.current) setPreview(result); }
    } catch (e) { if (version === generation.current) setError(e instanceof Error ? e.message : "The trim could not be saved."); }
    finally { if (version === generation.current) setBusy(false); }
  }
  return <section className="manual-trim" aria-label="Manual section trim">
    <h4>Your cut. Your timing.</h4><p>Choose a section to keep, or cut it out and close the gap. The original upload stays intact.</p>
    <fieldset disabled={busy}>
      <label className="label">Section action<select aria-label="Section action" value={mode} onChange={e => { reset(); setMode(e.target.value as "keep" | "remove"); }}><option value="remove">Remove section</option><option value="keep">Keep only this section</option></select></label>
      <div className="story-fields"><label className="label">Start (seconds)<input type="number" aria-label="Trim start seconds" min={0} max={duration} step={1 / plan.fps} value={start} onChange={e => { reset(); setStart(e.target.value); }} /></label>
        <label className="label">End (seconds)<input type="number" aria-label="Trim end seconds" min={0} max={duration} step={1 / plan.fps} value={end} onChange={e => { reset(); setEnd(e.target.value); }} /></label></div>
      <div className="story-actions"><button className="btn" disabled={!canListen} onClick={() => { reset(); setStart(time.toFixed(3)); }}>Set start at playhead</button>
        <button className="btn" disabled={!canListen} onClick={() => { reset(); setEnd(time.toFixed(3)); }}>Set end at playhead</button>
        <button className="btn" disabled={!canListen || !valid} onClick={() => onSeek(first / plan.fps)}>Review section start</button></div>
      {valid && <p aria-live="polite">{duration.toFixed(2)} s → {Math.max(0, seconds).toFixed(2)} s · {(duration - seconds).toFixed(2)} s removed</p>}
      <div className="actions"><button className="btn" disabled={!valid || seconds < 3 || seconds > 60} onClick={() => void run(false)}>Preview section edit</button>
        <button className="btn btn-primary" disabled={!valid || frames <= 0 || (mode === "keep" && frames === plan.total_frames)} onClick={() => void run(true)}>{busy ? "Working…" : "Save section edit"}</button></div>
    </fieldset>
    <p className="hint">Cuts snap to video frames. Preview a 3–60 second result. Save, then Update video to apply it and refit the soundtrack. Undo restores the previous edit. A cut inside corrected captions needs their original text restored first.</p>
    {preview && <div className="pacing-preview"><video ref={player} controls preload="metadata" src={preview.url} aria-label="Section edit preview" /><p className="hint">{preview.note}</p></div>}
    {error && <Notice tone="error">{error}</Notice>}
  </section>;
}
