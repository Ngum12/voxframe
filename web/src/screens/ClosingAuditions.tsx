import { useEffect, useRef, useState } from "react";
import { getClosingAuditions, previewClosing, saveComplete, type ClosingChoice, type ClosingControls, type ClosingPreview, type CompleteWinner, type ScenePlan, type PlanEditResult } from "../api";
import { StoryComparison, type StoryVariant } from "./StoryComparison";
import { Notice } from "../components";

type Audition = StoryVariant<CompleteWinner> & {settings: ClosingChoice; preview: ClosingPreview; reviewed: boolean};
export function ClosingAuditions({jobId, plan, onEdited}: {jobId: string; plan: ScenePlan; onEdited: (result: PlanEditResult) => void}) {
  const [open, setOpen] = useState(false), [data, setData] = useState<ClosingControls | null>(null);
  const [first, setFirst] = useState(0), [look, setLook] = useState<ClosingChoice["look"]>("authority");
  const [shot, setShot] = useState<ClosingChoice["shot"]>("keep"), [asset, setAsset] = useState<number | null>(null);
  const [match, setMatch] = useState(true), [replace, setReplace] = useState(false);
  const [variants, setVariants] = useState<Audition[]>([]), [active, setActive] = useState("");
  const [comparing, setComparing] = useState(false), [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(""), [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const generation = useRef(0), player = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    generation.current++; setData(null); setVariants([]); setActive(""); setBusy(false); setError(null); setStatus("");
    setLook("authority"); setShot("keep"); setAsset(null); setMatch(true); setReplace(false); setComparing(false);
    return () => { generation.current++; };
  }, [jobId, plan]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    getClosingAuditions(jobId).then(result => { if (alive) { setData(result); setFirst(result.default_first_word ?? 0); } })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : "Could not load closing words."); });
    return () => { alive = false; };
  }, [jobId, plan, open]);
  const choice: ClosingChoice | null = data ? {revision: data.revision, first_word: first, look, shot,
    asset_scene: shot === "picture" ? asset : null, match_captions: match, replace_pinned: replace} : null;
  const choiceKey = JSON.stringify(choice), window = data?.starts.find(ending => ending.first_word === first);
  const preview = variants.find(variant => variant.key === active);
  useEffect(() => { player.current?.pause(); setComparing(false); setActive(variants.find(variant => JSON.stringify(variant.settings) === choiceKey)?.key ?? ""); }, [choiceKey]);
  useEffect(() => { const mounted = player.current; setReady(!!mounted && mounted.readyState >= 1); return () => { mounted?.pause(); }; }, [active, comparing, open]);
  async function render(all: boolean) {
    if (!choice || !data || busy) return;
    const current = generation.current, options = all ? (["authority", "energy", "cinema"] as const) : [choice.look];
    setBusy(true); setError(null); setComparing(false); player.current?.pause();
    let selected = "";
    try {
      for (let index = 0; index < options.length; index++) {
        const settings = {...choice, look: options[index]};
        setStatus(`Rendering closing ${index + 1} of ${options.length} with its soundtrack…`);
        const result = await previewClosing(jobId, settings);
        if (current !== generation.current) return;
        const variant: Audition = {key: result.preview_id, label: `${data.profiles[settings.look].label} · ${result.closing.quote}`,
          settings, preview: result, reviewed: false, choice: {revision: result.revision, preview_id: result.preview_id},
          warnings: [...(result.sound?.problems ?? []), ...(result.music_note ? [result.music_note] : [])]};
        setVariants(previous => [...previous.filter(item => item.key !== variant.key), variant].slice(-6));
        if (settings.look === choice.look || !selected) selected = variant.key;
        setActive(selected);
      }
      setStatus("Closing auditions ready. Review the lead-in and final words before choosing.");
    } catch (e) { if (current === generation.current) { setError(e instanceof Error ? e.message : "Could not render this closing."); setStatus(""); } }
    finally { if (current === generation.current) setBusy(false); }
  }
  const canChoose = (winner: CompleteWinner) => !!variants.find(variant => variant.key === winner.preview_id)?.reviewed;
  async function choose(winner: CompleteWinner) {
    if (busy || !canChoose(winner)) return;
    const current = generation.current;
    setBusy(true); setError(null); player.current?.pause();
    try { const result = await saveComplete(jobId, winner); if (current === generation.current) onEdited(result); }
    catch (e) { if (current === generation.current) setError(e instanceof Error ? e.message : "Could not save this closing."); }
    finally { if (current === generation.current) setBusy(false); }
  }
  const blocked = !window || (window.pinned && !replace) || (shot === "speaker" && !window.has_speaker) || (shot === "picture" && asset === null);
  return <details className="story-composer closing-auditions" onToggle={event => {
    setOpen(event.currentTarget.open); if (!event.currentTarget.open) { player.current?.pause(); setComparing(false); }
  }}><summary>Closing auditions · let the final words land</summary>
    {open && <section aria-label="Closing auditions">
      <h3>Give the final idea room to land.</h3>
      <p>Compare three visual closings on your actual final words. Hear the full story, its lead-in and the soundtrack.</p>
      {data && !data.eligible && <Notice>Compose a 3–60 second story in Shorts first.</Notice>}
      {data?.eligible && !data.starts.length && <Notice>This closing needs word timings with a clear boundary. Check the transcript in Captions first.</Notice>}
      {!data && !error && <p role="status">Finding your final words…</p>}
      {!!data?.starts.length && <>
        <fieldset disabled={busy}>
          <label className="label">Closing words<select aria-label="Closing words" value={first} onChange={event => setFirst(Number(event.target.value))}>{data.starts.map(ending => <option key={ending.first_word} value={ending.first_word}>{ending.quote} · {(ending.end - ending.start).toFixed(1)}s</option>)}</select></label>
          <p className="closing-quote">“{window?.quote}”</p>
          <p className="hint">Closing at {window?.start.toFixed(2)}–{window?.end.toFixed(2)}s. Outro cards remain in place. Choose a complete thought; these treatments do not change the recording.</p>
          <div className="story-look-options">{Object.entries(data.profiles).map(([key, profile]) => <button type="button" className="story-look" aria-pressed={look === key} key={key} onClick={() => setLook(key as ClosingChoice["look"])}><strong>{profile.label}</strong><span>{profile.note}</span></button>)}</div>
          <label className="label">Closing shot<select aria-label="Closing shot" value={shot} onChange={event => setShot(event.target.value as ClosingChoice["shot"])}><option value="keep">Keep current shots</option><option value="speaker" disabled={!window?.has_speaker}>Speaker</option><option value="picture" disabled={!data.visuals.length}>Project picture</option></select></label>
          {shot === "picture" && <label className="label">Closing picture<select aria-label="Closing picture" value={asset ?? ""} onChange={event => setAsset(event.target.value === "" ? null : Number(event.target.value))}><option value="">Choose a project picture</option>{data.visuals.map(visual => <option key={visual.scene} value={visual.scene}>Scene {visual.scene + 1}: {visual.quote || visual.credit}</option>)}</select></label>}
          <label className="check"><input type="checkbox" checked={match} onChange={event => setMatch(event.target.checked)} />Match captions inside the closing</label>
          {window?.pinned && <label className="check"><input type="checkbox" checked={replace} onChange={event => setReplace(event.target.checked)} />Allow replacing my pinned closing text</label>}
          <div className="story-actions"><button className="btn" disabled={blocked} onClick={() => void render(false)}>Preview selected closing</button><button className="btn btn-primary" disabled={blocked} onClick={() => void render(true)}>Render three closing auditions</button></div>
        </fieldset>
        {preview && !comparing && <div className="story-preview"><h4>{preview.label}</h4><video ref={player} controls playsInline preload="metadata" src={preview.preview.url} aria-label="Closing audition preview" onLoadedMetadata={() => setReady(true)} onError={() => { setReady(false); setError("This draft could not load. Render the closing again."); }} />
          <button className="btn" disabled={!ready} onClick={() => { const video = player.current; if (video) { video.pause(); video.currentTime = Math.max(0, Math.min(preview.preview.closing.start - 1, video.duration)); } }}>Review the lead-in</button>
          <p className="hint">{preview.preview.note} {preview.preview.music_note}</p>
          {!!preview.preview.sound?.problems.length && <Notice>{preview.preview.sound.problems.join(" ")}</Notice>}
          <button className="btn btn-primary" disabled={busy || !preview.reviewed} onClick={() => void choose(preview.choice)}>Use this closing</button></div>}
        <div className="closing-reviews">{variants.map(variant => <div className="story-block" key={variant.key}><button className="btn" disabled={busy} onClick={() => { player.current?.pause(); setComparing(false); setActive(variant.key); }}>Watch {variant.label}</button><label className="check"><input type="checkbox" disabled={busy} aria-label={`Reviewed ${variant.label}`} checked={variant.reviewed} onChange={event => { const reviewed = event.target.checked; setVariants(previous => previous.map(item => item.key === variant.key ? {...item, reviewed} : item)); }} />I reviewed the lead-in and final words</label></div>)}</div>
        {variants.length >= 2 && <><button className="btn" disabled={busy} onClick={() => { player.current?.pause(); setComparing(value => !value); }}>{comparing ? "Close closing comparison" : "Compare closing auditions"}</button>{comparing && <StoryComparison complete reviewAt={Math.max(0, Math.min(...variants.map(variant => variant.preview.closing.start)) - 1)} variants={variants} busy={busy} canChoose={canChoose} onChoose={winner => void choose(winner)} />}</>}
      </>}
      {status && <p role="status">{status}</p>}{error && <Notice tone="error">{error}</Notice>}
      <p className="hint">Previews leave the project intact. Choosing saves the exact reviewed preview as one undoable edit. Timing, music and the rest of your story carry over. Update video creates the export. The last six drafts remain available during this session.</p>
    </section>}
  </details>;
}
