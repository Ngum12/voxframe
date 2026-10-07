import { useEffect, useRef, useState } from "react";
import { getBookendAuditions, previewBookend, saveComplete, listBookendSignatures, saveBookendSignature, deleteBookendSignature, previewBookendSignature, type BookendSignature, type BookendChoice, type BookendControls, type BookendPreview, type CompleteWinner, type ScenePlan, type PlanEditResult } from "../api";
import { StoryComparison, type StoryVariant } from "./StoryComparison";
import { bookendDefaults } from "../bookendDefaults";
import { Notice } from "../components";

type Audition = StoryVariant<CompleteWinner> & {settings: BookendChoice; preview: BookendPreview; reviewedOpening: boolean; reviewedClosing: boolean};
export function BookendAuditions({jobId, plan, onEdited}: {jobId: string; plan: ScenePlan; onEdited: (result: PlanEditResult) => void}) {
  const [open, setOpen] = useState(false), [data, setData] = useState<BookendControls | null>(null);
  const [first, setFirst] = useState(0), [last, setLast] = useState(0);
  const [openingLook, setOpeningLook] = useState<BookendChoice["opening_look"]>("authority"), [closingLook, setClosingLook] = useState<BookendChoice["closing_look"]>("authority");
  const [openingShot, setOpeningShot] = useState<BookendChoice["opening_shot"]>("keep"), [closingShot, setClosingShot] = useState<BookendChoice["closing_shot"]>("keep");
  const [match, setMatch] = useState(true), [replaceOpening, setReplaceOpening] = useState(false), [replaceClosing, setReplaceClosing] = useState(false);
  const [variants, setVariants] = useState<Audition[]>([]), [active, setActive] = useState("");
  const [comparing, setComparing] = useState(false), [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(""), [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [signatures, setSignatures] = useState<BookendSignature[]>([]), [signature, setSignature] = useState("");
  const [name, setName] = useState(""), [saveSource, setSaveSource] = useState("");
  const [signatureError, setSignatureError] = useState<string | null>(null);
  const generation = useRef(0), player = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    generation.current++; setData(null); setVariants([]); setActive(""); setBusy(false); setError(null); setStatus("");
    setSignature(""); setName(""); setSaveSource(""); setSignatureError(null);
    setOpeningLook("authority"); setClosingLook("authority"); setOpeningShot("keep"); setClosingShot("keep"); setMatch(true); setReplaceOpening(false); setReplaceClosing(false); setComparing(false);
    return () => { generation.current++; };
  }, [jobId, plan]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    getBookendAuditions(jobId).then(result => { if (alive) { setData(result); setFirst(result.default_first_word ?? 0); setLast(result.default_last_word ?? 0); } })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : "Could not load bookend words."); });
    return () => { alive = false; };
  }, [jobId, plan, open]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    listBookendSignatures().then(result => { if (alive) { setSignatures(result.signatures); setSignatureError(null); } })
      .catch(e => { if (alive) setSignatureError(e instanceof Error ? e.message : "Could not load bookend signatures."); });
    return () => { alive = false; };
  }, [open, jobId]);
  const saved = signatures.find(item => item.id === signature);
  const toSave = variants.find(item => item.key === (saveSource || active));
  function loadSignature(id: string) {
    setSignature(id); setReplaceOpening(false); setReplaceClosing(false);
    const item = signatures.find(item => item.id === id);
    if (!item || !data) return;
    setOpeningLook(item.opening_look); setClosingLook(item.closing_look);
    setOpeningShot(item.opening_shot); setClosingShot(item.closing_shot); setMatch(item.match_captions);
    const words = bookendDefaults(data, item);
    if (words) { setLast(words.last_word); setFirst(words.first_word); }
  }
  async function manage(remove: boolean) {
    if (busy || (!remove && (!toSave || !canChoose(toSave.choice) || !name.trim()))) return;
    const current = generation.current;
    setBusy(true); setSignatureError(null);
    try {
      if (remove && saved) {
        await deleteBookendSignature(saved.id);
        if (generation.current !== current) return;
        setSignatures(items => items.filter(item => item.id !== saved.id)); setSignature("");
        setStatus("Saved signature removed. Your projects and rendered drafts stay intact.");
      } else if (toSave) {
        const result = await saveBookendSignature(jobId, name.trim(), toSave.choice.revision, toSave.preview.bookend_id);
        if (generation.current !== current) return;
        setSignatures(items => [...items, result]); setName("");
        setStatus(`Bookend signature saved: ${result.name}. Available on other recordings.`);
      }
    } catch (e) { if (generation.current === current) setSignatureError(e instanceof Error ? e.message : "Could not update bookend signatures."); }
    finally { if (generation.current === current) setBusy(false); }
  }
  const choice: BookendChoice | null = data ? {revision: data.revision, last_word: last, first_word: first,
    opening_look: openingLook, closing_look: closingLook, opening_shot: openingShot, closing_shot: closingShot,
    match_captions: match, replace_opening: replaceOpening, replace_closing: replaceClosing} : null;
  const choiceKey = JSON.stringify(choice), opening = data?.opening.endings.find(window => window.last_word === last), closing = data?.closing.starts.find(window => window.first_word === first);
  const preview = variants.find(variant => variant.key === active);
  useEffect(() => { player.current?.pause(); setComparing(false); setActive(variants.find(variant => JSON.stringify(variant.settings) === choiceKey)?.key ?? ""); }, [choiceKey]);
  useEffect(() => { const mounted = player.current; setReady(!!mounted && mounted.readyState >= 1); return () => { mounted?.pause(); }; }, [active, comparing, open]);
  async function render(all: boolean) {
    if (!choice || !data || busy) return;
    const current = generation.current, options = all ? (["authority", "energy", "cinema"] as const).map(look => ({...choice, opening_look: look, closing_look: look})) : [choice];
    setBusy(true); setError(null); setComparing(false); player.current?.pause();
    let selected = "";
    try {
      for (let index = 0; index < options.length; index++) {
        const settings = options[index];
        setStatus(`Rendering bookend pair ${index + 1} of ${options.length} with its soundtrack…`);
        const result = signature && !all
          ? await previewBookendSignature(jobId, {revision: settings.revision, last_word: settings.last_word, first_word: settings.first_word, replace_opening: settings.replace_opening, replace_closing: settings.replace_closing}, signature)
          : await previewBookend(jobId, settings);
        if (current !== generation.current) return;
        const variant: Audition = {key: result.preview_id, label: `${result.signature ? result.signature.name + " · " : ""}${data.opening.profiles[result.settings.opening_look].label} → ${data.closing.profiles[result.settings.closing_look].label} · ${result.opening.quote} / ${result.closing.quote}`,
          settings: result.settings, preview: result, reviewedOpening: false, reviewedClosing: false, choice: {revision: result.revision, preview_id: result.preview_id},
          warnings: [...(result.sound?.problems ?? []), ...(result.music_note ? [result.music_note] : [])]};
        setVariants(previous => [...previous.filter(item => item.key !== variant.key), variant].slice(-6));
        if ((settings.opening_look === choice.opening_look && settings.closing_look === choice.closing_look) || !selected) selected = variant.key;
        setActive(selected);
      }
      setStatus("Bookend auditions ready. Review both ends and the full story before choosing.");
    } catch (e) { if (current === generation.current) { setError(e instanceof Error ? e.message : "Could not render this pair."); setStatus(""); } }
    finally { if (current === generation.current) setBusy(false); }
  }
  const canChoose = (winner: CompleteWinner) => { const variant = variants.find(item => item.key === winner.preview_id); return !!variant?.reviewedOpening && !!variant.reviewedClosing; };
  async function choose(winner: CompleteWinner) {
    if (busy || !canChoose(winner)) return;
    const current = generation.current;
    setBusy(true); setError(null); player.current?.pause();
    try { const result = await saveComplete(jobId, winner); if (current === generation.current) onEdited(result); }
    catch (e) { if (current === generation.current) setError(e instanceof Error ? e.message : "Could not save this pair."); }
    finally { if (current === generation.current) setBusy(false); }
  }
  const overlap = !!opening && !!closing && (last >= first || opening.end > closing.start);
  const blocked = !opening || !closing || overlap || (opening.pinned && !replaceOpening) || (closing.pinned && !replaceClosing) || (openingShot === "speaker" && !opening.has_speaker) || (closingShot === "speaker" && !closing.has_speaker);
  return <details className="story-composer bookend-auditions" onToggle={event => {
    setOpen(event.currentTarget.open); if (!event.currentTarget.open) { player.current?.pause(); setComparing(false); }
  }}><summary>Bookend auditions · shape the first and final impression</summary>
    {open && <section aria-label="Bookend auditions">
      <h3>Make the opening and ending belong together.</h3>
      <p>Pair two treatments on your actual spoken words. Compare the full story with its soundtrack.</p>
      {data && !data.eligible && <Notice>Compose a 3–60 second story in Shorts first.</Notice>}
      {data?.eligible && data.default_last_word === null && <Notice>This story needs separate timed opening and closing phrases. Check the transcript in Captions first.</Notice>}
      {!data && !error && <p role="status">Finding your bookend words…</p>}
      {data && data.default_last_word !== null && <>
        <fieldset disabled={busy}>
          <label className="label">Bookend signature<select aria-label="Bookend signature" value={signature} onChange={event => loadSignature(event.target.value)}><option value="">Make a new pair</option>{signatures.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          {saved && <div className="opening-signature-summary"><p className="hint">{saved.opening_words} opening words and {saved.closing_words} closing words preferred. Counts are a starting point: adjust each phrase to complete the thought. Uses this recording’s words, shots and soundtrack; speaker choices need footage.</p><button className="btn" onClick={() => void manage(true)}>Remove saved bookend signature</button></div>}
          <div className="bookend-fields">
            <div><label className="label">Opening words<select aria-label="Bookend opening words" value={last} onChange={event => setLast(Number(event.target.value))}>{data.opening.endings.map(window => <option key={window.last_word} value={window.last_word}>{window.quote} · {(window.end - window.start).toFixed(1)}s</option>)}</select></label>
              <p className="closing-quote">“{opening?.quote}”</p>
              <label className="label">Opening treatment<select aria-label="Bookend opening treatment" value={openingLook} onChange={event => { setSignature(""); setOpeningLook(event.target.value as BookendChoice["opening_look"]); }}>{Object.entries(data.opening.profiles).map(([key, profile]) => <option key={key} value={key}>{profile.label}</option>)}</select></label>
              <label className="label">Opening shot<select aria-label="Bookend opening shot" value={openingShot} onChange={event => { setSignature(""); setOpeningShot(event.target.value as BookendChoice["opening_shot"]); }}><option value="keep">Keep current shots</option><option value="speaker" disabled={!opening?.has_speaker}>Speaker</option></select></label>
              {opening?.pinned && <label className="check"><input type="checkbox" checked={replaceOpening} onChange={event => setReplaceOpening(event.target.checked)} />Allow replacing my pinned opening text</label>}
            </div>
            <div><label className="label">Closing words<select aria-label="Bookend closing words" value={first} onChange={event => setFirst(Number(event.target.value))}>{data.closing.starts.map(window => <option key={window.first_word} value={window.first_word}>{window.quote} · {(window.end - window.start).toFixed(1)}s</option>)}</select></label>
              <p className="closing-quote">“{closing?.quote}”</p>
              <label className="label">Closing treatment<select aria-label="Bookend closing treatment" value={closingLook} onChange={event => { setSignature(""); setClosingLook(event.target.value as BookendChoice["closing_look"]); }}>{Object.entries(data.closing.profiles).map(([key, profile]) => <option key={key} value={key}>{profile.label}</option>)}</select></label>
              <label className="label">Closing shot<select aria-label="Bookend closing shot" value={closingShot} onChange={event => { setSignature(""); setClosingShot(event.target.value as BookendChoice["closing_shot"]); }}><option value="keep">Keep current shots</option><option value="speaker" disabled={!closing?.has_speaker}>Speaker</option></select></label>
              {closing?.pinned && <label className="check"><input type="checkbox" checked={replaceClosing} onChange={event => setReplaceClosing(event.target.checked)} />Allow replacing my pinned closing text</label>}
            </div>
          </div>
          {overlap && <Notice>Opening and closing overlap. Choose shorter, separate phrases.</Notice>}
          <p className="hint">Current pictures, the middle of the story and outro cards carry over. These treatments do not change the recording.</p>
          <label className="check"><input type="checkbox" checked={match} onChange={event => { setSignature(""); setMatch(event.target.checked); }} />Match captions inside both bookends</label>
          <div className="story-actions"><button className="btn" disabled={blocked} onClick={() => void render(false)}>{saved ? "Preview saved bookend signature" : "Preview selected pair"}</button><button className="btn btn-primary" disabled={blocked || !!signature} onClick={() => void render(true)}>Render three coordinated pairs</button></div>
        </fieldset>
        {preview && !comparing && <div className="story-preview"><h4>{preview.label}</h4><video ref={player} controls playsInline preload="metadata" src={preview.preview.url} aria-label="Bookend audition preview" onLoadedMetadata={() => setReady(true)} onError={() => { setReady(false); setError("This draft could not load. Render the pair again."); }} />
          <div className="closing-watch-actions"><button className="btn" disabled={!ready} onClick={() => { const video = player.current; if (video) { video.pause(); video.currentTime = Math.max(0, preview.preview.opening.start - .5); } }}>Review the opening</button><button className="btn" disabled={!ready} onClick={() => { const video = player.current; if (video) { video.pause(); video.currentTime = Math.max(0, Math.min(preview.preview.closing.start - 1, video.duration)); } }}>Review the ending</button></div>
          <p className="hint">{preview.preview.note} {preview.preview.music_note}</p>
          {!!preview.preview.sound?.problems.length && <Notice>{preview.preview.sound.problems.join(" ")}</Notice>}
          <button className="btn btn-primary" disabled={busy || !canChoose(preview.choice)} onClick={() => void choose(preview.choice)}>Use this pair</button></div>}
        <div className="closing-reviews">{variants.map(variant => <div className="story-block" key={variant.key}>
          <button className="btn" disabled={busy} onClick={() => { player.current?.pause(); setComparing(false); setActive(variant.key); }}>Watch {variant.label}</button>
          <p className="hint">“{variant.preview.opening.quote}” → “{variant.preview.closing.quote}”</p>
          <label className="check"><input type="checkbox" disabled={busy} aria-label={`Reviewed opening ${variant.label}`} checked={variant.reviewedOpening} onChange={event => { const reviewedOpening = event.target.checked; setVariants(previous => previous.map(item => item.key === variant.key ? {...item, reviewedOpening} : item)); }} />I reviewed the opening and its return to the story</label>
          <label className="check"><input type="checkbox" disabled={busy} aria-label={`Reviewed closing ${variant.label}`} checked={variant.reviewedClosing} onChange={event => { const reviewedClosing = event.target.checked; setVariants(previous => previous.map(item => item.key === variant.key ? {...item, reviewedClosing} : item)); }} />I reviewed the lead-in and ending in the full story</label>
        </div>)}</div>
        {!!variants.length && <fieldset disabled={busy} className="opening-signature-save"><legend>Keep a reviewed pair as your signature</legend>
          <label className="label">Pair to save<select aria-label="Pair to save" value={saveSource || active} onChange={event => setSaveSource(event.target.value)}><option value="">Choose a rendered pair</option>{variants.map(item => <option key={item.key} value={item.key}>{item.label}{canChoose(item.choice) ? " · reviewed" : " · review both ends"}</option>)}</select></label>
          <label className="label">Signature name<input aria-label="Bookend signature name" maxLength={60} value={name} onChange={event => setName(event.target.value)} /></label>
          <button className="btn" disabled={!toSave || !canChoose(toSave.choice) || !name.trim()} onClick={() => void manage(false)}>Save reviewed bookend signature</button>
          <p className="hint">Save before applying. Keeps the reviewed pair’s recipe, without old words, media or sound. New recordings need fresh previews and pinned-text replacement choices.</p>
        </fieldset>}
        {variants.length >= 2 && <><button className="btn" disabled={busy} onClick={() => { player.current?.pause(); setComparing(value => !value); }}>{comparing ? "Close bookend comparison" : "Compare bookend auditions"}</button>{comparing && <StoryComparison complete reviewOpening reviewAt={Math.max(0, Math.min(...variants.map(variant => variant.preview.closing.start)) - 1)} variants={variants} busy={busy} canChoose={canChoose} onChoose={winner => void choose(winner)} />}</>}
      </>}
      {status && <p role="status">{status}</p>}{error && <Notice tone="error">{error}</Notice>}{signatureError && <Notice tone="error">{signatureError}</Notice>}
      <p className="hint">Previews leave the project intact. Choosing saves both ends of the exact reviewed preview as one undoable edit. Timing, music and the rest of your story carry over. Update video creates the export. The last six drafts remain available during this session.</p>
    </section>}
  </details>;
}
