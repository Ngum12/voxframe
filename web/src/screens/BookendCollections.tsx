import { useEffect, useRef, useState } from "react";
import { artifactUrl, cancelJob, resumeJob, getBatchShorts, getBookendCollection, listBookendSignatures, previewBookendCollection, exportBookendCollection, type BatchJob, type BookendCollectionChoice, type BookendCollectionControls, type BookendCollectionPreview, type BookendSignature, type ScenePlan } from "../api";
import { bookendDefaults } from "../bookendDefaults";
import { Notice } from "../components";
import { ShortCollections } from "./ShortCollections";

type Words = Pick<BookendCollectionChoice, "last_word" | "first_word" | "replace_opening" | "replace_closing">;
type Draft = {video: BookendCollectionPreview; opening: boolean; closing: boolean};
function CollectionPreview({draft, title, busy, onReview}: {draft: Draft; title: string; busy: boolean; onReview: (end: "opening" | "closing", checked: boolean) => void}) {
  const player = useRef<HTMLVideoElement>(null), [ready, setReady] = useState(false);
  useEffect(() => { const video = player.current; setReady(!!video && video.readyState >= 1); return () => { video?.pause(); }; }, [draft.video.url]);
  function jump(at: number) { const video = player.current; if (video) { video.pause(); video.currentTime = Math.max(0, Math.min(at, video.duration)); } }
  return <div className="story-preview bookend-collection-preview">
    <p>“{draft.video.opening.quote}” → “{draft.video.closing.quote}”</p>
    <p className="hint">{draft.video.signature.name} · {draft.video.seconds.toFixed(1)}s · {draft.video.music_note}</p>
    <video ref={player} controls playsInline preload="metadata" src={draft.video.url} aria-label={`${title} collection preview`} onLoadedMetadata={() => setReady(true)} onError={() => setReady(false)} />
    <div className="closing-watch-actions"><button className="btn" disabled={!ready} onClick={() => jump(draft.video.opening.start - .5)}>Review the opening</button><button className="btn" disabled={!ready} onClick={() => jump(draft.video.closing.start - 1)}>Review the ending</button></div>
    {!!draft.video.sound?.problems.length && <Notice>{draft.video.sound.problems.join(" ")}</Notice>}
    <label className="check"><input type="checkbox" disabled={busy} checked={draft.opening} onChange={event => onReview("opening", event.target.checked)} />I reviewed this opening and its return to the story</label>
    <label className="check"><input type="checkbox" disabled={busy} checked={draft.closing} onChange={event => onReview("closing", event.target.checked)} />I reviewed this ending and the full soundtrack</label>
  </div>;
}
export function BookendCollections({jobId, plan, initiallyOpen = false}: {jobId: string; plan: ScenePlan; initiallyOpen?: boolean}) {
  const [open, setOpen] = useState(initiallyOpen), [data, setData] = useState<BookendCollectionControls | null>(null);
  const [signatures, setSignatures] = useState<BookendSignature[]>([]), [signature, setSignature] = useState("");
  const [selected, setSelected] = useState<string[]>([]), [words, setWords] = useState<Record<string, Words>>({});
  const [drafts, setDrafts] = useState<Record<string, Draft>>({}), [jobs, setJobs] = useState<BatchJob[]>([]);
  const [busy, setBusy] = useState(false), [status, setStatus] = useState(""), [error, setError] = useState<string | null>(null), [refresh, setRefresh] = useState(0);
  const generation = useRef(0);
  useEffect(() => { generation.current++; setData(null); setSelected([]); setWords({}); setDrafts({}); setSignature(""); setBusy(false); setStatus(""); setError(null); return () => { generation.current++; }; }, [jobId, plan]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    Promise.all([getBookendCollection(jobId), listBookendSignatures()]).then(([controls, recipes]) => {
      if (!alive) return;
      setData(controls); setSignatures(recipes.signatures);
      setSelected(ids => ids.filter(id => controls.sources.some(source => source.job.id === id)));
      setError(null);
    }).catch(e => { if (alive) setError(e instanceof Error ? e.message : "Could not load recordings and signatures."); });
    return () => { alive = false; };
  }, [jobId, plan, open, refresh]);
  useEffect(() => {
    if (!open) return;
    let alive = true, timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try { const result = await getBatchShorts(jobId); if (alive) setJobs(result.jobs.filter(job => !!job.bookend_source)); }
      catch (e) { if (alive) setError(e instanceof Error ? e.message : "Could not load collection exports."); }
      if (alive) timer = setTimeout(() => void poll(), 2000);
    }
    setJobs([]); void poll();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [jobId, open]);
  const recipe = signatures.find(item => item.id === signature);
  function chooseSignature(id: string) { setSignature(id); setWords({}); setStatus(""); }
  const rows = selected.flatMap(id => {
    const source = data?.sources.find(item => item.job.id === id);
    if (!source || !recipe) return [];
    const defaults = bookendDefaults(source.controls, recipe);
    const config = words[id] ?? (defaults ? {...defaults, replace_opening: false, replace_closing: false} : null);
    const first = source.controls.opening.endings.find(item => item.last_word === config?.last_word);
    const last = source.controls.closing.starts.find(item => item.first_word === config?.first_word);
    const blocked = !config || !first || !last || first.last_word >= last.first_word || first.end > last.start
      || (first.pinned && !config.replace_opening) || (last.pinned && !config.replace_closing)
      || (recipe.opening_shot === "speaker" && !first.has_speaker) || (recipe.closing_shot === "speaker" && !last.has_speaker);
    const choice: BookendCollectionChoice | null = config ? {source_job: id, signature_id: recipe.id, revision: source.controls.revision, ...config} : null;
    return [{source, config, first, last, blocked, choice, key: JSON.stringify(choice)}];
  });
  function change(id: string, config: Words, patch: Partial<Words>) { setWords(current => ({...current, [id]: {...config, ...patch}})); setStatus(""); }
  const valid = rows.length > 0 && rows.length === selected.length && rows.every(row => !row.blocked);
  const reviewed = valid && rows.every(row => drafts[row.key]?.opening && drafts[row.key]?.closing);
  async function preview() {
    if (!valid || busy) return;
    const current = generation.current;
    setBusy(true); setError(null);
    try {
      for (let index = 0; index < rows.length; index++) {
        const row = rows[index];
        setStatus(`Previewing recording ${index + 1} of ${rows.length} with its soundtrack…`);
        const video = await previewBookendCollection(jobId, row.choice!);
        if (generation.current !== current) return;
        setDrafts(items => ({...items, [row.key]: {video, opening: false, closing: false}}));
      }
      setStatus("Collection previews ready. Watch and hear each recording, then review both ends.");
    } catch (e) { if (generation.current === current) { setError(e instanceof Error ? e.message : "Could not preview this recording."); setStatus(""); } }
    finally { if (generation.current === current) setBusy(false); }
  }
  async function queue() {
    if (!reviewed || busy) return;
    const current = generation.current;
    setBusy(true); setError(null);
    try {
      const result = await exportBookendCollection(jobId, rows.map(row => drafts[row.key].video.clip_id));
      if (generation.current !== current) return;
      const exports = await getBatchShorts(jobId);
      if (generation.current !== current) return;
      setJobs(exports.jobs.filter(job => !!job.bookend_source));
      setStatus(`${result.jobs.length} reviewed bookend exports queued. Originals stay intact.`);
    } catch (e) { if (generation.current === current) setError(e instanceof Error ? e.message : "Could not export this collection."); }
    finally { if (generation.current === current) setBusy(false); }
  }
  async function control(id: string, resume: boolean) {
    const current = generation.current;
    try { if (resume) await resumeJob(id); else await cancelJob(id); }
    catch (e) { if (generation.current === current) setError(e instanceof Error ? e.message : "Could not update this export."); }
  }
  return <details open={open} className="story-composer bookend-collections" onToggle={event => setOpen(event.currentTarget.open)}><summary>Bookend signature collection · one recipe, several stories</summary>
    {open && <section aria-label="Bookend signature collection">
      <h3>Your signature, on every recording.</h3><p>Choose up to six finished 3–60 second videos. Audition a saved pair on each story, review both ends and export separate editable projects.</p>
      <fieldset disabled={busy}>
        <button className="btn" onClick={() => setRefresh(value => value + 1)}>Refresh recordings and signatures</button>
        <label className="label">Collection signature<select aria-label="Collection signature" value={signature} onChange={event => chooseSignature(event.target.value)}><option value="">Choose a saved bookend signature</option>{signatures.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        {data && !signatures.length && <p className="hint">Save a reviewed pair in Bookend auditions first.</p>}
        {data && !data.sources.length && <p className="hint">Finish a 3–60 second recording first.</p>}
        <div className="collection-options">{data?.sources.map((source, index) => <label className="check" key={source.job.id}><input type="checkbox" aria-label={`Use recording ${index + 1}: ${source.job.audio_name}`} checked={selected.includes(source.job.id)} disabled={source.controls.default_last_word === null || (selected.length >= 6 && !selected.includes(source.job.id))} onChange={event => setSelected(ids => event.target.checked ? [...ids, source.job.id] : ids.filter(id => id !== source.job.id))} /><span>{source.job.audio_name} · {source.seconds.toFixed(1)}s{source.controls.default_last_word === null && " · Needs separate timed phrases"}</span></label>)}</div>
      </fieldset>
      <div className="story-blocks">{rows.map((row, index) => <section className="story-block" aria-label={`Collection recording ${index + 1}`} key={row.source.job.id}>
        <h4>Recording {index + 1} · {row.source.job.audio_name}</h4>
        {row.config && <fieldset disabled={busy}><div className="bookend-fields">
          <label className="label">Opening words<select aria-label="Collection opening words" value={row.config.last_word} onChange={event => change(row.source.job.id, row.config!, {last_word: Number(event.target.value)})}>{row.source.controls.opening.endings.map(item => <option key={item.last_word} value={item.last_word}>{item.quote}</option>)}</select></label>
          <label className="label">Closing words<select aria-label="Collection closing words" value={row.config.first_word} onChange={event => change(row.source.job.id, row.config!, {first_word: Number(event.target.value)})}>{row.source.controls.closing.starts.map(item => <option key={item.first_word} value={item.first_word}>{item.quote}</option>)}</select></label>
        </div>
        {row.first?.pinned && <label className="check"><input type="checkbox" checked={row.config.replace_opening} onChange={event => change(row.source.job.id, row.config!, {replace_opening: event.target.checked})} />Allow replacing this recording's pinned opening text</label>}
        {row.last?.pinned && <label className="check"><input type="checkbox" checked={row.config.replace_closing} onChange={event => change(row.source.job.id, row.config!, {replace_closing: event.target.checked})} />Allow replacing this recording's pinned closing text</label>}
        </fieldset>}
        {row.blocked && <Notice>Choose separate timed phrases, allow replacing pinned text when needed, and ensure this recipe’s speaker shots have footage. You can choose another recipe.</Notice>}
        {drafts[row.key] && <CollectionPreview key={row.key} draft={drafts[row.key]} title={row.source.job.audio_name} busy={busy} onReview={(end, checked) => setDrafts(items => ({...items, [row.key]: {...items[row.key], [end]: checked}}))} />}
      </section>)}</div>
      <div className="story-actions"><button className="btn" disabled={busy || !valid} onClick={() => void preview()}>Preview signature collection</button><button className="btn btn-primary" disabled={busy || !reviewed} onClick={() => void queue()}>Export reviewed bookend collection</button></div>
      <p className="hint">Each preview includes that recording's soundtrack. Exports keep its source timings, middle, cards and export size. Render again after media changes; new previews need new reviews. Repeated export clicks reuse the same jobs. Keep source projects and media to edit or resume exports.</p>
      {status && <p role="status">{status}</p>}{error && <Notice tone="error">{error}</Notice>}
      {!!jobs.length && <><section aria-label="Bookend collection exports"><h4>Your bookend exports</h4>{jobs.map(item => <article className="batch-export" key={item.job.id}><strong>{item.job.audio_name}</strong><p>{item.job.state} · {item.job.message}</p>{item.job.error && <Notice tone="error">{item.job.error}</Notice>}<div className="story-actions">{item.job.state === "succeeded" && <a className="btn" href={artifactUrl(item.job.id, "video")} download>Download clip</a>}{item.job.resumable && <button className="btn" onClick={() => void control(item.job.id, true)}>Resume clip</button>}{(item.job.state === "queued" || item.job.state === "running") && <button className="btn" onClick={() => void control(item.job.id, false)}>Stop clip</button>}</div><p className="hint">Open this export from Projects to edit it further.</p></article>)}</section><ShortCollections jobId={jobId} jobs={jobs} bookends /></>}
    </section>}
  </details>;
}
