import { useEffect, useRef, useState } from "react";
import { packageShortCollection, type BatchJob, type ShortCollection } from "../api";
import { Notice } from "../components";

export function ShortCollections({jobId, jobs, bookends = false}: {jobId: string; jobs: BatchJob[]; bookends?: boolean}) {
  const defaultTitle = bookends ? "Voxframe Bookends" : "Voxframe Shorts";
  const [title, setTitle] = useState(defaultTitle), [selected, setSelected] = useState<string[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false), [result, setResult] = useState<ShortCollection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    generation.current++; setTitle(defaultTitle); setSelected([]); setNames({});
    setBusy(false); setResult(null); setError(null);
    return () => { generation.current++; };
  }, [jobId, defaultTitle]);
  const ordered = [...jobs].sort((a, b) => a.job.created_at.localeCompare(b.job.created_at));
  const ready = ordered.filter(item => item.job.state === "succeeded" && item.job.artifacts.includes("video"));
  const choices = selected.map(id => jobs.find(item => item.job.id === id));
  const valid = selected.length > 0 && choices.every(item => item?.job.state === "succeeded" &&
    item.job.artifacts.includes("video")) && title.trim() && selected.every(id => (names[id] ?? "").trim());
  function invalidate() { generation.current++; setResult(null); setError(null); }
  function toggle(item: BatchJob, checked: boolean) {
    invalidate();
    setSelected(ids => checked ? [...ids, item.job.id] : ids.filter(id => id !== item.job.id));
    setNames(current => ({...current, [item.job.id]: current[item.job.id] ?? item.job.audio_name.slice(0, 80)}));
  }
  function move(index: number, step: number) {
    const next = index + step;
    if (next < 0 || next >= selected.length) return;
    invalidate(); setSelected(ids => { const result = [...ids]; [result[index], result[next]] = [result[next], result[index]]; return result; });
  }
  async function build() {
    if (busy || !valid) return;
    const version = generation.current;
    setBusy(true); setError(null); setResult(null);
    try {
      const collection = await packageShortCollection(jobId, title.trim(), selected.map(id => ({job_id: id, title: names[id].trim()})));
      if (generation.current === version) setResult(collection);
    } catch (e) { if (generation.current === version) setError(e instanceof Error ? e.message : "Could not package the collection."); }
    finally { if (generation.current === version) setBusy(false); }
  }
  return <section className="short-collections" aria-label={bookends ? "Bookend collection" : "Shorts collection"}>
    <h4>One collection. Ready to take with you.</h4>
    <p>Select up to six finished exports, name them and set their order. One ZIP includes videos, available SRT/VTT subtitles, per-clip credits and a collection manifest.</p>
    <fieldset disabled={busy}>
      <label className="label">Collection name<input aria-label="Collection name" maxLength={80} value={title} onChange={e => { invalidate(); setTitle(e.target.value); }} /></label>
      <button className="btn" disabled={!ready.length} onClick={() => {
        invalidate(); const items = ready.slice(0, 6);
        setSelected(items.map(item => item.job.id));
        setNames(current => ({...current, ...Object.fromEntries(items.map(item => [item.job.id, current[item.job.id] ?? item.job.audio_name.slice(0, 80)]))}));
      }}>Select finished clips</button>
      <div className="collection-options">{ordered.map(item => <label className="check" key={item.job.id}>
        <input type="checkbox" aria-label={`Include ${item.job.audio_name}`} checked={selected.includes(item.job.id)}
          disabled={item.job.state !== "succeeded" || !item.job.artifacts.includes("video") ||
            (selected.length >= 6 && !selected.includes(item.job.id))} onChange={e => toggle(item, e.target.checked)} />
        <span>{item.job.audio_name} · {item.height}px · {item.job.state}</span>
      </label>)}</div>
      <ol className="story-blocks">{choices.map((item, i) => <li className="story-block" key={selected[i]}>
        <label className="label">Clip {i + 1} name<input aria-label={`Collection clip ${i + 1} name`} value={names[selected[i]] ?? ""} maxLength={80}
          onChange={e => { invalidate(); const value = e.target.value; setNames(names => ({...names, [selected[i]]: value})); }} /></label>
        <p className="hint">{item?.job.audio_name ?? "Export removed"}</p>
        <div className="story-actions"><button className="btn" disabled={i === 0} onClick={() => move(i, -1)} aria-label={`Move collection clip ${i + 1} earlier`}>Earlier</button>
          <button className="btn" disabled={i === choices.length - 1} onClick={() => move(i, 1)} aria-label={`Move collection clip ${i + 1} later`}>Later</button>
          <button className="btn" onClick={() => { invalidate(); setSelected(ids => ids.filter(id => id !== selected[i])); }}>Remove from collection</button></div>
      </li>)}</ol>
      <button className="btn btn-primary" disabled={!valid} onClick={() => void build()}>{busy ? "Packaging your collection…" : "Build collection"}</button>
    </fieldset>
    <p className="hint">Packages finished exports as they were when assembled. Render pending edits with Update video first. Building a collection keeps your projects intact.</p>
    {busy && <p role="status">Copying videos, subtitles and credits into your collection…</p>}
    {result && <div className="collection-ready"><p role="status">{result.clips} clips packaged · {(result.bytes / 1024 / 1024).toFixed(1)} MB</p>
      <a className="btn btn-primary" href={result.url} download={result.name}>Download collection ZIP</a>
      <p className="hint">{result.name} · This download is a snapshot of the finished exports.</p></div>}
    {error && <Notice tone="error">{error}</Notice>}
  </section>;
}
