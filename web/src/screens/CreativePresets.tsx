import { useEffect, useState } from "react";
import { listCreativePresets, deleteCreativePreset, saveCreativePreset,
  type CreativePreset, type CreativeSettings, type ScenePlan } from "../api";
import { Notice } from "../components";

export function PresetPicker({ onChange }: { onChange: (settings: CreativeSettings | null) => void }) {
  const [presets, setPresets] = useState<CreativePreset[]>([]);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    listCreativePresets().then(data => { if (live) setPresets(data.presets); })
      .catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, []);
  const preset = presets.find(p => p.id === selected);
  async function remove() {
    if (!preset || busy) return;
    setBusy(true); setError(null);
    try {
      await deleteCreativePreset(preset.id);
      setPresets(items => items.filter(p => p.id !== preset.id));
      setSelected(""); onChange(null);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not delete the preset."); }
    finally { setBusy(false); }
  }
  return <section className="card creative-presets" aria-label="Creative preset">
    <h3>Your signature look</h3>
    <label className="label">Creative preset<select aria-label="Creative preset" disabled={busy} value={selected}
      onChange={e => {
        const id = e.target.value; setSelected(id);
        const chosen = presets.find(p => p.id === id);
        onChange(chosen ? {caption_treatment: chosen.caption_treatment, audio_mix: chosen.audio_mix} : null);
      }}>
      <option value="">Use this style’s defaults</option>
      {presets.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select></label>
    {preset && <>
      <p className="hint">{preset.caption_treatment?.animation ?? "Style defaults"} captions · {preset.audio_mix.music_arc} music arc · {preset.audio_mix.destination} sound</p>
      <details><summary>Manage this preset</summary>
        <p className="hint">Deleting removes it from future choices. Existing projects keep their settings.</p>
        <button className="btn" disabled={busy} onClick={() => void remove()}>{busy ? "Deleting…" : "Delete selected preset"}</button>
      </details>
    </>}
    <p className="hint">Save a caption look and sound mix in your project’s Export tab. Presets stay on this computer. Caption sizing follows your chosen style. Choose music separately for each recording.</p>
    {error && <Notice tone="error">{error}</Notice>}
  </section>;
}

export function PresetSaver({ jobId, plan, revision }: { jobId: string; plan: ScenePlan; revision: string | null }) {
  const scenes = plan.scenes.filter(s => !s.card_kind);
  const [name, setName] = useState("");
  const [scene, setScene] = useState(scenes[0]?.index ?? 0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => { setScene(plan.scenes.find(s => !s.card_kind)?.index ?? 0); setSaved(null); }, [plan, jobId]);
  async function save() {
    if (!revision || busy) return;
    setBusy(true); setSaved(null); setError(null);
    try {
      const preset = await saveCreativePreset(jobId, name, revision, scene);
      setSaved(`“${preset.name}” saved. Choose it after uploading your next recording.`);
      setName("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save your preset."); }
    finally { setBusy(false); }
  }
  return <details className="card creative-presets"><summary>Keep this as your signature preset</summary>
    <p className="hint">Reuse one scene’s caption treatment and the project’s saved sound mix. Words, emphasis, cuts, footage, music files and export size belong to this project.</p>
    <fieldset disabled={busy || !revision || !scenes.length}>
      <label className="label">Preset name<input type="text" aria-label="Preset name" maxLength={60} value={name}
        onChange={e => { setName(e.target.value); setSaved(null); }} placeholder="My bold voice" /></label>
      <label className="label">Caption look from<select aria-label="Caption look from" value={scene}
        onChange={e => { setScene(Number(e.target.value)); setSaved(null); }}>
        {scenes.map(s => <option key={s.index} value={s.index}>Scene {s.index + 1} · {(s.caption_text || s.text).slice(0, 65)}</option>)}
      </select></label>
      <button className="btn" disabled={!name.trim()} onClick={() => void save()}>{busy ? "Saving…" : "Save creative preset"}</button>
    </fieldset>
    {saved && <Notice live>{saved}</Notice>}
    {error && <Notice tone="error">{error}</Notice>}
  </details>;
}
