import { useEffect, useRef, useState } from "react";
import { getPlacement, previewPlacement, savePlacement, thumbnailUrl,
  type PlacementControls, type VisualPlacement, type VisualBeat, type ScenePlan,
  type PlanEditResult, type ShortPreview, type Storyboard } from "../api";
import { Notice } from "../components";

const freshBeat = (): VisualBeat => ({ text: "", kind: "keypoint", look: "energy",
  position: "auto", zoom: 1, source: "user" });

export function VisualPlacementStudio({ jobId, plan, onEdited }: {
  jobId: string; plan: ScenePlan; onEdited: (result: PlanEditResult) => void;
}) {
  const [data, setData] = useState<PlacementControls | null>(null);
  const [edits, setEdits] = useState<VisualPlacement[]>([]);
  const [preview, setPreview] = useState<(ShortPreview & {storyboard: Storyboard}) | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0), player = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    let live = true;
    generation.current++; setData(null); setEdits([]); setPreview(null); setBusy(null); setError(null);
    getPlacement(jobId).then(result => { if (live) setData(result); })
      .catch(e => { if (live) setError(e.message); });
    return () => { live = false; generation.current++; };
  }, [jobId, plan]);
  useEffect(() => { const video = player.current; return () => { video?.pause(); }; }, [preview]);
  const eligible = plan.total_frames / plan.fps >= 3 && plan.total_frames / plan.fps <= 60 + 1e-7;
  function change(next: VisualPlacement[]) { player.current?.pause(); setEdits(next); setPreview(null); setError(null); }
  function patch(index: number, values: Partial<VisualPlacement>) {
    change(edits.map((edit, i) => i === index ? { ...edit, ...values } : edit));
  }
  function beat(index: number, values: Partial<VisualBeat>) {
    patch(index, {beat: {...(edits[index].beat ?? freshBeat()), ...values}});
  }
  const overlap = edits.some((edit, i) => edits.slice(0, i).some(other =>
    edit.first_word <= other.last_word && edit.last_word >= other.first_word));
  const valid = !!data && !!edits.length && edits.every(edit =>
    edit.last_word >= edit.first_word && (edit.shot !== "keep" || !!edit.beat) &&
    (edit.shot !== "picture" || edit.asset_scene !== null));
  async function act(action: "preview" | "save") {
    if (!data || !valid || overlap) return;
    const current = generation.current;
    setBusy(action); setError(null); player.current?.pause();
    try {
      if (action === "preview") {
        const rendered = await previewPlacement(jobId, data.revision, edits);
        if (current === generation.current) setPreview(rendered);
      } else {
        const result = await savePlacement(jobId, data.revision, edits);
        if (current === generation.current) onEdited(result);
      }
    } catch (e) { if (current === generation.current) setError(e instanceof Error ? e.message : "The placements could not be saved."); }
    finally { if (current === generation.current) setBusy(null); }
  }
  return <details className="story-composer visual-placement" onToggle={e => { if (!e.currentTarget.open) player.current?.pause(); }}>
    <summary>Place visuals · choose when they appear</summary>
    <section aria-label="Visual placement">
      <h3>Make the picture follow the words.</h3>
      <p>Choose a spoken span, bring in a project visual or return to the speaker, and add a text beat exactly there.</p>
      <p className="hint">The voice keeps playing. These placements keep your story's duration and caption timing. Add or replace project visuals in Scenes first.</p>
      {error && <Notice tone="error">{error}</Notice>}
      {!eligible && <Notice>Compose a 3–60 second story in Shorts first.</Notice>}
      {!data && !error && <p role="status">Loading story visuals…</p>}
      {data && eligible && <>
        {!data.words.length && <p>Word timings are needed. Transcribe this recording first.</p>}
        <details><summary>Review current shot sequence</summary><ol>{data.storyboard.beats.map(b => <li key={b.index}>
          {b.start.toFixed(1)}–{b.end.toFixed(1)}s · {b.shot} · {b.quote}{b.text && ` · Text: ${b.text}`}
        </li>)}</ol></details>
        <ol className="story-blocks">{edits.map((edit, index) => {
          const visual = data.visuals.find(v => v.scene === edit.asset_scene);
          return <li className="story-block" key={index}><fieldset disabled={!!busy}>
            <legend>Placement {index + 1}</legend>
            <div className="story-fields">
              <label>Shot<select aria-label={`Shot ${index + 1}`} value={edit.shot} onChange={e => {
                const shot = e.target.value as VisualPlacement["shot"];
                patch(index, {shot, asset_scene: shot === "picture" ? data.visuals[0]?.scene ?? null : null});
              }}><option value="keep">Keep current shots</option><option value="speaker" disabled={!data.has_speaker}>Speaker</option>
                <option value="picture" disabled={!data.visuals.length}>Supporting visual</option></select></label>
              <label>From word<select aria-label={`Placement ${index + 1} first word`} value={edit.first_word} onChange={e => patch(index, {first_word: Number(e.target.value)})}>
                {data.words.map(w => <option key={w.index} value={w.index}>{w.index + 1}. {w.text} · {w.start.toFixed(1)}s</option>)}</select></label>
              <label>Through word<select aria-label={`Placement ${index + 1} last word`} value={edit.last_word} onChange={e => patch(index, {last_word: Number(e.target.value)})}>
                {data.words.map(w => <option key={w.index} value={w.index}>{w.index + 1}. {w.text} · {w.end.toFixed(1)}s</option>)}</select></label>
            </div>
            <blockquote>{data.words.slice(edit.first_word, edit.last_word + 1).map(w => w.text).join(" ") || "Choose words in order."}</blockquote>
            {edit.shot === "picture" && <>
              <label>Project visual<select aria-label={`Project visual ${index + 1}`} value={edit.asset_scene ?? ""} onChange={e => patch(index, {asset_scene: Number(e.target.value)})}>
                {data.visuals.map(v => <option key={v.scene} value={v.scene}>Scene {v.scene + 1} · {v.quote || v.id}</option>)}</select></label>
              {visual && <figure className="placement-visual"><img src={`${thumbnailUrl(jobId, visual.scene)}?asset_only=true`} alt={`Supporting visual from scene ${visual.scene + 1}`} />
                <figcaption>{visual.credit || "Project visual"}</figcaption></figure>}
            </>}
            <label className="check"><input type="checkbox" aria-label={`Text treatment ${index + 1}`} checked={!!edit.beat}
              onChange={e => patch(index, {beat: e.target.checked ? freshBeat() : null})} />Set a text treatment for this span</label>
            {edit.beat && <>
              <label>Text<input aria-label={`Placement text ${index + 1}`} maxLength={96} value={edit.beat.text} onChange={e => beat(index, {text: e.target.value})} /></label>
              <p className="hint">Leave text empty to clear text within this span. Captions continue normally.</p>
              <div className="story-fields">
                <label>Look<select aria-label={`Placement look ${index + 1}`} value={edit.beat.look} onChange={e => beat(index, {look: e.target.value as VisualBeat["look"]})}>
                  <option value="authority">Clean authority</option><option value="energy">High energy</option><option value="cinema">Cinematic story</option></select></label>
                <label>Position<select aria-label={`Placement position ${index + 1}`} value={edit.beat.position} onChange={e => beat(index, {position: e.target.value as VisualBeat["position"]})}>
                  <option value="auto">Auto</option><option value="top">Top</option><option value="center">Center</option></select></label>
                <label>Role<select aria-label={`Placement role ${index + 1}`} value={edit.beat.kind} onChange={e => beat(index, {kind: e.target.value as VisualBeat["kind"]})}>
                  <option value="opening">Opening</option><option value="keypoint">Key point</option><option value="number">In focus</option><option value="closing">Closing</option></select></label>
              </div>
            </>}
            <button type="button" onClick={() => change(edits.filter((_, i) => i !== index))} aria-label={`Remove placement ${index + 1}`}>Remove placement</button>
          </fieldset></li>;
        })}</ol>
        <button type="button" disabled={!!busy || edits.length >= 12 || !data.words.length} onClick={() => change([...edits,
          {first_word: 0, last_word: Math.min(3, data.words.length - 1), shot: "keep", asset_scene: null, beat: null}])}>Add visual placement</button>
        {overlap && <p role="alert">Placements overlap. Adjust their word boundaries.</p>}
        <div className="story-actions">
          <button type="button" disabled={!!busy || !valid || overlap} onClick={() => act("preview")}>Preview visual placements</button>
          <button type="button" className="btn btn-primary" disabled={!!busy || !valid || overlap} onClick={() => act("save")}>Use visual placements</button>
        </div>
        {busy && <p role="status">{busy === "preview" ? "Rendering the placed visuals…" : "Saving placements…"}</p>}
        {preview && <div className="story-preview"><video ref={player} controls preload="metadata" src={preview.url} aria-label="Visual placement preview" />
          <p>{preview.seconds.toFixed(1)} seconds · {preview.note}</p>
          <details><summary>Review rendered shot sequence</summary><ol>{preview.storyboard.beats.map(b => <li key={b.index}>
            {b.start.toFixed(1)}–{b.end.toFixed(1)}s · {b.shot} · {b.quote}{b.text && ` · Text: ${b.text}`}
          </li>)}</ol></details></div>}
        <p className="hint">Preview does not save. Use visual placements pins these choices as one undoable edit. Update video renders the result. Speaker zoom and text treatments can be refined in Edit selected beat.</p>
      </>}
    </section>
  </details>;
}
