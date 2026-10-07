import { useEffect, useRef, useState } from "react";
import { getOpeningAuditions, previewOpening, saveComplete, listOpeningSignatures, saveOpeningSignature, deleteOpeningSignature, previewOpeningSignature, type OpeningSignature, type OpeningChoice, type OpeningControls, type OpeningPreview, type CompleteWinner, type ScenePlan, type PlanEditResult } from "../api";
import { StoryComparison, type StoryVariant } from "./StoryComparison";
import { Notice } from "../components";

type Audition = StoryVariant<CompleteWinner> & {settings: OpeningChoice; preview: OpeningPreview; reviewed: boolean};
export function OpeningAuditions({jobId, plan, onEdited}: {jobId: string; plan: ScenePlan; onEdited: (result: PlanEditResult) => void}) {
  const [open, setOpen] = useState(false), [data, setData] = useState<OpeningControls | null>(null);
  const [last, setLast] = useState(0), [look, setLook] = useState<OpeningChoice["look"]>("authority");
  const [shot, setShot] = useState<OpeningChoice["shot"]>("keep"), [asset, setAsset] = useState<number | null>(null);
  const [match, setMatch] = useState(true), [replace, setReplace] = useState(false);
  const [variants, setVariants] = useState<Audition[]>([]), [active, setActive] = useState("");
  const [comparing, setComparing] = useState(false), [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(""), [error, setError] = useState<string | null>(null);
  const [signatures, setSignatures] = useState<OpeningSignature[]>([]), [signature, setSignature] = useState("");
  const [name, setName] = useState(""), [saveSource, setSaveSource] = useState("");
  const [signatureError, setSignatureError] = useState<string | null>(null);
  const generation = useRef(0), player = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    generation.current++; setData(null); setVariants([]); setActive(""); setBusy(false); setError(null); setStatus("");
    setSignature(""); setName(""); setSaveSource(""); setSignatureError(null);
    setLook("authority"); setShot("keep"); setAsset(null); setMatch(true); setReplace(false); setComparing(false);
    return () => { generation.current++; };
  }, [jobId, plan]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    getOpeningAuditions(jobId).then(result => { if (alive) { setData(result); setLast(result.default_last_word ?? 0); } })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : "Could not load opening words."); });
    return () => { alive = false; };
  }, [jobId, plan, open]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    listOpeningSignatures().then(result => { if (alive) { setSignatures(result.signatures); setSignatureError(null); } })
      .catch(e => { if (alive) setSignatureError(e instanceof Error ? e.message : "Could not load opening signatures."); });
    return () => { alive = false; };
  }, [open, jobId]);
  const saved = signatures.find(item => item.id === signature);
  const toSave = variants.find(item => item.key === (saveSource || active));
  function loadSignature(id: string) {
    setSignature(id); setReplace(false); setAsset(null);
    const item = signatures.find(item => item.id === id);
    if (!item || !data) return;
    setLook(item.look); setShot(item.shot); setMatch(item.match_captions);
    const endings = data.endings.filter(ending => ending.last_word + 1 <= item.preferred_words);
    setLast((endings.at(-1) ?? data.endings[0])?.last_word ?? 0);
  }
  async function manage(remove: boolean) {
    if (busy || (!remove && (!toSave?.reviewed || !name.trim()))) return;
    const current = generation.current;
    setBusy(true); setSignatureError(null);
    try {
      if (remove && saved) {
        await deleteOpeningSignature(saved.id);
        if (generation.current !== current) return;
        setSignatures(items => items.filter(item => item.id !== saved.id)); setSignature("");
        setStatus("Saved signature removed. Your projects and rendered drafts stay intact.");
      } else if (toSave) {
        const result = await saveOpeningSignature(jobId, name.trim(), toSave.choice.revision, toSave.preview.opening_id);
        if (generation.current !== current) return;
        setSignatures(items => [...items, result]); setName("");
        setStatus(`Opening signature saved: ${result.name}. Available on other recordings.`);
      }
    } catch (e) { if (generation.current === current) setSignatureError(e instanceof Error ? e.message : "Could not update opening signatures."); }
    finally { if (generation.current === current) setBusy(false); }
  }
  const choice: OpeningChoice | null = data ? {revision: data.revision, last_word: last, look, shot,
    asset_scene: shot === "picture" ? asset : null, match_captions: match, replace_pinned: replace} : null;
  const choiceKey = JSON.stringify(choice), window = data?.endings.find(ending => ending.last_word === last);
  const preview = variants.find(variant => variant.key === active);
  useEffect(() => { player.current?.pause(); setComparing(false); setActive(variants.find(variant => JSON.stringify(variant.settings) === choiceKey)?.key ?? ""); }, [choiceKey]);
  useEffect(() => { const mounted = player.current; return () => { mounted?.pause(); }; }, [active, comparing, open]);
  async function render(all: boolean) {
    if (!choice || !data || busy) return;
    const current = generation.current, options = all ? (["authority", "energy", "cinema"] as const) : [choice.look];
    setBusy(true); setError(null); setComparing(false); player.current?.pause();
    let selected = "";
    try {
      for (let index = 0; index < options.length; index++) {
        const settings = {...choice, look: options[index]};
        setStatus(`Rendering opening ${index + 1} of ${options.length} with its soundtrack…`);
        const result = signature && !all
          ? await previewOpeningSignature(jobId, {revision: settings.revision, last_word: settings.last_word, asset_scene: settings.asset_scene, replace_pinned: settings.replace_pinned}, signature)
          : await previewOpening(jobId, settings);
        if (current !== generation.current) return;
        const variant: Audition = {key: result.preview_id, label: `${result.signature ? result.signature.name + " · " : ""}${data.profiles[result.settings.look].label} · ${result.opening.quote}`,
          settings: result.settings, preview: result, reviewed: false, choice: {revision: result.revision, preview_id: result.preview_id},
          warnings: [...(result.sound?.problems ?? []), ...(result.music_note ? [result.music_note] : [])]};
        setVariants(previous => [...previous.filter(item => item.key !== variant.key), variant].slice(-6));
        if (settings.look === choice.look || !selected) selected = variant.key;
        setActive(selected);
      }
      setStatus("Opening auditions ready. Review the opening and its return to the story before choosing.");
    } catch (e) { if (current === generation.current) { setError(e instanceof Error ? e.message : "Could not render this opening."); setStatus(""); } }
    finally { if (current === generation.current) setBusy(false); }
  }
  const canChoose = (winner: CompleteWinner) => !!variants.find(variant => variant.key === winner.preview_id)?.reviewed;
  async function choose(winner: CompleteWinner) {
    if (busy || !canChoose(winner)) return;
    const current = generation.current;
    setBusy(true); setError(null); player.current?.pause();
    try { const result = await saveComplete(jobId, winner); if (current === generation.current) onEdited(result); }
    catch (e) { if (current === generation.current) setError(e instanceof Error ? e.message : "Could not save this opening."); }
    finally { if (current === generation.current) setBusy(false); }
  }
  const blocked = !window || (window.pinned && !replace) || (shot === "speaker" && !window.has_speaker) || (shot === "picture" && asset === null);
  return <details className="story-composer opening-auditions" onToggle={event => {
    setOpen(event.currentTarget.open); if (!event.currentTarget.open) { player.current?.pause(); setComparing(false); }
  }}><summary>Opening auditions · make the first words count</summary>
    {open && <section aria-label="Opening auditions">
      <h3>Same words. A different entrance.</h3>
      <p>Compare three visual openings on your actual spoken words. Hear the full story and soundtrack, including the return from the opening.</p>
      {data && !data.eligible && <Notice>Compose a 3–60 second story in Shorts first.</Notice>}
      {data?.eligible && !data.endings.length && <Notice>This opening needs word timings with a clear boundary. Check the transcript in Captions first.</Notice>}
      {!data && !error && <p role="status">Finding your opening words…</p>}
      {!!data?.endings.length && <>
        <fieldset disabled={busy}>
          <label className="label">Opening signature<select aria-label="Opening signature" value={signature} onChange={event => loadSignature(event.target.value)}><option value="">Make a new opening</option>{signatures.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          {saved && <div className="opening-signature-summary"><p className="hint">{data.profiles[saved.look].label} · {saved.preferred_words} words preferred · {saved.shot === "picture" ? "Choose a picture from this project" : saved.shot === "speaker" ? "Needs speaker footage" : "Keeps this project’s shots"}. Uses this recording’s words and soundtrack.</p><button className="btn" onClick={() => void manage(true)}>Remove saved signature</button></div>}
          <label className="label">Opening words<select aria-label="Opening words" value={last} onChange={event => setLast(Number(event.target.value))}>{data.endings.map(ending => <option key={ending.last_word} value={ending.last_word}>{ending.quote} · {(ending.end - ending.start).toFixed(1)}s</option>)}</select></label>
          <p className="opening-quote">“{window?.quote}”</p>
          <p className="hint">Opening at {window?.start.toFixed(2)}–{window?.end.toFixed(2)}s. Intro cards remain in place. Choose a complete thought; these treatments do not change the recording.</p>
          <div className="story-look-options">{Object.entries(data.profiles).map(([key, profile]) => <button type="button" className="story-look" aria-pressed={look === key} key={key} onClick={() => { setSignature(""); setLook(key as OpeningChoice["look"]); }}><strong>{profile.label}</strong><span>{profile.note}</span></button>)}</div>
          <label className="label">Opening shot<select aria-label="Opening shot" value={shot} onChange={event => { setSignature(""); setShot(event.target.value as OpeningChoice["shot"]); }}><option value="keep">Keep current shots</option><option value="speaker" disabled={!window?.has_speaker}>Speaker</option><option value="picture" disabled={!data.visuals.length}>Project picture</option></select></label>
          {shot === "picture" && <label className="label">Opening picture<select aria-label="Opening picture" value={asset ?? ""} onChange={event => setAsset(event.target.value === "" ? null : Number(event.target.value))}><option value="">Choose a project picture</option>{data.visuals.map(visual => <option key={visual.scene} value={visual.scene}>Scene {visual.scene + 1}: {visual.quote || visual.credit}</option>)}</select></label>}
          <label className="check"><input type="checkbox" checked={match} onChange={event => { setSignature(""); setMatch(event.target.checked); }} />Match captions inside the opening</label>
          {window?.pinned && <label className="check"><input type="checkbox" checked={replace} onChange={event => setReplace(event.target.checked)} />Allow replacing my pinned opening text</label>}
          <div className="story-actions"><button className="btn" disabled={blocked} onClick={() => void render(false)}>{saved ? "Preview saved opening signature" : "Preview selected opening"}</button><button className="btn btn-primary" disabled={blocked || !!signature} onClick={() => void render(true)}>Render three opening auditions</button></div>
        </fieldset>
        {preview && !comparing && <div className="story-preview"><h4>{preview.label}</h4><video ref={player} controls playsInline preload="metadata" src={preview.preview.url} aria-label="Opening audition preview" />
          <p className="hint">{preview.preview.note} {preview.preview.music_note}</p>
          {!!preview.preview.sound?.problems.length && <Notice>{preview.preview.sound.problems.join(" ")}</Notice>}
          <button className="btn btn-primary" disabled={busy || !preview.reviewed} onClick={() => void choose(preview.choice)}>Use this opening</button></div>}
        <div className="opening-reviews">{variants.map(variant => <div className="story-block" key={variant.key}><button className="btn" disabled={busy} onClick={() => { player.current?.pause(); setComparing(false); setActive(variant.key); }}>Watch {variant.label}</button><label className="check"><input type="checkbox" disabled={busy} aria-label={`Reviewed ${variant.label}`} checked={variant.reviewed} onChange={event => { const reviewed = event.target.checked; setVariants(previous => previous.map(item => item.key === variant.key ? {...item, reviewed} : item)); }} />I reviewed this opening and its return to the story</label></div>)}</div>
        {!!variants.length && <fieldset disabled={busy} className="opening-signature-save"><legend>Keep a reviewed opening as your signature</legend>
          <label className="label">Opening to save<select aria-label="Opening to save" value={saveSource || active} onChange={event => setSaveSource(event.target.value)}><option value="">Choose a rendered opening</option>{variants.map(item => <option key={item.key} value={item.key}>{item.label}{item.reviewed ? " · reviewed" : " · review needed"}</option>)}</select></label>
          <label className="label">Signature name<input aria-label="Opening signature name" maxLength={60} value={name} onChange={event => setName(event.target.value)} /></label>
          <button className="btn" disabled={!toSave?.reviewed || !name.trim()} onClick={() => void manage(false)}>Save reviewed opening signature</button>
          <p className="hint">Save before applying to this project. Keeps the reviewed draft’s recipe. New recordings provide their own words, pictures and music. Pinned-text replacement always needs a fresh choice.</p>
        </fieldset>}
        {variants.length >= 2 && <><button className="btn" disabled={busy} onClick={() => { player.current?.pause(); setComparing(value => !value); }}>{comparing ? "Close opening comparison" : "Compare opening auditions"}</button>{comparing && <StoryComparison complete variants={variants} busy={busy} canChoose={canChoose} onChoose={winner => void choose(winner)} />}</>}
      </>}
      {status && <p role="status">{status}</p>}{error && <Notice tone="error">{error}</Notice>}{signatureError && <Notice tone="error">{signatureError}</Notice>}
      <p className="hint">Previews leave the project intact. Choosing saves the exact reviewed preview as one undoable edit. Timing, music and the rest of your story carry over. Update video creates the export. The last six drafts remain available during this session.</p>
    </section>}
  </details>;
}
