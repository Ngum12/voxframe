import { useEffect, useState } from "react";
import { getDirection, directVideo, previewDirection, saveVisual, previewVisual,
  type DirectionControls, type VisualBeat, type ShortPreview, type ScenePlan, type PlanEditResult } from "../api";
import { CompleteAuditions } from "./CompleteAuditions";
import { VisualPlacementStudio } from "./VisualPlacementStudio";
import { Notice } from "../components";

const descriptions = {
  authority: "Measured cuts, selective emphasis and clean text.",
  energy: "Faster beats, stronger punch-ins and bold text.",
  cinema: "Longer shots, restrained framing and warm accents.",
};
export function DirectorStudio({ jobId, plan, sceneIndex, onEdited }: {
  jobId: string; plan: ScenePlan; sceneIndex: number; onEdited: (result: PlanEditResult) => void;
}) {
  const [data, setData] = useState<DirectionControls | null>(null);
  const [look, setLook] = useState<VisualBeat["look"]>("energy");
  const [match, setMatch] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null), [preview, setPreview] = useState<ShortPreview | null>(null);
  const scene = plan.scenes[sceneIndex];
  const [beat, setBeat] = useState<VisualBeat>(scene.visual_beat ?? {
    text: "", kind: "keypoint", look: "energy", position: "auto", zoom: 1, source: "user",
  });
  useEffect(() => {
    let live = true;
    setData(null); setPreview(null);
    setBeat(scene.visual_beat ?? {
      text: "", kind: "keypoint", look: "energy", position: "auto", zoom: 1, source: "user",
    });
    getDirection(jobId).then(d => { if (live) setData(d); }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [jobId, plan]);
  useEffect(() => { setPreview(null); }, [look, match, beat]);
  const duration = plan.total_frames / plan.fps, eligible = duration >= 3 && duration <= 60.001;
  async function act(action: "preview" | "apply" | "beat-preview" | "beat-save" | "clear") {
    if (!data) return;
    setBusy(true); setError(null);
    try {
      const choice = { revision: data.revision, look, match_captions: match };
      if (action === "preview") setPreview(await previewDirection(jobId, choice));
      else if (action === "apply") onEdited(await directVideo(jobId, choice));
      else if (action === "beat-preview") setPreview(await previewVisual(jobId, sceneIndex, data.revision, beat));
      else onEdited(await saveVisual(jobId, sceneIndex, data.revision, action === "clear" ? null : beat));
    } catch (e) { setError(e instanceof Error ? e.message : "The direction could not be saved."); }
    finally { setBusy(false); }
  }
  return <section className="shorts-studio" aria-label="Visual director">
    <div className="shorts-heading"><span className="shorts-eyebrow">VISUAL DIRECTOR</span>
      <h3>Give every beat a purpose.</h3><p>Your opening. Your emphasis. Your final word.</p></div>
    <p className="hint">The director cuts at word boundaries, uses your existing image matches for cutaways, and quotes your transcript for text beats. Speaker shots return at the beginning and end unless you chose otherwise.</p>
    <CompleteAuditions jobId={jobId} plan={plan} onEdited={onEdited} />
    <VisualPlacementStudio jobId={jobId} plan={plan} onEdited={onEdited} />
    {error && <Notice tone="error">{error}</Notice>}
    {!eligible && <Notice>Choose a 3–60 second passage in Shorts to build a beat sequence.</Notice>}
    <fieldset disabled={busy || !data || !eligible} className="shorts-boundaries"><legend>Choose the direction</legend>
      <div className="shorts-candidates">{(["authority", "energy", "cinema"] as const).map(id =>
        <article key={id} className="shorts-candidate" data-selected={look === id}>
          <button type="button" aria-pressed={look === id} onClick={() => setLook(id)}>
            <strong>{data?.looks[id]?.label ?? id}</strong><span>{descriptions[id]}</span>
          </button>
        </article>)}</div>
      <label><input type="checkbox" checked={match} onChange={e => setMatch(e.target.checked)} /> Match the captions to this look</label>
      <div className="caption-save-actions"><button className="btn" onClick={() => act("preview")}>Preview direction</button>
        <button className="btn btn-primary" onClick={() => act("apply")}>Apply direction</button></div>
    </fieldset>
    <details open={!!scene.visual_beat} className="shorts-boundaries"><summary>Edit selected beat · Scene {sceneIndex + 1}</summary>
      {scene.card_kind ? <p>Select a spoken scene in the timeline.</p> : <fieldset disabled={busy || !data}>
        <p>{(scene.start_frame / plan.fps).toFixed(1)}–{(scene.end_frame / plan.fps).toFixed(1)} s · {scene.shot === "picture" ? "Cutaway" : "Speaker"}{scene.visual_beat?.source === "user" ? " · Your pinned beat" : ""}</p>
        <label className="label">Text beat<input aria-label="Text beat" maxLength={96} value={beat.text} onChange={e => setBeat({ ...beat, text: e.target.value })} /></label>
        <label className="label">Beat role<select aria-label="Beat role" value={beat.kind} onChange={e => setBeat({ ...beat, kind: e.target.value as VisualBeat["kind"] })}>
          <option value="opening">Opening</option><option value="keypoint">Key point</option><option value="number">In focus</option><option value="closing">Closing</option></select></label>
        <label className="label">Text look<select aria-label="Text look" value={beat.look} onChange={e => setBeat({ ...beat, look: e.target.value as VisualBeat["look"] })}>
          <option value="authority">Clean authority</option><option value="energy">High energy</option><option value="cinema">Cinematic story</option></select></label>
        <label className="label">Text placement<select aria-label="Text placement" value={beat.position} onChange={e => setBeat({ ...beat, position: e.target.value as VisualBeat["position"] })}>
          <option value="auto">Auto</option><option value="top">Top</option><option value="center">Center</option></select></label>
        <p className="hint">Auto avoids captions and tracked faces; text may be hidden if there is no room. Review manual placement in the preview.</p>
        <label className="label">Speaker zoom · {beat.zoom.toFixed(2)}×<input aria-label="Speaker zoom" type="range" min={1} max={1.25} step={.01} value={beat.zoom} onChange={e => setBeat({ ...beat, zoom: Number(e.target.value) })} /></label>
        <p className="hint">Zoom applies to speaker footage. Choose Speaker or Picture and change the cutaway image in Scenes.</p>
        <div className="caption-save-actions"><button className="btn" onClick={() => act("beat-preview")}>Preview beat edit</button>
          <button className="btn btn-primary" onClick={() => act("beat-save")}>Save beat</button>
          {scene.visual_beat && <button className="btn" onClick={() => act("clear")}>Clear beat</button>}</div>
      </fieldset>}
    </details>
    {busy && <p role="status">Preparing your direction…</p>}
    {preview && <div className="shorts-preview"><p role="status">Rendered draft · {preview.seconds.toFixed(2)} s</p>
      <video controls preload="metadata" src={preview.url} aria-label="Rendered director preview" /><p className="hint">{preview.note}</p></div>}
    <p className="hint">Preview leaves your edit intact. Apply direction saves the sequence; Save beat pins your changes for future direction passes. Undo restores the previous edit. Update video to export.</p>
  </section>;
}
