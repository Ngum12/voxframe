import { useEffect, useRef, useState } from "react";
import { getStory, previewStory, saveStory, type StoryBlock, type StoryControls,
  type ShortPreview, type ScenePlan, type PlanEditResult } from "../api";
import { Notice } from "../components";

const roles: StoryBlock["role"][] = ["hook", "keypoint", "payoff", "ending"];
const names = { hook: "Hook", keypoint: "Key point", payoff: "Payoff", ending: "Ending" };

export function StoryComposer({ jobId, plan, onEdited }: {
  jobId: string; plan: ScenePlan; onEdited: (result: PlanEditResult) => void;
}) {
  const [data, setData] = useState<StoryControls | null>(null);
  const [blocks, setBlocks] = useState<StoryBlock[]>([]);
  const [candidate, setCandidate] = useState(0);
  const [vertical, setVertical] = useState(true);
  const [preview, setPreview] = useState<ShortPreview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const player = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    let live = true;
    generation.current++; setData(null); setPreview(null); setBusy(null); setError(null);
    getStory(jobId).then(result => {
      if (live) { setData(result); setBlocks(result.proposal); setCandidate(0); }
    }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; generation.current++; };
  }, [jobId, plan]);
  useEffect(() => {
    const video = player.current;
    return () => { video?.pause(); };
  }, [preview]);
  function change(next: StoryBlock[]) { setBlocks(next); setPreview(null); setError(null); }
  function patch(index: number, values: Partial<StoryBlock>) {
    change(blocks.map((block, i) => i === index ? { ...block, ...values } : block));
  }
  function move(index: number, delta: number) {
    const next = [...blocks];
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    change(next);
  }
  const valid = !!data?.words.length && !!blocks.length && blocks.every(b =>
    Number.isInteger(b.first_word) && Number.isInteger(b.last_word) && b.first_word >= 0 &&
    b.last_word >= b.first_word && b.last_word < data.words.length);
  const overlap = blocks.some((b, i) => blocks.slice(0, i).some(other =>
    b.first_word <= other.last_word && b.last_word >= other.first_word));
  async function act(action: "preview" | "save") {
    if (!data || !valid || overlap) return;
    const current = generation.current;
    setBusy(action); setError(null); player.current?.pause();
    try {
      if (action === "preview") {
        const result = await previewStory(jobId, data.revision, blocks, vertical);
        if (current === generation.current) setPreview(result);
      } else {
        const result = await saveStory(jobId, data.revision, blocks, vertical);
        if (current === generation.current) onEdited(result);
      }
    } catch (e) {
      if (current === generation.current) setError(e instanceof Error ? e.message : "The story could not be composed.");
    } finally { if (current === generation.current) setBusy(null); }
  }
  return <details className="story-composer" onToggle={e => { if (!e.currentTarget.open) player.current?.pause(); }}>
    <summary>Story Composer · build your sequence</summary>
    <section aria-label="Story Composer">
      <h3>Your recording. Your story.</h3>
      <p>Arrange a hook, key points and an ending using your own words. Build a 3–60 second story with up to eight passages.</p>
      {error && <Notice tone="error">{error}</Notice>}
      {!data && !error && <p role="status">Loading the transcript…</p>}
      {data && <>
        <p className="hint">{data.note}</p>
        {!data.words.length && <p>Transcribe this recording first to get word timings.</p>}
        {!!data.words.length && <>
          <ol className="story-blocks">
            {blocks.map((block, index) => {
              const words = data.words.slice(block.first_word, block.last_word + 1);
              const before = data.words.slice(Math.max(0, block.first_word - 8), block.first_word);
              const after = data.words.slice(block.last_word + 1, block.last_word + 9);
              return <li key={index} className="story-block">
                <fieldset disabled={!!busy}>
                  <legend>Passage {index + 1}</legend>
                  <div className="story-fields">
                    <label>Role {index + 1}<select aria-label={`Role ${index + 1}`} value={block.role} onChange={e => patch(index, {role: e.target.value as StoryBlock["role"]})}>
                      {roles.map(role => <option key={role} value={role}>{names[role]}</option>)}
                    </select></label>
                    <label>First word {index + 1}<input type="number" min={1} max={data.words.length} value={block.first_word + 1}
                      onChange={e => patch(index, {first_word: Number(e.target.value) - 1})} /></label>
                    <label>Last word {index + 1}<input type="number" min={1} max={data.words.length} value={block.last_word + 1}
                      onChange={e => patch(index, {last_word: Number(e.target.value) - 1})} /></label>
                  </div>
                  <p className="hint">Words {block.first_word + 1}–{block.last_word + 1}{words.length > 0 && ` · ${words[0].start.toFixed(1)}–${words[words.length - 1].end.toFixed(1)}s in the current edit`}</p>
                  <blockquote>{words.map(w => w.text).join(" ") || "Choose valid word boundaries."}</blockquote>
                  <details><summary>Review surrounding words</summary>
                    <p><strong>Before: </strong>{before.map(w => w.text).join(" ") || "Start of recording"}</p>
                    <p><strong>After: </strong>{after.map(w => w.text).join(" ") || "End of recording"}</p>
                    <p className="hint">Reordering can change meaning. Check references and incomplete sentences.</p>
                  </details>
                  <div className="story-actions">
                    <button type="button" disabled={index === 0} onClick={() => move(index, -1)} aria-label={`Move passage ${index + 1} up`}>Move up</button>
                    <button type="button" disabled={index === blocks.length - 1} onClick={() => move(index, 1)} aria-label={`Move passage ${index + 1} down`}>Move down</button>
                    <button type="button" onClick={() => change(blocks.filter((_, i) => i !== index))} aria-label={`Remove passage ${index + 1}`}>Remove</button>
                  </div>
                </fieldset>
              </li>;
            })}
          </ol>
          <fieldset disabled={!!busy}>
            <label>Add a transcript passage<select value={candidate} onChange={e => setCandidate(Number(e.target.value))}>
              {data.passages.map((passage, index) => <option key={index} value={index}>{index + 1}. Words {passage.first_word + 1}–{passage.last_word + 1}: {passage.text}</option>)}
            </select></label>
            <button type="button" disabled={blocks.length >= 8 || !data.passages[candidate]} onClick={() => {
              const passage = data.passages[candidate];
              change([...blocks, {role: "keypoint", first_word: passage.first_word, last_word: passage.last_word}]);
            }}>Add passage</button>
            <label className="check"><input type="checkbox" checked={vertical} onChange={e => { setVertical(e.target.checked); setPreview(null); }} />Vertical 9:16</label>
          </fieldset>
          {overlap && <p role="alert">Passages overlap. Trim their word boundaries or remove a duplicate.</p>}
          <div className="story-actions">
            <button type="button" disabled={!!busy || !valid || overlap} onClick={() => act("preview")}>{busy === "preview" ? "Rendering story…" : "Preview story sequence"}</button>
            <button type="button" className="primary" disabled={!!busy || !valid || overlap} onClick={() => act("save")}>{busy === "save" ? "Saving…" : "Use this sequence"}</button>
          </div>
          {busy && <p role="status">{busy === "preview" ? "Rendering the selected passages in order…" : "Saving the story sequence…"}</p>}
          {preview && <div className="story-preview"><video ref={player} controls preload="metadata" src={preview.url} aria-label="Story sequence preview" />
            <p>{preview.seconds.toFixed(1)} seconds · {preview.note}</p></div>}
          <p className="hint">Preview leaves your project untouched. Use this sequence saves one undoable edit; Update video renders it. Your original upload is kept. Undo restores the previous sequence.</p>
        </>}
      </>}
    </section>
  </details>;
}
