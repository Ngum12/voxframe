import { useEffect, useState, type CSSProperties } from "react";
import { ApiError, getTransitionControls, saveTransition, previewTransition,
  type ScenePlan, type PlanEditResult, type TransitionTreatment, type TransitionControls } from "../api";
import { Notice } from "../components";
import { sceneThumbnail, showsSpeaker } from "./Filmstrip";

const KINDS = [["cut", "Cut"], ["crossfade", "Crossfade"], ["dip_to_black", "Dip to black"],
  ["slide", "Slide"], ["push", "Push"], ["zoom", "Zoom"], ["soft_blur", "Soft blur"]];
const label = (kind: string) => KINDS.find(([k]) => k === kind)?.[1] ?? kind;

export function TransitionStudio({ jobId, plan, sceneIndex, onEdited }: {
  jobId: string; plan: ScenePlan; sceneIndex: number; onEdited: (edit: PlanEditResult) => void;
}) {
  const [index, setIndex] = useState(Math.min(sceneIndex, Math.max(0, plan.scenes.length - 2)));
  const [controls, setControls] = useState<TransitionControls | null>(null);
  const [look, setLook] = useState<TransitionTreatment | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const [rendered, setRendered] = useState<string | null>(null), [notice, setNotice] = useState("");
  const [playing, setPlaying] = useState(true);
  useEffect(() => {
    if (plan.scenes.length < 2) return;
    if (index >= plan.scenes.length - 1) { setIndex(plan.scenes.length - 2); return; }
    let live = true;
    setControls(null); setLook(null); setRendered(null); setError(null);
    getTransitionControls(jobId, index).then((c) => {
      if (live) { setControls(c); setLook(c.treatment); }
    }).catch((e) => { if (live) setError(e instanceof ApiError ? e.message : "The join could not be loaded."); });
    return () => { live = false; };
  }, [jobId, index, plan]);
  if (plan.scenes.length < 2) return <Notice tone="info">A transition needs two scenes. This video has one.</Notice>;
  if (!controls || !look) return error ? <Notice tone="error">{error}</Notice> : <p>Loading transitions…</p>;
  const dirty = JSON.stringify(look) !== JSON.stringify(controls.treatment);
  const wanted = Math.min(Math.round(look.seconds * plan.fps), controls.max_frames);
  const frames = look.kind === "cut" || wanted < 4 ? 0 : wanted;
  const patch = (change: Partial<TransitionTreatment>) => { setLook({ ...look, ...change }); setRendered(null); setNotice(""); };
  const run = async (work: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await work(); } catch (e) { setError(e instanceof ApiError ? e.message : "That transition could not be completed."); }
    finally { setBusy(false); }
  };
  const save = (all: boolean, reset = false) => void run(async () => {
    onEdited(await saveTransition(jobId, index, reset ? null : look, all));
  });
  const title = (i: number) => plan.scenes[i].card_text || plan.scenes[i].caption_text || plan.scenes[i].text || `Scene ${i + 1}`;
  const thumb = (i: number) => <>{(plan.scenes[i].asset || showsSpeaker(plan.scenes[i])) && <img src={sceneThumbnail(jobId, plan.scenes[i])} alt="" />}<span>{title(i)}</span></>;
  return <section className="transition-studio" aria-label="Transition studio">
    <header><span className="caption-eyebrow">MAKE THE CUT FEEL RIGHT</span><h3>Give each join a rhythm.</h3>
      <p className="muted">Your pictures move. Your voice and word timings stay in place.</p></header>
    <label className="label">Join<select aria-label="Choose join" disabled={busy} value={index} onChange={(e) => setIndex(Number(e.target.value))}>
      {plan.scenes.slice(0, -1).map((s, i) => <option key={s.index} value={i}>Scene {i + 1} → {i + 2} · {(s.card_text || s.text).slice(0, 38)}</option>)}
    </select></label>
    <div className="transition-boundary"><div>{thumb(index)}<small>Scene {index + 1}</small></div><span aria-hidden="true">→</span><div>{thumb(index + 1)}<small>Scene {index + 2}</small></div></div>
    <div className="transition-presets" role="group" aria-label="Transition presets">
      {Object.entries(controls.presets).map(([name, treatment]) => <button type="button" key={name} disabled={busy}
        aria-pressed={JSON.stringify(look) === JSON.stringify(treatment)} onClick={() => { setLook(treatment); setRendered(null); setNotice(""); }}>
        <strong>{name[0].toUpperCase() + name.slice(1)}</strong><span>{label(treatment.kind)}</span></button>)}
    </div>
    <div className={`transition-stage ${plan.aspect === "9:16" ? "portrait" : plan.aspect === "1:1" ? "square" : ""} kind-${look.kind} direction-${look.direction} ${playing ? "playing" : ""}`} style={{ aspectRatio: plan.aspect.replace(":", "/"), "--join-cycle": `${2 + look.seconds}s` } as CSSProperties}>
      {rendered ? <video src={rendered} controls autoPlay loop muted playsInline aria-label="Rendered transition preview" /> : <>
        <div className="transition-sketch outgoing">{thumb(index)}</div><div className="transition-sketch incoming" key={JSON.stringify(look)}>{thumb(index + 1)}</div>
        <span className="transition-stage-note">JOIN SKETCH</span></>}
    </div>
    <div className="caption-preview-actions">
      <button type="button" className="btn btn-small" disabled={busy} onClick={() => { setPlaying(!playing); setRendered(null); }}>{playing ? "Pause sketch" : "Play sketch"}</button>
      <button type="button" className="btn btn-small" disabled={busy} onClick={() => void run(async () => {
        const preview = await previewTransition(jobId, index, look); setRendered(preview.url); setNotice(preview.note);
      })}>{busy ? "Working…" : "Preview this join"}</button></div>
    <p className="hint">The sketch shows the idea. Preview this join to check the rendered effect with your pictures.</p>
    <fieldset disabled={busy} className="transition-controls"><legend className="sr-only">Transition appearance</legend>
      <label className="label">Transition<select aria-label="Transition" value={look.kind} onChange={(e) => patch({ kind: e.target.value as TransitionTreatment["kind"], seconds: look.seconds || .4 })}>{KINDS.map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></label>
      {look.kind !== "cut" && <label className="label">Duration · {look.seconds.toFixed(2)} s<input aria-label="Transition duration" type="range" min=".1" max="1.5" step=".05" value={look.seconds} onChange={(e) => patch({ seconds: Number(e.target.value) })} /></label>}
      {["slide", "push"].includes(look.kind) && <label className="label">Direction<select aria-label="Direction" value={look.direction} onChange={(e) => patch({ direction: e.target.value as TransitionTreatment["direction"] })}>
        <option value="left">Left</option><option value="right">Right</option><option value="up">Up</option><option value="down">Down</option></select></label>}
    </fieldset>
    <p className="transition-timing" role="status">{frames ? `${frames} frames · ${(frames / plan.fps).toFixed(2)} s at this join` : "A clean cut at this join"}{frames && wanted < Math.round(look.seconds * plan.fps) ? " · Shortened to keep both scenes readable." : ""}</p>
    <p className="hint">{controls.source === "template" ? `Template choice: ${controls.resolved.reason}.` : controls.source === "video" ? "Using your whole-video transition." : "A transition chosen for this join."} Blends start at the join and take at most a quarter of the shorter scene.</p>
    {error && <Notice tone="error">{error}</Notice>}{notice && <p className="hint" role="status">{notice}</p>}
    <div className="caption-save-actions"><button type="button" className="btn btn-primary" disabled={busy || (!dirty && controls.source === "scene")} onClick={() => save(false)}>Save for this join</button>
      <button type="button" className="btn" disabled={busy} onClick={() => save(true)}>Apply to all joins</button>
      {controls.source === "scene" && <button type="button" className="link-button" disabled={busy} onClick={() => save(false, true)}>Use video default</button>}
      {plan.transition_treatment && <button type="button" className="link-button" disabled={busy} onClick={() => save(true, true)}>Reset all to template</button>}</div>
    <p className="hint">{dirty ? "Unsaved transition changes." : "Saved in your plan."} Save, then update the video. Each saved change can be undone.</p>
    <div className="transition-join-map" aria-label="All joins">{plan.scenes.slice(0, -1).map((s, i) => <button key={s.index} type="button" disabled={busy} aria-pressed={index === i} onClick={() => setIndex(i)}>
      <span>{i + 1} → {i + 2}</span><small>{s.transition_after ? label(s.transition_after.kind) : plan.transition_treatment ? label(plan.transition_treatment.kind) : "Template"}</small></button>)}</div>
  </section>;
}
