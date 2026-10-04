import { useEffect, useId, useState } from "react";
import { hideMusic, importMusic, listMusic, updateMusic, uploadAudio, type MusicTrack } from "../api";
import { Notice } from "../components";
import { MusicSearch } from "./MusicSearch";

const MOODS = ["other", "calm", "energetic", "cinematic", "reflective", "inspiring"];

function TrackRow({ track, onChoose, onChanged, onError }: {
  track: MusicTrack; onChoose?: (track: MusicTrack) => void;
  onChanged: () => void; onError: (message: string) => void;
}) {
  const [edit, setEdit] = useState<MusicTrack | null>(null);
  const [busy, setBusy] = useState(false);
  const act = async (operation: () => Promise<unknown>) => {
    setBusy(true);
    try { await operation(); setEdit(null); onChanged(); }
    catch (error) { onError((error as Error).message); }
    finally { setBusy(false); }
  };
  return <article className="music-track">
    <div><strong>{track.title}</strong><span className="muted"> · {track.mood} · {Math.round(track.seconds)} seconds</span></div>
    <p className="muted">{track.credit || `${track.title} (supplied by the user)`}</p>
    <audio controls preload="none" src={`/api/music-library/${track.id}/audio`} aria-label={`Listen to ${track.title}`} />
    <div className="music-track-actions">
      {onChoose && <button className="btn" onClick={(event) => {
        event.currentTarget.closest(".music-library")?.querySelectorAll("audio").forEach(audio => audio.pause());
        onChoose(track);
      }}>Audition under my voice</button>}
      <button className="btn btn-quiet" disabled={busy} onClick={() => setEdit({...track})}>Edit details</button>
      <button className="btn btn-quiet" disabled={busy} onClick={() => void act(() => hideMusic(track.id))}>Hide from library</button>
    </div>
    {edit && <form onSubmit={(event) => { event.preventDefault(); void act(() => updateMusic(edit)); }}>
      <label className="field">Title<input required maxLength={120} value={edit.title} onChange={(event) => setEdit({...edit, title: event.target.value})} /></label>
      <label className="field">Credit<input maxLength={300} value={edit.credit} onChange={(event) => setEdit({...edit, credit: event.target.value})} /></label>
      <label className="field">Mood<select aria-label="Mood" value={edit.mood} onChange={(event) => setEdit({...edit, mood: event.target.value})}>{MOODS.map(mood => <option key={mood}>{mood}</option>)}</select></label>
      <button className="btn" disabled={busy}>Save details</button>{" "}<button type="button" className="btn btn-quiet" disabled={busy} onClick={() => setEdit(null)}>Cancel</button>
    </form>}
  </article>;
}

export function MusicLibraryPanel({ onChoose, onAudition }: {
  onChoose?: (track: MusicTrack) => void; onAudition?: (token: string) => Promise<void>;
}) {
  const id = useId();
  const [q, setQuery] = useState("");
  const [mood, setMood] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{tracks: MusicTrack[]; total: number} | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [credit, setCredit] = useState("");
  const [importMood, setImportMood] = useState("other");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const reload = () => setRefresh(value => value + 1);
  useEffect(() => {
    let active = true;
    setLoading(true);
    const timer = window.setTimeout(() => {
      void listMusic(q, mood, offset).then(result => {
        if (active) { setPage(result); setError(null); }
      }).catch(error => { if (active) setError((error as Error).message); })
        .finally(() => { if (active) setLoading(false); });
    }, 150);
    return () => { active = false; window.clearTimeout(timer); };
  }, [q, mood, offset, refresh]);
  const add = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!file) return;
    if (file.size > 200 * 1024 * 1024) {
      setError("Choose a music track up to 200 MB."); return;
    }
    const form = event.currentTarget;
    setBusy(true); setError(null); setMessage(null);
    try {
      const upload = await uploadAudio(file);
      const result = await importMusic(upload.upload_id, title, credit, importMood);
      setMessage(result.already_there ? "This track is already saved. Its existing details were kept." : "Track saved. You can use it in any project.");
      form.reset();
      setFile(null); setTitle(""); setCredit(""); setImportMood("other");
      setQuery(""); setMood(""); setOffset(0); reload();
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  };
  return <section className="music-library" aria-labelledby={`${id}-heading`}>
    <h2 id={`${id}-heading`}>Your music library</h2>
    <p className="muted">Keep your tracks on this computer and reuse them with their credits. Hiding a track keeps saved projects working. Editing details applies to future selections.</p>
    {error && <Notice tone="error">{error}</Notice>}
    {message && <Notice tone="info">{message}</Notice>}
    <details>
      <summary>Save a music track</summary>
      <form onSubmit={event => void add(event)} className="music-import">
        <label className="field">Music file (up to 200 MB)<input type="file" required accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.opus,.aac,.wma" disabled={busy} onChange={event => {
          const chosen = event.target.files?.[0] ?? null;
          setFile(chosen);
          if (chosen) setTitle(chosen.name.replace(/\.[^.]+$/, "").slice(0, 120));
        }} /></label>
        <label className="field">Track title<input required maxLength={120} value={title} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
        <label className="field">Track credit<input maxLength={300} value={credit} disabled={busy} placeholder="Title, creator and license, if applicable" onChange={event => setCredit(event.target.value)} /></label>
        <label className="field">Track mood<select aria-label="Track mood" value={importMood} disabled={busy} onChange={event => setImportMood(event.target.value)}>{MOODS.map(mood => <option key={mood}>{mood}</option>)}</select></label>
        <button className="btn" disabled={busy || !file}>{busy ? "Saving track…" : "Save track"}</button>
      </form>
    </details>
    <MusicSearch onChoose={onChoose} onAudition={onAudition} onSaved={() => {
      setQuery(""); setMood(""); setOffset(0); reload();
    }} />
    <div className="music-filters">
      <label className="field">Search music<input type="search" value={q} maxLength={120} onChange={event => {setQuery(event.target.value); setOffset(0);}} /></label>
      <label className="field">Filter by mood<select aria-label="Filter by mood" value={mood} onChange={event => {setMood(event.target.value); setOffset(0);}}><option value="">All moods</option>{MOODS.map(mood => <option key={mood}>{mood}</option>)}</select></label>
    </div>
    {loading && <p role="status">Loading tracks…</p>}
    {!loading && page?.total === 0 && <p className="muted">No tracks here yet. Save a track above, or change your search.</p>}
    {!loading && page?.tracks.map(track => <TrackRow key={`${track.id}-${refresh}`} track={track} onChoose={onChoose} onChanged={reload} onError={setError} />)}
    {page && page.total > 60 && <div className="music-track-actions">
      <button className="btn btn-quiet" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 60))}>Previous tracks</button>
      <span>{offset + 1}–{Math.min(offset + 60, page.total)} of {page.total}</span>
      <button className="btn btn-quiet" disabled={loading || offset + 60 >= page.total} onClick={() => setOffset(offset + 60)}>Next tracks</button>
    </div>}
    {onChoose && <p className="hint">Audition plays a draft mix under your voice. Apply fits the music to the full video.</p>}
  </section>;
}
