import { useEffect, useState } from "react";
import { getAudioTracks, audioTrackUrl, type AudioTrackState } from "../api";
import { Notice } from "../components";

export function AudioTracks({jobId, updating = false}: {jobId: string; updating?: boolean}) {
  const [state, setState] = useState<AudioTrackState | null>(null), [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let live = true; setState(null); setError(null);
    if (updating) return;
    void getAudioTracks(jobId).then(value => { if (live) setState(value); })
      .catch(e => { if (live) setError(e instanceof Error ? e.message : "Audio tracks are unavailable."); });
    return () => { live = false; };
  }, [jobId, refresh, updating]);
  return <details className="audio-tracks"><summary>Separate voice and music tracks</summary>
    <section aria-label="Separate audio tracks"><h3>Your soundtrack, in separate pieces.</h3>
      <p>Use the voice and added music independently in another editor. Change or replace the music with the controls below.</p>
      <button className="btn" disabled={updating} onClick={() => setRefresh(value => value + 1)}>Refresh audio tracks</button>
      {updating && <p role="status">Separate tracks will be available when the update finishes.</p>}
      {!updating && !state && !error && <p role="status">Loading finished audio tracks…</p>}
      {!updating && state && <><p className="hint">{state.note}</p>
        {!!state.pending_edits && <Notice>These downloads match the last finished video. Update video to include your saved cuts and edits.</Notice>}
        {!state.tracks.length && <p>Update video once to create its separate tracks.</p>}
        {state.tracks.map(track => <article className="story-block" key={track.id}><h4>{track.id === "music" ? "Added music" : "Recorded voice"}</h4>
          <audio controls preload="none" aria-label={`${track.id} track`} src={audioTrackUrl(jobId, track.id)} style={{width: "100%"}} />
          <a className="btn" href={audioTrackUrl(jobId, track.id)} download>Download {track.id} WAV</a>
        </article>)}
        {!!state.tracks.length && !state.tracks.some(track => track.id === "music") && <p>No added music was used in this render. Add a track below, then Update video.</p>}
      </>}
      {!updating && error && <Notice tone="error">{error}</Notice>}
    </section>
  </details>;
}
