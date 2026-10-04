import { useEffect, useState, type CSSProperties } from "react";
import { ApiError, getCaptionControls, previewCaptions, saveCaptionLook, suggestCaptionEmphasis,
  type CaptionTreatment, type CaptionControls, type PlanEditResult, type PlannedScene } from "../api";
import { Notice } from "../components";
const MODES = [["highlight", "Highlight"], ["karaoke", "Karaoke"], ["pop", "Pop-in"],
  ["typewriter", "Typewriter"], ["emphasis", "Emphasis"], ["plain", "Plain"],
  ["spotlight", "Spotlight"], ["pulse", "Pulse"]];
const ACCENTS = ["#FFD700", "#70F0D0", "#FFCE56", "#F4A9FF", "#F5C38B", "#FFFFFF"];
export function CaptionStudio({ jobId, scene, fps, aspect, onEdited }: {
  jobId: string; scene: PlannedScene; fps: number; aspect: string;
  onEdited: (result: PlanEditResult) => void;
}) {
  const [controls, setControls] = useState<CaptionControls | null>(null);
  const [look, setLook] = useState<CaptionTreatment | null>(null);
  const [emphasis, setEmphasis] = useState<number[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState(""), [playing, setPlaying] = useState(true);
  const [clock, setClock] = useState(0), [rendered, setRendered] = useState<string | null>(null);
  const duration = Math.max(0.1, (scene.end_frame - scene.start_frame) / fps);
  useEffect(() => {
    let live = true;
    getCaptionControls(jobId, scene.index).then((c) => {
      if (live) { setControls(c); setLook(c.treatment); setEmphasis(c.emphasis); }
    }).catch((e) => { if (live) setError(e instanceof ApiError ? e.message : "Captions could not be loaded."); });
    return () => { live = false; };
  }, [jobId, scene.index, scene.caption_treatment, scene.caption_emphasis]);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setClock((c) => (c + .06) % duration), 60);
    return () => window.clearInterval(timer);
  }, [playing, duration]);
  if (!look || !controls) return error ? <Notice tone="error">{error}</Notice> : <p>Loading caption studio…</p>;
  const active = controls.words.reduce((a, w, i) => w.start - scene.start_frame / fps <= clock ? i : a, -1);
  const page = Math.floor(Math.max(0, active) / look.words_per_page);
  const dirty = JSON.stringify(look) !== JSON.stringify(controls.treatment) || JSON.stringify(emphasis) !== JSON.stringify(controls.emphasis);
  const patch = (p: Partial<CaptionTreatment>) => { setLook({ ...look, ...p }); setRendered(null); setNotice(""); };
  const run = async (work: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await work(); } catch (e) { setError(e instanceof ApiError ? e.message : "That caption change could not be completed."); }
    finally { setBusy(false); }
  };
  const save = (all: boolean, reset = false) => void run(async () => {
    onEdited(await saveCaptionLook(jobId, scene.index, reset ? null : look, emphasis, all));
    const c = await getCaptionControls(jobId, scene.index);
    setControls(c); setLook(c.treatment); setEmphasis(c.emphasis);
    setNotice("Caption look saved. Update the video when you are ready.");
  });
  const style = { "--caption-accent": look.accent, "--caption-ink": look.color,
    "--caption-size": look.size, "--caption-emphasis": look.emphasis_scale,
    "--caption-lift": `${look.lift * 100}%`, aspectRatio: aspect.replace(":", "/") } as CSSProperties;
  return <section className="caption-studio" aria-label="Caption studio">
    <header><span className="caption-eyebrow">MAKE EVERY WORD COUNT</span><h3>Give your voice a look.</h3>
      <p className="muted">Start with a look. Make it yours. Your word timings stay intact.</p></header>
    <div className="caption-presets" role="group" aria-label="Caption presets">
      {Object.entries(controls.presets).map(([key, preset]) => <button type="button" key={key} disabled={busy}
        className={`caption-preset ${JSON.stringify(look) === JSON.stringify(preset) ? "selected" : ""}`}
        aria-pressed={JSON.stringify(look) === JSON.stringify(preset)}
        onClick={() => { setLook(preset); setRendered(null); setNotice(""); }}>
        <span style={{ color: preset.accent }}>Make it {key === "quiet" ? "clear" : "matter"}.</span>
        <strong>{key[0].toUpperCase() + key.slice(1)}</strong></button>)}
    </div>
    <div className={`caption-stage ${aspect === "9:16" ? "portrait" : ""}`} style={style}>
      {rendered ? <video src={rendered} controls autoPlay loop muted playsInline aria-label="Rendered caption preview" /> : <>
        <span className="caption-stage-note">STYLE SKETCH · THIS SCENE</span>
        <div className={`caption-demo ${look.position} backing-${look.backing} mode-${look.animation} ${look.uppercase ? "uppercase" : ""}`} aria-hidden="true">
          {controls.words.map((w, i) => ({ ...w, i })).filter((w) => Math.floor(w.i / look.words_per_page) === page && (look.animation !== "spotlight" || w.i === active)).map((w) => <span key={`${page}-${w.i}-${w.i === active}`}
            className={`caption-demo-word ${w.i === active && !["plain", "emphasis"].includes(look.animation) ? "current" : ""} ${emphasis.includes(w.i) ? "emphasized" : ""} ${look.animation === "karaoke" && w.i <= active ? "filled" : ""}`}
            style={{ visibility: !["pop", "typewriter"].includes(look.animation) || w.i <= active ? "visible" : "hidden" }}>{w.text} </span>)}
        </div></>}
    </div>
    <div className="caption-preview-actions">
      <button type="button" className="btn btn-small" onClick={() => { setPlaying(!playing); setRendered(null); }}>{playing ? "Pause sketch" : "Play sketch"}</button>
      <button type="button" className="btn btn-small" disabled={busy} onClick={() => void run(async () => {
        const result = await previewCaptions(jobId, scene.index, look, emphasis); setRendered(result.url); setNotice(result.note);
      })}>{busy ? "Working…" : "Render exact preview"}</button></div>
    <p className="hint">The sketch responds immediately. Render an exact preview to check the export on a neutral background.</p>
    <fieldset disabled={busy} className="caption-controls"><legend className="sr-only">Caption appearance</legend>
      <label className="label">Animation<select value={look.animation} onChange={(e) => patch({ animation: e.target.value as CaptionTreatment["animation"] })}>
        {MODES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <div className="caption-control-pair">
        <label className="label">Position<select value={look.position} onChange={(e) => patch({ position: e.target.value as CaptionTreatment["position"] })}>
          <option value="bottom">Bottom</option><option value="center">Centre</option><option value="top">Top</option></select></label>
        <label className="label">Backing<select value={look.backing} onChange={(e) => patch({ backing: e.target.value as CaptionTreatment["backing"] })}>
          <option value="box">Soft box</option><option value="band">Full band</option><option value="outline">Outline only</option></select></label></div>
      <div className="caption-control-pair">
        <label className="label">Accent colour<input type="color" value={look.accent} onChange={(e) => patch({ accent: e.target.value })} /></label>
        <label className="label">Text colour<input type="color" value={look.color} onChange={(e) => patch({ color: e.target.value })} /></label></div>
      <div className="caption-swatches" role="group" aria-label="Accent colours">{ACCENTS.map((c) => <button type="button" key={c} style={{ backgroundColor: c }} aria-label={`Use accent ${c}`}
        aria-pressed={look.accent.toUpperCase() === c} onClick={() => patch({ accent: c })} />)}</div>
      <label className="label">Size · {Math.round(look.size * 100)}%<input type="range" min=".7" max="1.5" step=".05" value={look.size} onChange={(e) => patch({ size: Number(e.target.value) })} /></label>
      <label className="label">Words per page · {look.words_per_page}<input type="range" min="1" max="18" step="1" value={look.words_per_page} onChange={(e) => patch({ words_per_page: Number(e.target.value) })} /></label>
      <div className="caption-control-pair">
        <label className="label">Lines<select value={look.max_lines} onChange={(e) => patch({ max_lines: Number(e.target.value) })}><option value="1">One</option><option value="2">Two</option><option value="3">Three</option></select></label>
        <label className="label">Lift · {Math.round(look.lift * 100)}%<input type="range" min="0" max=".15" step=".01" value={look.lift} onChange={(e) => patch({ lift: Number(e.target.value) })} /></label></div>
      <label className="check"><input type="checkbox" checked={look.uppercase} onChange={(e) => patch({ uppercase: e.target.checked })} /> All caps</label>
    </fieldset>
    <div className="caption-emphasis"><div className="caption-preview-actions"><h4>Choose the words that land.</h4>
      <button type="button" className="btn btn-small" disabled={busy} onClick={() => void run(async () => {
        const result = await suggestCaptionEmphasis(jobId, scene.index); setEmphasis(result.emphasis); setRendered(null);
        setNotice("Suggestions from your voice. Review the marked words, then save to apply them.");
      })}>Suggest emphasis</button></div>
      <p className="hint">Click words to emphasise them. Suggestions measure your voice locally; they never save automatically.</p>
      <div className="caption-word-picks" role="group" aria-label="Emphasis words">{controls.words.map((w, i) => <button type="button" key={i} disabled={busy} aria-pressed={emphasis.includes(i)} className={emphasis.includes(i) ? "chosen" : ""}
        onClick={() => { setEmphasis(emphasis.includes(i) ? emphasis.filter((v) => v !== i) : [...emphasis, i].sort((a, b) => a - b)); setRendered(null); }}>{w.text}</button>)}</div>
      <label className="label">Emphasis strength · {Math.round(look.emphasis_scale * 100)}%<input type="range" min="1" max="1.35" step=".01" value={look.emphasis_scale} disabled={busy} onChange={(e) => patch({ emphasis_scale: Number(e.target.value) })} /></label></div>
    {error && <Notice tone="error">{error}</Notice>}{notice && <p className="hint" role="status">{notice}</p>}
    <div className="caption-save-actions"><button type="button" className="btn btn-primary" disabled={busy || !dirty} onClick={() => save(false)}>Save for this scene</button>
      <button type="button" className="btn" disabled={busy} onClick={() => save(true)}>Apply look to whole video</button>
      {!controls.inherited && <button type="button" className="link-button" disabled={busy} onClick={() => save(false, true)}>Use video look</button>}</div>
    <p className="hint">{dirty ? "Unsaved caption changes." : "Saved in your plan."} Every saved change can be undone in the studio.</p>
  </section>;
}
