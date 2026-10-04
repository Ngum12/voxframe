import { useEffect, useState } from "react";
import { getShorts, previewShort, saveShort, type ShortsControls, type ShortPreview,
  type ShortChoice, type ScenePlan, type PlanEditResult } from "../api";
import { Notice } from "../components";

const clock = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;

export function ShortsStudio({ jobId, plan, canListen, onEdited, onSeek }: {
  jobId: string; plan: ScenePlan; canListen: boolean;
  onEdited: (result: PlanEditResult) => void; onSeek: (seconds: number) => void;
}) {
  const [data, setData] = useState<ShortsControls | null>(null);
  const [first, setFirst] = useState(0), [last, setLast] = useState(0);
  const [vertical, setVertical] = useState(true), [search, setSearch] = useState("");
  const [busy, setBusy] = useState<"preview" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ShortPreview | null>(null);
  useEffect(() => {
    let live = true;
    setData(null); setPreview(null); setError(null); setSearch("");
    getShorts(jobId).then(d => {
      if (!live) return;
      setData(d);
      setFirst(d.suggestions[0]?.first_word ?? 0);
      setLast(d.suggestions[0]?.last_word ?? Math.max(0, d.words.length - 1));
    }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [jobId, plan]);
  useEffect(() => { setPreview(null); }, [first, last, vertical]);
  const valid = !!data?.words.length && last >= first;
  const selected = data?.words.slice(first, last + 1) ?? [];
  const candidates = data?.suggestions ?? [];
  const options = data?.words.filter(w => search ? w.text.toLocaleLowerCase().includes(search.toLocaleLowerCase())
    : Math.abs(w.index - first) < 12 || Math.abs(w.index - last) < 12).slice(0, 100) ?? [];
  for (const i of [first, last]) {
    const word = data?.words[i];
    if (word && !options.some(w => w.index === i)) options.push(word);
  }
  options.sort((a, b) => a.index - b.index);
  async function act(action: "preview" | "save") {
    if (!data) return;
    const choice: ShortChoice = { revision: data.revision, first_word: first, last_word: last, vertical };
    setBusy(action); setError(null);
    try {
      if (action === "preview") setPreview(await previewShort(jobId, choice));
      else onEdited(await saveShort(jobId, choice));
    } catch (e) { setError(e instanceof Error ? e.message : "The short could not be made."); }
    finally { setBusy(null); }
  }
  return <section className="shorts-studio" aria-label="Shorts producer">
    <div className="shorts-heading"><span className="shorts-eyebrow">SHORTS PRODUCER</span><h3>Find your opening.</h3>
      <p>Keep a passage worth watching. Give it a clear beginning and an ending that delivers.</p></div>
    <p className="hint">Suggested openings come from sentences in your transcript. Read the whole passage and check that the ending delivers.</p>
    {error && <Notice tone="error">{error}</Notice>}
    {!data && !error && <p role="status">Looking for passages…</p>}
    {data && !data.words.length && <p>Word timings are needed to make a short. Transcribe this recording first.</p>}
    {data && !!data.words.length && <>
      <div className="shorts-candidates" aria-label="Suggested passages">
        {!candidates.length && <p>No passage fits automatically. Choose words below for a 3–60 second cut.</p>}
        {candidates.map((c, i) => <article key={c.id} className="shorts-candidate" data-selected={first === c.first_word && last === c.last_word}>
          <button type="button" disabled={!!busy} aria-pressed={first === c.first_word && last === c.last_word}
            onClick={() => { setFirst(c.first_word); setLast(c.last_word); setSearch(""); }}>
            <span className="shorts-candidate-meta">OPTION {i + 1} <span>{c.seconds.toFixed(1)} s · {clock(c.start)}</span></span>
            <strong>“{c.opening}”</strong>
          </button>
          <p className="shorts-signals">{c.reasons.join(" · ")}</p>
          <details><summary>Read passage and ending</summary><p>{c.text}</p><p><b>Ending:</b> {c.ending}</p></details>
        </article>)}
      </div>
      <fieldset disabled={!!busy} className="shorts-boundaries"><legend>Make the cut yours</legend>
        <label className="label">Find a word<input aria-label="Search transcript words" type="search" value={search} placeholder="Search a word in the transcript" onChange={e => setSearch(e.target.value)} /></label>
        <p className="hint">Search finds up to 100 word positions. Times refer to your current edit.</p>
        <label className="label">First word<select aria-label="First word" value={first} onChange={e => setFirst(Number(e.target.value))}>
          {options.map(w => <option key={w.index} value={w.index}>{clock(w.start)} · {w.text}</option>)}</select></label>
        <label className="label">Last word<select aria-label="Last word" value={last} onChange={e => setLast(Number(e.target.value))}>
          {options.map(w => <option key={w.index} value={w.index}>{clock(w.end)} · {w.text}</option>)}</select></label>
        <label><input type="checkbox" checked={vertical} onChange={e => setVertical(e.target.checked)} /> Vertical 9:16</label>
      </fieldset>
      {valid && <div className="shorts-selection" aria-label="Selected passage">
        <p><b>Current edit:</b> {clock(selected[0].start)} → {clock(selected[selected.length - 1].end)}</p>
        <details><summary>Review selected transcript</summary><p>{selected.map(w => w.text).join(" ")}</p></details>
        <button type="button" className="link-button" disabled={!canListen || !!busy} onClick={() => onSeek(Math.max(0, selected[0].start - .12))}>Watch in current video</button>
      </div>}
      {!valid && <Notice tone="error">Choose a last word after the first word.</Notice>}
      <div className="caption-save-actions"><button className="btn" type="button" disabled={!!busy || !valid} onClick={() => act("preview")}>{busy === "preview" ? "Rendering preview…" : "Render short preview"}</button>
        <button className="btn btn-primary" type="button" disabled={!!busy || !valid} onClick={() => act("save")}>{busy === "save" ? "Saving…" : "Use this short"}</button></div>
      {preview && <div className="shorts-preview">
        <p role="status">Rendered draft · {preview.seconds.toFixed(2)} s</p>
        <video controls preload="metadata" src={preview.url} aria-label="Rendered short preview" />
        <p className="hint">{preview.note}</p>
        <details><summary>Inspect source ranges</summary>{preview.source_ranges.map((r, i) => <p key={i}>
          Audio {clock(r.audio_start)} → {clock(r.audio_start + r.seconds)}{r.footage_start !== null && <> · Footage {clock(r.footage_start)} → {clock(r.footage_start + r.seconds)}</>}</p>)}</details>
      </div>}
      <p className="hint">Preview leaves your plan intact. Use this short saves a 3–60 second cut in this project; title/chapter cards are omitted. Undo restores your full edit. Update video for final export.</p>
    </>}
  </section>;
}
