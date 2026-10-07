import { useEffect, useRef, useState } from "react";
import { listCreativePresets, getCompleteAuditions, previewSignatureKit, saveComplete,
  type CreativePreset, type ScenePlan, type PlanEditResult, type ShortPreview } from "../api";
import { Notice } from "../components";

export function SignatureKits({jobId, plan, onEdited}: {
  jobId: string; plan: ScenePlan; onEdited: (result: PlanEditResult) => void;
}) {
  const [kits, setKits] = useState<CreativePreset[]>([]), [selected, setSelected] = useState("");
  const [revision, setRevision] = useState("");
  const [preview, setPreview] = useState<(ShortPreview & {preview_id: string}) | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const generation = useRef(0), player = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const current = ++generation.current;
    setRevision(""); setPreview(null); setBusy(false); setError(null);
    Promise.all([listCreativePresets(), getCompleteAuditions(jobId)]).then(([data, controls]) => {
      if (generation.current === current) { setKits(data.presets); setRevision(controls.revision); }
    }).catch(e => { if (generation.current === current) setError(e.message); });
    return () => { generation.current++; };
  }, [jobId, plan]);
  useEffect(() => {
    const video = player.current;
    return () => { video?.pause(); };
  }, [preview, open]);
  const kit = kits.find(k => k.id === selected);
  const seconds = plan.total_frames / plan.fps, eligible = seconds >= 3 && seconds <= 60.001;
  async function act(apply: boolean) {
    if (!revision || !kit || busy || (apply && !preview)) return;
    const current = generation.current;
    setBusy(true); setError(null); player.current?.pause();
    try {
      if (apply && preview) {
        const result = await saveComplete(jobId, {revision, preview_id: preview.preview_id});
        if (generation.current === current) onEdited(result);
      } else {
        const result = await previewSignatureKit(jobId, revision, kit.id);
        if (generation.current === current) setPreview(result);
      }
    } catch (e) { if (generation.current === current) setError(e instanceof Error ? e.message : "Could not audition the kit."); }
    finally { if (generation.current === current) setBusy(false); }
  }
  return <details className="story-composer" open={open} onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>Signature style kits</summary>
    <p>Bring your signature to this story. Save kits from Export, then watch and hear them here.</p>
    {!eligible && <Notice>Choose a 3–60 second story in Shorts to preview a kit. Saved kits also work on new uploads.</Notice>}
    <fieldset disabled={busy || !revision || !eligible}>
      <label className="label">Signature kit<select aria-label="Signature kit" value={selected} onChange={e => {
        generation.current++; player.current?.pause(); setSelected(e.target.value); setPreview(null);
      }}><option value="">Choose your saved look</option>{kits.map(k => <option key={k.id} value={k.id}>{k.name}</option>)}</select></label>
      {kit && <p className="hint">{kit.caption_treatment?.animation ?? "Style default"} captions · {kit.audio_mix.music_arc} sound arc
        {kit.transition_treatment && ` · ${kit.transition_treatment.kind} transitions`}
        {kit.camera_move && ` · camera ${kit.camera_move.direction}`}
        {kit.beat_style && ` · ${kit.beat_style.look} text beats`}</p>}
      <p className="hint">Replaces caption styling and sound settings. Included visuals replace transitions and restyle existing beats and moving photos. Your words, cuts, media and held photos stay intact.</p>
      <div className="story-actions"><button className="btn" disabled={!kit} onClick={() => void act(false)}>{busy ? "Preparing…" : "Preview signature kit"}</button>
        <button className="btn btn-primary" disabled={!preview} onClick={() => void act(true)}>Apply previewed kit</button></div>
    </fieldset>
    {open && preview && <div className="story-preview"><video ref={player} controls preload="metadata" src={preview.url} aria-label="Signature kit preview" />
      <p className="hint">{preview.note} Apply saves this exact preview as one undoable edit. Update video to export.</p></div>}
    {error && <Notice tone="error">{error}</Notice>}
  </details>;
}
