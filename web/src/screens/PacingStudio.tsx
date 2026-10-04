import { useEffect, useState } from "react";
import { getPacing, savePacing, type PacingControls, type PlanEditResult, type ScenePlan } from "../api";
import { Notice } from "../components";

export function PacingStudio({ jobId, plan, canListen, onEdited, onSeek }: {
  jobId: string; plan: ScenePlan; canListen: boolean; onEdited: (result: PlanEditResult) => void; onSeek: (seconds: number) => void;
}) {
  const [data, setData] = useState<PacingControls | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setData(null); setSelected([]);
    getPacing(jobId).then(d => { if (live) setData(d); })
      .catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [jobId, plan]);
  const saved = data?.cuts.filter(c => selected.includes(c.id)).reduce((sum, c) => sum + c.seconds, 0) ?? 0;
  async function apply() {
    setBusy(true); setError(null);
    try { onEdited(await savePacing(jobId, selected)); }
    catch (e) { setError(e instanceof Error ? e.message : "Cuts could not be saved."); }
    finally { setBusy(false); }
  }
  return <section className="transition-studio" aria-label="Shorts pacing">
    <h3>Keep the momentum</h3>
    <p>Choose long pauses to shorten. Every cut keeps a little breathing room and moves your voice, footage and captions together.</p>
    <Notice tone="info">These are gaps in the transcript. Listen first: a gap may contain a dramatic pause, background sound or words the transcription missed.</Notice>
    {error && <Notice tone="error">{error}</Notice>}
    {!data && !error && <p role="status">Finding pause suggestions…</p>}
    {data && <>
      <p role="status">{data.seconds.toFixed(1)} s → {(data.seconds - saved).toFixed(1)} s · {saved.toFixed(1)} s removed</p>
      {data.cuts.length === 0 && <p>No long pauses found. Corrected captions are kept intact.</p>}
      {!canListen && <p className="hint">Update the video to listen on the current timeline.</p>}
      <fieldset disabled={busy}><legend>Review pauses</legend>
        {data.cuts.map(c => <div key={c.id} className="pacing-cut">
          <label><input type="checkbox" checked={selected.includes(c.id)} onChange={e => setSelected(s => e.target.checked ? [...s, c.id] : s.filter(id => id !== c.id))} />
            {" “"}{c.before}{"” → “"}{c.after}{"” · "}{c.seconds.toFixed(2)} s</label>
          <button className="link-button" type="button" disabled={!canListen} onClick={() => onSeek(Math.max(0, c.start - 1))}>Listen at {c.start.toFixed(1)} s</button>
        </div>)}
      </fieldset>
      <button className="btn btn-primary" type="button" disabled={busy || !selected.length || selected.length > 100} onClick={apply}>{busy ? "Saving…" : "Save selected cuts"}</button>
      <p className="hint">Save up to 100 cuts at once, then update the video to hear the result. Undo restores the previous timeline; redo reapplies it. Update the video before listening to further suggestions.</p>
    </>}
  </section>;
}
