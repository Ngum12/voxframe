import { useEffect, useRef, useState } from "react";
import { getPacing, previewPacing, savePacing, type PacingControls, type PlanEditResult, type ScenePlan, type ShortPreview } from "../api";
import { Notice } from "../components";

const GROUPS = [
  ["pause", "Review pauses"], ["filler", "Review hesitation words"], ["repeat", "Review repeated phrases"],
] as const;

export function PacingStudio({ jobId, plan, canListen, onEdited, onSeek }: {
  jobId: string; plan: ScenePlan; canListen: boolean; onEdited: (result: PlanEditResult) => void; onSeek: (seconds: number) => void;
}) {
  const [data, setData] = useState<PacingControls | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [preview, setPreview] = useState<ShortPreview | null>(null);
  const request = useRef(0);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    request.current++;
    setData(null); setSelected([]); setPreview(null); setError(null); setBusy(false);
    getPacing(jobId).then(d => { if (live) setData(d); })
      .catch(e => { if (live) setError(e.message); });
    return () => { live = false; request.current++; };
  }, [jobId, plan]);
  const saved = data?.cuts.filter(c => selected.includes(c.id)).reduce((sum, c) => sum + c.seconds, 0) ?? 0;
  async function apply() {
    if (!data) return;
    setBusy(true); setError(null);
    try { onEdited(await savePacing(jobId, selected, data.revision)); }
    catch (e) { setError(e instanceof Error ? e.message : "Cuts could not be saved."); }
    finally { setBusy(false); }
  }
  async function audition() {
    if (!data) return;
    const current = ++request.current;
    setBusy(true); setError(null);
    try {
      const result = await previewPacing(jobId, selected, data.revision);
      if (current === request.current) setPreview(result);
    } catch (e) {
      if (current === request.current) setError(e instanceof Error ? e.message : "Preview could not be rendered.");
    } finally { if (current === request.current) setBusy(false); }
  }
  return <section className="transition-studio" aria-label="Shorts pacing">
    <span className="eyebrow">MAKE EVERY WORD COUNT</span>
    <h3>Keep the momentum</h3>
    <p>Review pauses, hesitation words and repeated phrases. Choose what to remove; your voice, footage and captions move together.</p>
    <Notice tone="info">Listen first. A transcript gap may contain missed words, and a hesitation or repetition may be intentional. Nothing is selected automatically.</Notice>
    {error && <Notice tone="error">{error}</Notice>}
    {!data && !error && <p role="status">Finding pacing suggestions…</p>}
    {data && <>
      <p role="status">{data.seconds.toFixed(1)} s → {(data.seconds - saved).toFixed(1)} s · {saved.toFixed(1)} s removed</p>
      {data.cuts.length === 0 && <p>No long pauses found. No clear hesitation or repetition cues found. Corrected captions are kept intact.</p>}
      {!canListen && <p className="hint">Update the video to listen on the current timeline.</p>}
      {GROUPS.map(([kind, title]) => {
        const cuts = data.cuts.filter(c => c.kind === kind);
        if (!cuts.length) return null;
        return <fieldset key={kind} disabled={busy}><legend>{title}</legend>
          {cuts.map(c => <div key={c.id} className="pacing-cut">
            <label><input type="checkbox" checked={selected.includes(c.id)} onChange={e => {
              request.current++; setPreview(null);
              setSelected(s => e.target.checked ? [...s, c.id] : s.filter(id => id !== c.id));
            }} />
              <span className="pacing-copy">{c.kind === "pause" ? <> “{c.before}” → “{c.after}”</> : <> Remove “{c.removed_text}”</>}
              {" · "}{c.seconds.toFixed(2)} s
              {c.kind !== "pause" && <span className="pacing-context">{c.before} <del>{c.removed_text}</del> {c.after}</span>}
              <span className="hint pacing-context">{c.reason}</span></span>
            </label>
            <button className="link-button" type="button" disabled={!canListen} onClick={() => onSeek(Math.max(0, c.start - 1))}>Listen at {c.start.toFixed(1)} s</button>
          </div>)}
        </fieldset>;
      })}
      <div className="actions">
        <button className="btn" type="button" disabled={busy || !selected.length || selected.length > 100 || data.seconds - saved < 3 || data.seconds - saved > 60} onClick={audition}>Preview selected cuts</button>
        <button className="btn btn-primary" type="button" disabled={busy || !selected.length || selected.length > 100} onClick={apply}>{busy ? "Working…" : "Save selected cuts"}</button>
      </div>
      {preview && <div className="pacing-preview"><video controls preload="metadata" src={preview.url} aria-label="Pacing preview" /><p className="hint">{preview.note}</p></div>}
      <p className="hint">Preview a 3–60 second edit before saving. Save up to 100 cuts at once, then update the video to hear the final mix. Undo restores the previous timeline; redo reapplies it.</p>
    </>}
  </section>;
}
