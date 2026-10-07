import { useEffect, useRef, useState } from "react";
import { getShorts, getStoryboard, previewShort, saveShort, type ShortsControls, type ShortPreview,
  type ShortChoice, type ScenePlan, type PlanEditResult, type Storyboard, type VisualBeat } from "../api";
import { StoryComparison, type StoryVariant } from "./StoryComparison";
import { StoryComposer } from "./StoryComposer";
import { Notice } from "../components";

const clock = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const looks = [
  { id: "authority", name: "Clean authority", description: "Measured cuts and selective emphasis." },
  { id: "energy", name: "High energy", description: "Faster beats, punch-ins and bold text." },
  { id: "cinema", name: "Cinematic story", description: "Longer shots and restrained framing." },
] as const;
const shotNames = { speaker: "Speaker", picture: "Supporting visual", background: "Background" };

export function ShortsStudio({ jobId, plan, canListen, onEdited, onSeek }: {
  jobId: string; plan: ScenePlan; canListen: boolean;
  onEdited: (result: PlanEditResult) => void; onSeek: (seconds: number) => void;
}) {
  const generation = useRef(0);
  const [comparing, setComparing] = useState(false);
  const [data, setData] = useState<ShortsControls | null>(null);
  const [first, setFirst] = useState(0), [last, setLast] = useState(0);
  const [vertical, setVertical] = useState(true), [search, setSearch] = useState("");
  const [busy, setBusy] = useState<"preview" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [look, setLook] = useState<VisualBeat["look"] | null>(null);
  const [match, setMatch] = useState(false);
  const [previews, setPreviews] = useState<Record<string, ShortPreview>>({});
  const [board, setBoard] = useState<Storyboard | null>(null);
  const [expandedBoard, setExpandedBoard] = useState(false);
  const [boardError, setBoardError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    generation.current++; setBusy(null); setComparing(false);
    setData(null); setPreviews({}); setError(null); setSearch(""); setBoard(null);
    getShorts(jobId).then(d => {
      if (!live) return;
      setData(d);
      setFirst(d.suggestions[0]?.first_word ?? 0);
      setLast(d.suggestions[0]?.last_word ?? Math.max(0, d.words.length - 1));
    }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; generation.current++; };
  }, [jobId, plan]);
  const valid = !!data?.words.length && last >= first;
  const choice: ShortChoice | null = data ? { revision: data.revision, first_word: first,
    last_word: last, vertical, look, match_captions: !!look && match } : null;
  const choiceKey = JSON.stringify(choice);
  const preview = previews[choiceKey];
  const variants: StoryVariant[] = Object.entries(previews).flatMap(([key, rendered]) => {
    const candidate: ShortChoice = JSON.parse(key);
    if (!choice || candidate.revision !== choice.revision || candidate.first_word !== first || candidate.last_word !== last || candidate.vertical !== vertical) return [];
    const name = looks.find(option => option.id === candidate.look)?.name ?? "Current edit";
    return [{ key, choice: candidate, preview: rendered, label: `${name} · ${candidate.match_captions ? "matched captions" : "current captions"}` }];
  });
  useEffect(() => {
    let live = true;
    setBoard(null); setBoardError(null); setExpandedBoard(false);
    if (choice && valid) getStoryboard(jobId, choice).then(result => {
      if (live) setBoard(result);
    }).catch(e => { if (live) setBoardError(e.message); });
    return () => { live = false; };
  }, [jobId, choiceKey, valid]);
  const selected = data?.words.slice(first, last + 1) ?? [];
  const boardBeats = board && !expandedBoard && board.beats.length > 6
    ? [...board.beats.slice(0, 4), ...board.beats.slice(-2)] : board?.beats ?? [];
  const candidates = data?.suggestions ?? [];
  const options = data?.words.filter(w => search ? w.text.toLocaleLowerCase().includes(search.toLocaleLowerCase())
    : Math.abs(w.index - first) < 12 || Math.abs(w.index - last) < 12).slice(0, 100) ?? [];
  for (const i of [first, last]) {
    const word = data?.words[i];
    if (word && !options.some(w => w.index === i)) options.push(word);
  }
  options.sort((a, b) => a.index - b.index);
  async function act(action: "preview" | "save", chosen = choice) {
    if (!chosen) return;
    const current = generation.current;
    const key = JSON.stringify(chosen);
    setBusy(action); setError(null);
    try {
      if (action === "preview") {
        const rendered = await previewShort(jobId, chosen);
        if (current === generation.current) setPreviews(previews => ({ ...previews, [key]: rendered }));
      }
      else {
        const result = await saveShort(jobId, chosen);
        if (current === generation.current) onEdited(result);
      }
    } catch (e) { if (current === generation.current) setError(e instanceof Error ? e.message : "The short could not be made."); }
    finally { if (current === generation.current) setBusy(null); }
  }
  return <section className="shorts-studio" aria-label="Shorts producer">
    <div className="shorts-heading"><span className="shorts-eyebrow">SHORTS PRODUCER</span><h3>Find your opening.</h3>
      <p>Keep a passage worth watching. Give it a clear beginning and an ending that delivers.</p></div>
    <p className="hint">Audition a complete story before you commit. Quoted openings, complete passages and context help you choose.</p>
    <StoryComposer jobId={jobId} plan={plan} onEdited={onEdited} />
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
          <p className="story-hook"><b>{c.hook_type}</b> · Opening {c.opening_seconds.toFixed(1)} s</p>
          <p className="story-payoff"><b>Where it lands:</b> {c.payoff}</p>
          {c.warnings.length > 0 && <ul className="story-warnings">{c.warnings.map(w => <li key={w}>{w}</li>)}</ul>}
          <details><summary>Read passage and ending</summary><p>{c.text}</p><p><b>Ending:</b> {c.ending}</p>
            {c.context_before && <p><b>Just before:</b> {c.context_before}</p>}
            {c.context_after && <p><b>Just after:</b> {c.context_after}</p>}
          </details>
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
      <fieldset disabled={!!busy} className="story-directions"><legend>Audition a creative direction</legend>
        <p className="hint">The same passage, three ways to tell it. Preview each look and switch back to compare. Your edit stays intact until you choose.</p>
        <button className="btn btn-quiet" type="button" aria-pressed={look === null} onClick={() => setLook(null)}>Keep current edit</button>
        <div className="story-look-options">{looks.map(option => <button type="button" key={option.id}
          className="story-look" aria-pressed={look === option.id} onClick={() => setLook(option.id)}>
          <strong>{option.name}</strong><span>{option.description}</span>
        </button>)}</div>
        <label><input type="checkbox" disabled={!look || !!busy} checked={!!look && match}
          onChange={e => setMatch(e.target.checked)} /> Match captions to the direction</label>
      </fieldset>
      {boardError && <Notice>{boardError}</Notice>}
      {valid && !board && !boardError && <p role="status">Preparing the story…</p>}
      {board && <section className="story-board" aria-label="Planned story">
        <h4>The edit, beat by beat <span>{board.seconds.toFixed(1)} s</span></h4>
        {look && !board.has_speaker && <p className="hint">This project uses visuals with your voice. Upload a recording with video to direct speaker shots.</p>}
        <p className="hint">{board.beats.length} beats · {board.beats.filter(b => b.shot === "speaker").length} speaker shots · {board.beats.filter(b => b.shot === "picture").length} supporting visuals</p>
        <ol>{boardBeats.map(beat => <li key={beat.index} data-shot={beat.shot}>
          <div className="story-beat-meta"><time>{clock(beat.start)}–{clock(beat.end)}</time>
            <strong>{shotNames[beat.shot]}</strong><span>{beat.role === "passage" ? "" : beat.role}</span>
            {beat.zoom > 1 && <span>{beat.zoom.toFixed(2)}× framing</span>}</div>
          <p>{beat.text || beat.quote || (beat.index === 0 ? "Lead-in" : "Uncaptioned beat")}</p>
          <details><summary>Why this shot?</summary><p>{beat.reason}</p><p>Recording: {clock(beat.audio_start)}{beat.footage_start !== null && <> · Picture: {clock(beat.footage_start)}</>}</p></details>
        </li>)}</ol>
        {board.beats.length > 6 && <button type="button" className="link-button"
          onClick={() => setExpandedBoard(!expandedBoard)}>{expandedBoard ? "Show opening and ending" : `Show all ${board.beats.length} beats`}</button>}
        <p className="hint">{board.note}</p>
      </section>}
      <div className="caption-save-actions"><button className="btn" type="button" disabled={!!busy || !valid} onClick={() => act("preview")}>{busy === "preview" ? "Rendering preview…" : "Render short preview"}</button>
        <button className="btn btn-primary" type="button" disabled={!!busy || !valid} onClick={() => act("save")}>{busy === "save" ? "Saving…" : "Use this short"}</button></div>
      {preview && <div className="shorts-preview">
        <p role="status">Rendered draft · {preview.seconds.toFixed(2)} s</p>
        <video controls preload="metadata" src={preview.url} aria-label="Rendered short preview" />
        <p className="hint">{preview.note}</p>
        <details><summary>Inspect source ranges</summary>{preview.source_ranges.map((r, i) => <p key={i}>
          Audio {clock(r.audio_start)} → {clock(r.audio_start + r.seconds)}{r.footage_start !== null && <> · Footage {clock(r.footage_start)} → {clock(r.footage_start + r.seconds)}</>}</p>)}</details>
      </div>}
      {variants.length >= 2 && <>
        <button className="btn" type="button" disabled={!!busy} onClick={() => setComparing(!comparing)}>{comparing ? "Close comparison" : "Compare rendered edits"}</button>
        {comparing && <StoryComparison variants={variants} busy={!!busy} onChoose={chosen => void act("save", chosen)} />}
      </>}
      <p className="hint">Preview leaves your plan intact. Use this short saves a 3–60 second cut in this project; title/chapter cards are omitted. Undo restores your full edit. Update video for final export.</p>
    </>}
  </section>;
}
