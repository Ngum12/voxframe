import { useEffect, useRef, useState } from "react";
import { approveCollectionReview, artifactUrl, openClipReview, reviewCollection, type CollectionFinishReport } from "../api";
import { Notice } from "../components";

export function CollectionFinishReview({jobId, selected, stamp, onApproved}: {
  jobId: string; selected: string[]; stamp: string; onApproved: (id: string | null) => void;
}) {
  const [report, setReport] = useState<CollectionFinishReport | null>(null);
  const [checked, setChecked] = useState<string[]>([]), [watched, setWatched] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0), [approved, setApproved] = useState(false);
  const generation = useRef(0);
  const selection = selected.join(",");
  useEffect(() => {
    let live = true; generation.current++;
    onApproved(null); setApproved(false); setReport(null); setChecked([]); setWatched([]); setError(null); setBusy(false);
    void reviewCollection(jobId, selection.split(",")).then(value => { if (live) setReport(value); })
      .catch(e => { if (live) setError(e instanceof Error ? e.message : "Could not review the collection."); });
    return () => { live = false; generation.current++; };
  }, [jobId, selection, stamp, refresh, onApproved]);
  const issues = report?.clips.flatMap(clip => clip.issues) ?? [];
  const ready = report && !report.clips.some(clip => clip.blockers.length) && issues.every(cue => checked.includes(cue.id)) &&
    report.clips.every(clip => watched.includes(clip.job_id));
  return <section className="collection-finish-review" aria-label="Collection finishing review">
    <h4>Watch. Listen. Finish with confidence.</h4>
    <p>Check each finished clip and acknowledge its cues before building your bookend collection.</p>
    <button className="btn" disabled={busy} onClick={() => setRefresh(value => value + 1)}>Refresh collection review</button>
    {!report && !error && <p role="status">Checking finished clips…</p>}
    {report && <><p className="hint">{report.note}</p>
      {report.clips.map(clip => <article className="story-block" key={clip.job_id}>
        <h5>{clip.name}</h5><p className="hint">{clip.width} × {clip.height} · {clip.seconds.toFixed(1)} seconds</p>
        <video controls preload="metadata" aria-label={`Finished clip ${clip.name}`} src={artifactUrl(clip.job_id, "video")} style={{width: "100%", maxHeight: 360}} />
        <button className="btn" onClick={() => openClipReview({jobId: clip.job_id, parent: jobId, scene: null, action: "export"})}>Open finished clip</button>
        {clip.sound && <p className="hint">Recorded sound: {clip.sound.integrated_lufs?.toFixed(1) ?? "unavailable"} LUFS · peak {clip.sound.true_peak?.toFixed(1) ?? "unavailable"} dBTP</p>}
        {clip.blockers.map(blocker => <Notice tone="error" key={blocker}>{blocker}</Notice>)}
        {!!clip.blockers.length && <button className="btn" onClick={() => openClipReview({jobId: clip.job_id, parent: jobId, scene: null, action: "export"})}>Open clip to update</button>}
        {clip.issues.map(cue => <div className="finish-issue" key={cue.id}>
          <strong>{cue.title}</strong><p>{cue.detail}</p>
          <button className="btn" onClick={() => openClipReview({jobId: clip.job_id, parent: jobId, scene: cue.scene, action: cue.action})}>Review in {cue.action[0].toUpperCase() + cue.action.slice(1)}</button>
          <label className="check"><input type="checkbox" checked={checked.includes(cue.id)} disabled={busy || approved}
            onChange={e => setChecked(ids => e.target.checked ? [...ids, cue.id] : ids.filter(id => id !== cue.id))} />Mark checked: {cue.title}</label>
        </div>)}
        <label className="check"><input type="checkbox" checked={watched.includes(clip.job_id)} disabled={busy || approved || !!clip.blockers.length}
          onChange={e => setWatched(ids => e.target.checked ? [...ids, clip.job_id] : ids.filter(id => id !== clip.job_id))} />I watched and listened to this finished clip: {clip.name}</label>
      </article>)}
      <button className="btn btn-primary" disabled={!ready || busy || approved} onClick={() => {
        const version = generation.current;
        setBusy(true); setError(null);
        void approveCollectionReview(jobId, selected, report.fingerprint, checked, watched).then(value => {
          if (version !== generation.current) return;
          setApproved(true); onApproved(value.review_id);
        }).catch(e => { if (version === generation.current) setError(e instanceof Error ? e.message : "Review changed. Refresh and check again."); })
          .finally(() => { if (version === generation.current) setBusy(false); });
      }}>{approved ? "Collection review approved" : busy ? "Saving your review…" : "Approve collection review"}</button>
    </>}
    {error && <Notice tone="error">{error}</Notice>}
  </section>;
}
