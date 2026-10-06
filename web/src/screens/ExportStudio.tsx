import { useEffect, useState } from "react";
import { getShortExport, saveShortExport, previewShortExport, type ExportControls,
  type ShortExport, type ShortPreview, type ScenePlan, type PlanEditResult } from "../api";
import { PresetSaver } from "./CreativePresets";
import { FinishReview } from "./FinishReview";
import { type FinishAction } from "../api";
import { Notice } from "../components";

export function ExportStudio({ jobId, plan, onEdited, pending, onReview }: {
  jobId: string; plan: ScenePlan; onEdited: (result: PlanEditResult) => void; pending: number;
  onReview: (scene: number | null, action: FinishAction) => void;
}) {
  const [data, setData] = useState<ExportControls | null>(null);
  const [settings, setSettings] = useState<ShortExport | null>(null);
  const [busy, setBusy] = useState(false), [guides, setGuides] = useState(true);
  const [error, setError] = useState<string | null>(null), [preview, setPreview] = useState<ShortPreview | null>(null);
  useEffect(() => {
    let live = true;
    setData(null); setPreview(null); setError(null);
    getShortExport(jobId).then(d => {
      if (!live) return;
      setData(d); setSettings(d.settings ?? d.presets.youtube.export);
    }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [jobId, plan]);
  useEffect(() => { setPreview(null); }, [settings]);
  const eligible = plan.total_frames / plan.fps >= 3 && plan.total_frames / plan.fps <= 60.001;
  async function act(action: "preview" | "save" | "clear") {
    if (!data || !settings) return;
    setBusy(true); setError(null);
    try {
      if (action === "preview") setPreview(await previewShortExport(jobId, data.revision, settings));
      else onEdited(await saveShortExport(jobId, data.revision, action === "clear" ? null : settings));
    } catch (e) { setError(e instanceof Error ? e.message : "The export settings could not be saved."); }
    finally { setBusy(false); }
  }
  const beats = plan.scenes.filter(s => !s.card_kind && s.visual_beat);
  const images = plan.scenes.filter(s => !s.card_kind && s.shot === "picture" && s.asset);
  return <section className="shorts-studio" aria-label="Shorts export">
    <div className="shorts-heading"><span className="shorts-eyebrow">THE FINAL FRAME</span>
      <h3>Ready for your audience.</h3><p>Keep the words clear. Make the ending count.</p></div>
    <PresetSaver jobId={jobId} plan={plan} revision={data?.revision ?? null} />
    <FinishReview jobId={jobId} plan={plan} pending={pending} onReview={onReview} />
    {error && <Notice tone="error">{error}</Notice>}
    {!eligible && <Notice>Choose a 3–60 second passage in Shorts before choosing an export preset.</Notice>}
    {!settings && !error && <p role="status">Loading export looks…</p>}
    {settings && data && <>
      <fieldset className="shorts-boundaries" disabled={busy || !eligible}><legend>Where will it go?</legend>
        <label className="label">Platform<select aria-label="Export platform" value={settings.platform}
          onChange={e => setSettings(data.presets[e.target.value as ShortExport["platform"]].export)}>
          {Object.entries(data.presets).map(([id, preset]) => <option key={id} value={id}>{preset.label}</option>)}
        </select></label>
        <label className="label">Picture size<select aria-label="Export picture size" value={settings.height}
          onChange={e => setSettings({ ...settings, height: Number(e.target.value) as ShortExport["height"] })}>
          <option value={1920}>1080 × 1920 · Full HD</option><option value={1280}>720 × 1280 · Smaller file</option>
        </select></label>
        <label><input type="checkbox" checked={settings.progress} onChange={e => setSettings({ ...settings, progress: e.target.checked })} /> Timed progress bar</label>
        <label className="label">Progress accent<input aria-label="Progress accent" type="color" value={settings.accent}
          onChange={e => setSettings({ ...settings, accent: e.target.value })} /></label>
        <details><summary>Adjust text guides</summary>
          <p className="hint">These reserve space for platform controls. Captions stay centered within conservative side margins; text beats use the available area. Review on your device.</p>
          {(["top", "bottom", "left", "right"] as const).map(edge => <label className="label" key={edge}>
            {edge[0].toUpperCase() + edge.slice(1)} · {Math.round(settings.safe_area[edge] * 100)}%
            <input aria-label={`${edge} safe margin`} type="range" min={edge === "bottom" ? 8 : 4}
              max={edge === "bottom" ? 35 : 25} step={1} value={Math.round(settings.safe_area[edge] * 100)}
              onChange={e => setSettings({ ...settings, safe_area: { ...settings.safe_area, [edge]: Number(e.target.value) / 100 } })} />
          </label>)}
        </details>
        <div className="export-receipt" aria-label="Export summary">
          <strong>{data.presets[settings.platform].label}</strong>
          <span>{Math.round(settings.height * 9 / 16)} × {settings.height} · 9:16 · {(plan.total_frames / plan.fps).toFixed(2)} s</span>
          <span>{settings.platform === "whatsapp" ? "−15 LUFS · −1.5 dBTP" : "−14 LUFS · −1 dBTP"} · Voice and music levels retained</span>
          <span>{beats.length} directed beats · {images.length} matched cutaways</span>
        </div>
        <div className="caption-save-actions"><button className="btn" onClick={() => act("preview")}>Preview export look</button>
          <button className="btn btn-primary" onClick={() => act("save")}>Save export settings</button></div>
      </fieldset>
      {plan.short_export && <button className="link-button" disabled={busy} onClick={() => act("clear")}>Use project output settings</button>}
      {busy && <p role="status">Preparing your export look…</p>}
      {preview && <div className="shorts-preview">
        <label><input type="checkbox" checked={guides} onChange={e => setGuides(e.target.checked)} /> Show text guides</label>
        <div className="export-player"><video controls preload="metadata" src={preview.url} aria-label="Rendered export preview" />
          {guides && <div className="export-guide" aria-label="Text safe area" style={{
            top: `${settings.safe_area.top * 100}%`, bottom: `${settings.safe_area.bottom * 100}%`,
            left: `${settings.safe_area.left * 100}%`, right: `${settings.safe_area.right * 100}%`,
          }}><span>TEXT AREA</span></div>}
        </div><p className="hint">{data.note} {preview.note}</p>
      </div>}
      <p className="hint">Preview uses the export renderer at draft size. Save retains portrait size, text guides and the progress bar. Update video makes the finished MP4; Download saves it. Undo restores previous export and sound settings.</p>
      {plan.short_export && <p className="hint">Project output settings remove the export size, guides and bar; the current portrait shape and sound destination stay selected.</p>}
    </>}
  </section>;
}
