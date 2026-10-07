import { useEffect, useRef, useState } from "react";
import { previewBatchShort, exportBatchShorts, getBatchShorts, artifactUrl, cancelJob, resumeJob,
  type BatchPreview, type BatchJob, type ShortsControls, type ShortChoice, type ScenePlan } from "../api";
import { DestinationVariants } from "./DestinationVariants";
import { ShortCollections } from "./ShortCollections";
import { Notice } from "../components";

type Clip = {id: number; choice: ShortChoice; preview: BatchPreview | null; reviewed: boolean};
const lookNames = {authority: "Clean authority", energy: "High energy", cinema: "Cinematic story"};
function ClipPlayer({preview}: {preview: BatchPreview}) {
  const player = useRef<HTMLVideoElement>(null);
  useEffect(() => { const video = player.current; return () => { video?.pause(); }; }, [preview]);
  return <div className="story-preview"><video ref={player} controls preload="metadata" src={preview.url} aria-label="Batch clip preview" />
    <p className="hint">{preview.note} {preview.music_note}</p></div>;
}
export function BatchShorts({jobId, plan, data, current}: {
  jobId: string; plan: ScenePlan; data: ShortsControls | null; current: ShortChoice | null;
}) {
  const [open, setOpen] = useState(false), [clips, setClips] = useState<Clip[]>([]);
  const [jobs, setJobs] = useState<BatchJob[]>([]), [height, setHeight] = useState<1280 | 1920>(1280);
  const [busy, setBusy] = useState(false), [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0), nextId = useRef(0);
  useEffect(() => {
    generation.current++; setClips([]); setBusy(false); setStatus(""); setError(null);
    return () => { generation.current++; };
  }, [jobId, plan]);
  useEffect(() => {
    let live = true, timer: ReturnType<typeof setTimeout> | undefined;
    async function refresh() {
      try {
        const result = await getBatchShorts(jobId);
        if (live) setJobs(result.jobs);
      } catch (e) { if (live) setError(e instanceof Error ? e.message : "Could not load clip exports."); }
      if (live) timer = setTimeout(() => void refresh(), 2000);
    }
    setJobs([]); void refresh();
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, [jobId]);
  function add(choice: ShortChoice) {
    if (busy || clips.length >= 6) return;
    if (clips.some(c => c.choice.first_word === choice.first_word && c.choice.last_word === choice.last_word)) {
      setError("That word range is already in your shortlist."); return;
    }
    setClips(rows => [...rows, {id: ++nextId.current, choice, preview: null, reviewed: false}]); setError(null);
  }
  function change(id: number, changes: Partial<ShortChoice>) {
    setClips(rows => rows.map(c => c.id === id ? {...c, choice: {...c.choice, ...changes}, preview: null, reviewed: false} : c));
    setStatus(""); setError(null);
  }
  const overlap = clips.some((a, i) => clips.some((b, j) => j > i &&
    a.choice.first_word <= b.choice.last_word && a.choice.last_word >= b.choice.first_word));
  const allReviewed = !!clips.length && clips.every(c => c.preview && c.reviewed);
  async function preview(selected: Clip[]) {
    if (busy) return;
    const version = generation.current;
    setBusy(true); setError(null);
    try {
      for (const [i, clip] of selected.entries()) {
        if (generation.current !== version) return;
        setStatus(`Previewing clip ${i + 1} of ${selected.length}…`);
        const rendered = await previewBatchShort(jobId, clip.choice);
        if (generation.current !== version) return;
        setClips(rows => rows.map(c => c.id === clip.id ? {...c, preview: rendered, reviewed: false} : c));
      }
      setStatus("Previews ready. Check each opening, ending and soundtrack before exporting.");
    } catch (e) { if (generation.current === version) { setError(e instanceof Error ? e.message : "Could not preview the clip."); setStatus(""); } }
    finally { if (generation.current === version) setBusy(false); }
  }
  async function queue() {
    if (!data || busy || !allReviewed || overlap) return;
    const version = generation.current;
    setBusy(true); setError(null); setStatus("Queueing your reviewed clips…");
    try {
      await exportBatchShorts(jobId, data.revision, clips.map(c => c.preview!.clip_id), height);
      const result = await getBatchShorts(jobId);
      if (generation.current === version) { setJobs(result.jobs); setStatus("Clips queued. Each export runs as its own project; you can leave this tab."); }
    } catch (e) { if (generation.current === version) { setError(e instanceof Error ? e.message : "Could not queue clips."); setStatus(""); } }
    finally { if (generation.current === version) setBusy(false); }
  }
  async function control(id: string, retry: boolean) {
    try { if (retry) await resumeJob(id); else await cancelJob(id); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not update the export."); }
  }
  return <details className="story-composer batch-shorts" open={open} onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>Batch Shorts · one recording, several stories</summary>
    <p>Build up to six distinct clips. Trim their word ranges, audition each with its soundtrack, then export the reviewed set. Your source project stays intact.</p>
    <fieldset disabled={busy || !data?.words.length}>
      <div className="story-actions"><button className="btn" disabled={!current || clips.length >= 6} onClick={() => current && add(current)}>Add current passage</button>
        <button className="btn" disabled={clips.length >= 6 || !data?.suggestions.length} onClick={() => {
          if (!data) return;
          const additions = data.suggestions.filter(s => !clips.some(c => c.choice.first_word === s.first_word && c.choice.last_word === s.last_word)).slice(0, 6 - clips.length);
          setClips(rows => [...rows, ...additions.map(s => ({id: ++nextId.current,
            choice: {revision: data.revision, first_word: s.first_word, last_word: s.last_word, vertical: true}, preview: null, reviewed: false}))]);
        }}>Add suggested passages</button></div>
    </fieldset>
    {open && <div className="story-blocks">{clips.map((clip, i) => <article className="batch-clip" key={clip.id} aria-label={`Batch clip ${i + 1}`}>
      <h4>Clip {i + 1}</h4>
      <fieldset disabled={busy}>
        <div className="story-fields"><label className="label">From word<input aria-label={`Clip ${i + 1} first word`} type="number" min={1} max={data?.words.length ?? 1} value={clip.choice.first_word + 1}
          onChange={e => change(clip.id, {first_word: Math.max(0, Number(e.target.value) - 1)})} /></label>
        <label className="label">Through word<input aria-label={`Clip ${i + 1} last word`} type="number" min={1} max={data?.words.length ?? 1} value={clip.choice.last_word + 1}
          onChange={e => change(clip.id, {last_word: Math.max(0, Number(e.target.value) - 1)})} /></label>
        <label className="label">Look<select aria-label={`Clip ${i + 1} look`} value={clip.choice.look ?? ""} onChange={e => change(clip.id, {look: e.target.value as ShortChoice["look"] || null})}>
          <option value="">Current styling</option>{Object.entries(lookNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
        <label><input type="checkbox" checked={clip.choice.vertical} onChange={e => change(clip.id, {vertical: e.target.checked})} /> Portrait framing</label>
        <label><input type="checkbox" disabled={!clip.choice.look} checked={!!clip.choice.match_captions} onChange={e => change(clip.id, {match_captions: e.target.checked})} /> Match captions to look</label></div>
        <p>{data?.words.slice(clip.choice.first_word, clip.choice.last_word + 1).map(w => w.text).join(" ")}</p>
        <div className="story-actions"><button className="btn" onClick={() => void preview([clip])}>Preview clip {i + 1}</button>
          <button className="btn" onClick={() => { setClips(rows => rows.filter(c => c.id !== clip.id)); setStatus(""); }}>Remove clip {i + 1}</button></div>
      </fieldset>
      {clip.preview && <><ClipPlayer preview={clip.preview} />
        <details><summary>Opening context and ending</summary><p>Before: {clip.preview.context_before || "Start of passage"}</p>
          <p>Ending: {clip.preview.payoff}</p><p>After: {clip.preview.context_after || "End of passage"}</p>
          {clip.preview.warnings.map(w => <p className="hint" key={w}>{w}</p>)}</details>
        <label><input type="checkbox" disabled={busy} checked={clip.reviewed} onChange={e => {
          const reviewed = e.target.checked; setClips(rows => rows.map(c => c.id === clip.id ? {...c, reviewed} : c));
        }} /> I checked this clip’s opening, ending and sound</label>
      </>}
    </article>)}</div>}
    {overlap && <Notice>Some clips share spoken words. Trim the ranges to make each clip distinct before exporting.</Notice>}
    <fieldset disabled={busy || !data}><div className="story-actions">
      <button className="btn" disabled={!clips.some(c => !c.preview)} onClick={() => void preview(clips.filter(c => !c.preview))}>Preview unrendered clips</button>
      <label className="label">Export size<select aria-label="Batch export size" value={height} onChange={e => setHeight(Number(e.target.value) as 1280 | 1920)}>
        <option value={1280}>720 × 1280 portrait</option><option value={1920}>1080 × 1920 portrait</option></select></label>
      <button className="btn btn-primary" disabled={!allReviewed || overlap} onClick={() => void queue()}>Export reviewed clips</button>
    </div></fieldset>
    <p className="hint">Suggestions use transcript cues; review the full thought. Exports keep current music and credits. The size sets frame height; non-portrait clips retain their aspect. Repeated export clicks reuse the same jobs. Keep the source project and its media to edit or resume clips.</p>
    {status && <p role="status">{status}</p>}{error && <Notice tone="error">{error}</Notice>}
    {jobs.length > 0 && <DestinationVariants jobId={jobId} jobs={jobs} /> }
    {jobs.length > 0 && <ShortCollections jobId={jobId} jobs={jobs} />}
    {jobs.length > 0 && <section aria-label="Batch export jobs"><h4>Your clip exports</h4>{jobs.map(item => <article className="batch-export" key={item.job.id}>
      <strong>{item.job.audio_name}</strong><p>{item.job.state} · {item.job.message} · {item.height}px height</p>
      {item.job.error && <Notice tone="error">{item.job.error}</Notice>}
      <div className="story-actions">{item.job.state === "succeeded" && <a className="btn" href={artifactUrl(item.job.id, "video")} download>Download clip</a>}
        {item.job.resumable && <button className="btn" onClick={() => void control(item.job.id, true)}>Resume clip</button>}
        {(item.job.state === "queued" || item.job.state === "running") && <button className="btn" onClick={() => void control(item.job.id, false)}>Stop clip</button>}
      </div><p className="hint">Open this clip from Projects to edit it further.</p>
    </article>)}</section>}
  </details>;
}
