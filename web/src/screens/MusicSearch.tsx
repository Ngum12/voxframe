import { useEffect, useRef, useState } from "react";
import { getSettings, saveSettings, searchMusic, saveOnlineMusic, type MusicTrack, type OnlineMusicResult } from "../api";
import { Notice } from "../components";

export function MusicSearch({onChoose, onSaved, onAudition}: {
  onChoose?: (track: MusicTrack) => void; onSaved: () => void;
  onAudition?: (token: string) => Promise<void>;
}) {
  const [enabled, setEnabled] = useState(false);
  const [shareAlike, setShareAlike] = useState(false);
  const [ready, setReady] = useState(false);
  const [query, setQuery] = useState("");
  const [mood, setMood] = useState("");
  const [minimum, setMinimum] = useState(0);
  const [maximum, setMaximum] = useState(7200);
  const [instrumental, setInstrumental] = useState(false);
  const [page, setPage] = useState(1);
  const [results, setResults] = useState<OnlineMusicResult[] | null>(null);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [preview, setPreview] = useState<{token: string; url: string} | null>(null);
  const player = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    let active = true;
    getSettings().then(settings => { if (active) {
      setEnabled(settings.music_search.enabled); setShareAlike(settings.music_search.share_alike);
      setReady(true);
    }}).catch(error => { if (active) setError((error as Error).message); });
    return () => { active = false; };
  }, []);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview.url); }, [preview]);
  const action = async (work: () => Promise<void>) => {
    setBusy(true); setError(null); setMessage(null);
    try { await work(); } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  };
  const preferences = (allow: boolean, sa: boolean) => {
    const before = {enabled, shareAlike};
    setEnabled(allow); setShareAlike(sa);
    void action(async () => {
      try { await saveSettings({music_search_consent: allow, music_share_alike: sa}); }
      catch (error) { setEnabled(before.enabled); setShareAlike(before.shareAlike); throw error; }
      setResults(null); setMore(false); setPreview(null);
    });
  };
  const find = (next: number) => void action(async () => {
    player.current?.pause(); setPreview(null);
    const found = await searchMusic(query, next, mood, minimum, maximum, instrumental);
    setResults(found.results); setMore(found.has_more); setPage(next);
  });
  const hear = (result: OnlineMusicResult) => void action(async () => {
    player.current?.pause();
    const response = await fetch(`/api/music-search/${result.token}/preview`, {credentials: "same-origin"});
    if (!response.ok) {
      const body = await response.json(); throw new Error(body.detail || "Preview unavailable.");
    }
    setPreview({token: result.token, url: URL.createObjectURL(await response.blob())});
  });
  const save = (result: OnlineMusicResult, choose: boolean) => void action(async () => {
    player.current?.pause();
    const saved = await saveOnlineMusic(result.token);
    onSaved();
    if (choose) onChoose?.(saved.track);
    setMessage(saved.already_there ? "Existing audio reused with credit from the selected source." : `Saved ${result.title} with its credit and license.`);
  });
  const filtersChanged = () => { setResults(null); setPreview(null); setMore(false); };
  return <details className="music-discovery">
    <summary>Discover openly licensed music</summary>
    <p className="muted">Search Openverse when you choose. Only the search words you type are sent. Previewing downloads audio temporarily; saving keeps a local copy.</p>
    {error && <Notice tone="error">{error}</Notice>}
    {message && <Notice tone="info">{message}</Notice>}
    <label className="toggle"><input type="checkbox" checked={enabled} disabled={!ready || busy} onChange={event => preferences(event.target.checked, shareAlike)} /> Allow online music search</label>
    {enabled && <>
      <label className="toggle"><input type="checkbox" checked={shareAlike} disabled={busy} onChange={event => preferences(enabled, event.target.checked)} /> Include ShareAlike music (your video may need the same license)</label>
      <p className="hint">NonCommercial and NoDerivatives tracks are excluded. Some openly licensed tracks are registered with YouTube Content ID and may still receive a claim.</p>
      <form onSubmit={event => {event.preventDefault(); find(1);}} className="music-import">
        <label className="field">Find music<input required maxLength={120} value={query} disabled={busy} placeholder="For example: gentle piano" onChange={event => {setQuery(event.target.value); filtersChanged();}} /></label>
        <label className="field">Online mood<select aria-label="Online mood" value={mood} disabled={busy} onChange={event => {setMood(event.target.value); filtersChanged();}}><option value="">Any mood</option>{["calm", "energetic", "cinematic", "reflective", "inspiring"].map(m => <option key={m}>{m}</option>)}</select></label>
        <div className="music-filters">
          <label className="field">Minimum seconds<input type="number" min={0} max={maximum} value={minimum} disabled={busy} onChange={event => {setMinimum(Number(event.target.value)); filtersChanged();}} /></label>
          <label className="field">Maximum seconds<input type="number" min={Math.max(1,minimum)} max={7200} value={maximum} disabled={busy} onChange={event => {setMaximum(Number(event.target.value)); filtersChanged();}} /></label>
        </div>
        <label className="toggle"><input type="checkbox" checked={instrumental} disabled={busy} onChange={event => {setInstrumental(event.target.checked); filtersChanged();}} /> Tagged instrumental only</label>
        <p className="hint">Length, mood and instrumental filters use the source metadata on each page. Tags may be missing; try another page or fewer filters.</p>
        <button className="btn" disabled={busy || !query.trim()}>{busy ? "Working…" : "Search Openverse"}</button>
      </form>
      {results?.length === 0 && <p>No matching tracks on this page. Try fewer filters or the next page.</p>}
      {results?.map(result => <article className="music-track" key={result.token}>
        <strong>{result.title}</strong><p className="muted">{result.creator} · {result.license} · {Math.round(result.seconds)} seconds</p>
        <p><a href={result.license_url} target="_blank" rel="noreferrer">License terms</a>{" · "}<a href={`https://openverse.org/audio/${result.id}`} target="_blank" rel="noreferrer">Openverse source</a>{result.source_url && <> · <a href={result.source_url} target="_blank" rel="noreferrer">Original source</a></>}</p>
        <div className="music-track-actions">
          <button className="btn btn-quiet" disabled={busy} onClick={() => hear(result)}>Preview track</button>
          {onAudition && <button className="btn btn-quiet" disabled={busy} onClick={() => void action(async () => {player.current?.pause(); await onAudition(result.token); setMessage(`Auditioning ${result.title}. Choose Use this track, then Apply to save it to your video.`);})}>Hear under my voice</button>}
          <button className="btn" disabled={busy} onClick={() => save(result, false)}>Save to library</button>
          {onChoose && <button className="btn" disabled={busy} onClick={() => save(result, true)}>Use this track</button>}
        </div>
        {preview?.token === result.token && <audio ref={player} controls autoPlay src={preview.url} aria-label={`Online preview: ${result.title}`} />}
      </article>)}
      {results && <div className="music-track-actions"><button className="btn btn-quiet" disabled={busy || page === 1} onClick={() => find(page - 1)}>Previous search page</button><span>Page {page}</span><button className="btn btn-quiet" disabled={busy || !more} onClick={() => find(page + 1)}>Next search page</button></div>}
    </>}
  </details>;
}
