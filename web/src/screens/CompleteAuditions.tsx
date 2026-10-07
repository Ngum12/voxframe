import { useEffect, useRef, useState } from "react";
import { getCompleteAuditions, previewComplete, saveComplete, listMusic,
  type CompleteChoice, type CompleteControls, type CompletePreview, type CompleteWinner,
  type AudioMix, type MusicTrack, type ScenePlan, type PlanEditResult } from "../api";
import { StoryComparison, type StoryVariant } from "./StoryComparison";
import { Notice } from "../components";

type Audition = StoryVariant<CompleteWinner> & { preview: CompletePreview; settings: CompleteChoice };

export function CompleteAuditions({ jobId, plan, onEdited }: {
  jobId: string; plan: ScenePlan; onEdited: (result: PlanEditResult) => void;
}) {
  const [data, setData] = useState<CompleteControls | null>(null);
  const [look, setLook] = useState<CompleteChoice["look"]>(null), [match, setMatch] = useState(false);
  const [music, setMusic] = useState<CompleteChoice["music_source"]>("project");
  const [track, setTrack] = useState(""), [query, setQuery] = useState("");
  const [selectedTrack, setSelectedTrack] = useState<MusicTrack | null>(null);
  const [tracks, setTracks] = useState<MusicTrack[]>([]), [trackBusy, setTrackBusy] = useState(false);
  const [trackError, setTrackError] = useState<string | null>(null);
  const [mix, setMix] = useState<AudioMix>(plan.audio_mix);
  const [variants, setVariants] = useState<Audition[]>([]), [active, setActive] = useState("");
  const [comparing, setComparing] = useState(false), [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const generation = useRef(0), player = useRef<HTMLVideoElement>(null);
  const duration = plan.total_frames / plan.fps, eligible = duration >= 3 && duration <= 60 + 1e-7;
  const choice: CompleteChoice | null = data ? {revision: data.revision, look, match_captions: !!look && match,
    music_source: music, music_library_id: music === "library" ? track || null : null, mix} : null;
  const choiceKey = JSON.stringify(choice);
  const preview = variants.find(v => v.key === active);
  useEffect(() => {
    let live = true;
    generation.current++; setData(null); setVariants([]); setActive(""); setBusy(null); setError(null);
    setLook(null); setMatch(false); setMusic("project"); setMix(plan.audio_mix); setComparing(false);
    setTrack(""); setQuery(""); setTracks([]); setSelectedTrack(null);
    getCompleteAuditions(jobId).then(result => { if (live) { setData(result); setMix(result.mix); } })
      .catch(e => { if (live) setError(e.message); });
    return () => { live = false; generation.current++; };
  }, [jobId, plan]);
  useEffect(() => {
    let live = true;
    if (!open || music !== "library") return;
    setTrackBusy(true); setTrackError(null);
    const timer = window.setTimeout(() => {
      listMusic(query).then(result => { if (live) setTracks(result.tracks); })
        .catch(e => { if (live) setTrackError(e.message); })
        .finally(() => { if (live) setTrackBusy(false); });
    }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [query, music, open, jobId]);
  useEffect(() => {
    player.current?.pause(); setActive(variants.find(v => JSON.stringify(v.settings) === choiceKey)?.key ?? "");
    setComparing(false);
  }, [choiceKey]);
  useEffect(() => {
    const mounted = player.current;
    return () => { mounted?.pause(); };
  }, [active, comparing]);
  function profile(next: CompleteChoice["look"]) {
    setLook(next); setMatch(!!next);
    if (next && data) setMix({...mix, music_arc: data.profiles[next].arc, music_db: data.profiles[next].music_db});
  }
  async function render() {
    if (!choice) return;
    const current = generation.current, settings = choice;
    setBusy("preview"); setError(null); player.current?.pause(); setComparing(false);
    try {
      const rendered = await previewComplete(jobId, settings);
      if (current !== generation.current) return;
      const trackLabel = settings.music_source === "none" ? "No music" : settings.music_source === "library"
        ? selectedTrack?.title ?? "Saved track" : data?.project_music ?? "Project music";
      const label = `${settings.look ? data?.profiles[settings.look].label : "Current edit"} · ${trackLabel} · ${settings.mix.music_arc} · ${settings.mix.music_db} dB`;
      const variant: Audition = {key: rendered.preview_id, label, settings, preview: rendered,
        warnings: [...(rendered.sound?.problems ?? []), ...(rendered.music_note ? [rendered.music_note] : [])],
        choice: {revision: rendered.revision, preview_id: rendered.preview_id}};
      setVariants(previous => [...previous.filter(v => v.key !== variant.key), variant].slice(-6));
      setActive(variant.key);
    } catch (e) { if (current === generation.current) setError(e instanceof Error ? e.message : "The complete audition could not be rendered."); }
    finally { if (current === generation.current) setBusy(null); }
  }
  async function choose(winner: CompleteWinner) {
    const current = generation.current;
    setBusy("save"); setError(null); player.current?.pause();
    try {
      const result = await saveComplete(jobId, winner);
      if (current === generation.current) onEdited(result);
    } catch (e) { if (current === generation.current) setError(e instanceof Error ? e.message : "The audition could not be chosen."); }
    finally { if (current === generation.current) setBusy(null); }
  }
  return <details className="story-composer complete-auditions" onToggle={e => {
    setOpen(e.currentTarget.open); if (!e.currentTarget.open) { player.current?.pause(); setComparing(false); }
  }}>
    <summary>Complete auditions · picture, captions and sound</summary>
    <section aria-label="Complete auditions">
      <h3>Hear the whole idea.</h3>
      <p>Audition your entire current story, including the soundtrack. Compare two treatments on one playhead and save the one you prefer.</p>
      {!eligible && <Notice>Compose a 3–60 second story in Shorts first.</Notice>}
      {error && <Notice tone="error">{error}</Notice>}
      {!data && !error && <p role="status">Loading audition settings…</p>}
      {data && eligible && <>
        <fieldset disabled={!!busy}>
          <legend>Picture and captions</legend>
          <div className="story-look-options">
            <button type="button" className="btn" aria-pressed={!look} onClick={() => profile(null)}>Current picture edit</button>
            {Object.entries(data.profiles).map(([key, option]) => <button type="button" className="story-look" key={key}
              aria-pressed={look === key} onClick={() => profile(key as CompleteChoice["look"])}>
              <strong>{option.label}</strong><span>{option.arc} music arc · {option.music_db} dB starting level</span>
            </button>)}
          </div>
          <label className="check"><input type="checkbox" disabled={!look} checked={!!look && match} onChange={e => setMatch(e.target.checked)} />Match audition captions</label>
          <p className="hint">Pinned shots and text remain yours. Direction buttons suggest a music arc and level; adjust them below.</p>
          <label>Music source<select aria-label="Audition music source" value={music} onChange={e => setMusic(e.target.value as CompleteChoice["music_source"])}>
            <option value="project">{data.project_music}</option><option value="library">Saved track</option><option value="none">No added music</option></select></label>
          {music === "project" && data.credit && <p className="hint">{data.credit}</p>}
          {music === "library" && <>
            <label>Find saved music<input aria-label="Find audition music" value={query} onChange={e => setQuery(e.target.value)} /></label>
            {trackError && <Notice tone="error">{trackError}</Notice>}
            {trackBusy && <p role="status">Finding saved tracks…</p>}
            <label>Saved track<select aria-label="Audition saved track" value={track} onChange={e => { setTrack(e.target.value); setSelectedTrack(tracks.find(t => t.id === e.target.value) ?? null); }}>
              <option value="">Choose a track</option>{selectedTrack && !tracks.some(t => t.id === selectedTrack.id) && <option value={selectedTrack.id}>{selectedTrack.title}</option>}{tracks.map(t => <option value={t.id} key={t.id}>{t.title} · {t.mood}</option>)}
            </select></label>
            {!tracks.length && !trackBusy && <p className="hint">Save or import music in Library first. Search shows up to 60 tracks.</p>}
            {selectedTrack?.credit && <p className="hint">{selectedTrack.credit}</p>}
          </>}
          <div className="story-fields">
            <label>Music arc<select aria-label="Audition music arc" value={mix.music_arc} disabled={music === "none"} onChange={e => setMix({...mix, music_arc: e.target.value as AudioMix["music_arc"]})}>
              <option value="steady">Steady</option><option value="rise">Rise</option><option value="punch">Punch</option></select></label>
            <label>Music level · {mix.music_db} dB<input aria-label="Audition music level" type="range" min={-30} max={12} step={1} value={mix.music_db} disabled={music === "none"} onChange={e => setMix({...mix, music_db: Number(e.target.value)})} /></label>
            <label>Under speech · {mix.speech_margin_db} dB<input aria-label="Audition speech margin" type="range" min={3} max={30} step={1} value={mix.speech_margin_db} disabled={music === "none"} onChange={e => setMix({...mix, speech_margin_db: Number(e.target.value)})} /></label>
          </div>
          <label className="check"><input type="checkbox" checked={mix.voice_polish} onChange={e => setMix({...mix, voice_polish: e.target.checked})} />Audition voice polish</label>
        </fieldset>
        <button type="button" className="btn btn-primary" disabled={!!busy || (music === "library" && !track)} onClick={() => void render()}>Render complete audition</button>
        {busy && <p role="status">{busy === "preview" ? "Rendering picture, captions and soundtrack…" : "Saving the previewed treatment…"}</p>}
        {preview && !comparing && <div className="story-preview">
          <p><strong>{preview.label}</strong></p>
          <video ref={player} controls preload="metadata" src={preview.preview.url} aria-label="Complete audition preview" />
          <p>{preview.preview.note}</p>
          {preview.preview.music_note && <p className="hint">{preview.preview.music_note}</p>}
          {preview.preview.sound && <p className="hint">{preview.preview.sound.passed ? "Sound checks passed." : preview.preview.sound.problems.join(" ")}</p>}
          <button type="button" className="btn btn-primary" disabled={!!busy} onClick={() => void choose(preview.choice)}>Use this complete audition</button>
        </div>}
        {variants.length >= 2 && <>
          <button type="button" className="btn" disabled={!!busy} onClick={() => { player.current?.pause(); setComparing(!comparing); }}>{comparing ? "Close complete comparison" : "Compare complete auditions"}</button>
          {comparing && <StoryComparison complete variants={variants} busy={!!busy} onChoose={winner => void choose(winner)} />}
        </>}
        <p className="hint">The last six auditions stay available for comparison in this session. Preview leaves the project untouched. Choosing saves the previewed visuals, captions, track and mix as one undoable edit. Update video creates the final export.</p>
      </>}
    </section>
  </details>;
}
