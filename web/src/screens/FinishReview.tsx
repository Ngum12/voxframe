import { useEffect, useState } from "react";
import { getFinishReview, type FinishAction, type FinishIssue, type FinishReport, type ScenePlan } from "../api";
import { Notice } from "../components";

const LABELS = { captions: "Captions", framing: "Framing", timing: "Timing", sound: "Sound" };
const ACTIONS: Record<FinishAction, string> = { captions: "Captions", director: "Director", sound: "Sound", scenes: "Scenes", export: "Export", shorts: "Shorts" };

export function FinishReview({ jobId, plan, pending, onReview }: {
  jobId: string; plan: ScenePlan; pending: number;
  onReview: (scene: number | null, action: FinishAction) => void;
}) {
  const [report, setReport] = useState<FinishReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const [filter, setFilter] = useState<FinishIssue["category"] | "all">("all");
  const [expanded, setExpanded] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setReport(null); setError(null); setChecked([]); setExpanded(false);
    getFinishReview(jobId).then(result => {
      if (!live) return;
      setReport(result);
      try {
        const saved: unknown = JSON.parse(sessionStorage.getItem(`voxframe.finish.${jobId}`) ?? "null");
        if (saved && typeof saved === "object" && "revision" in saved && saved.revision === result.revision && "checked" in saved && Array.isArray(saved.checked)) {
          setChecked(saved.checked.filter((id): id is string => typeof id === "string" && result.issues.some(issue => issue.id === id)));
        }
      } catch { /* Storage is optional. */ }
    })
      .catch(reason => { if (live) setError(reason.message); });
    return () => { live = false; };
  }, [jobId, plan, attempt]);
  useEffect(() => {
    if (!report) return;
    try { sessionStorage.setItem(`voxframe.finish.${jobId}`, JSON.stringify({ revision: report.revision, checked })); }
    catch { /* Storage is optional. */ }
  }, [jobId, report, checked]);
  const issues = report?.issues.filter(issue => filter === "all" || issue.category === filter) ?? [];
  return <section className="finish-review" aria-label="Finishing review">
    <div className="finish-heading"><span className="shorts-eyebrow">ONE LAST LOOK</span>
      <h3>Finish with intention.</h3><p>Review the saved edit before you export.</p></div>
    {error && <><Notice tone="error">{error}</Notice><button type="button" className="btn" onClick={() => setAttempt(n => n + 1)}>Retry review</button></>}
    {!report && !error && <p role="status">Checking the saved edit…</p>}
    {report && <>
      <p className="finish-receipt" role="status">{report.width} × {report.height} · {report.seconds.toFixed(2)} s · {report.issues.length} review cues · {checked.length} checked</p>
      {pending > 0 && <Notice>There are saved changes waiting for Update video. Review the controls now; update before judging picture and sound playback.</Notice>}
      <div className="filters" role="group" aria-label="Filter finishing cues">
        <button type="button" className="chip" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>All {report.issues.length}</button>
        {Object.entries(LABELS).map(([category, label]) => <button key={category} type="button" className="chip"
          aria-pressed={filter === category} onClick={() => setFilter(category as FinishIssue["category"])}>{label} {report.counts[category as FinishIssue["category"]]}</button>)}
      </div>
      {report.issues.length === 0 ? <p className="finish-clear">No finishing cues found in the saved metadata. Watch and listen to the finished video to confirm the result.</p>
        : issues.length === 0 ? <p>No cues in this category.</p> : <ul className="finish-issues">
          {(expanded ? issues : issues.slice(0, 8)).map(issue => {
            const number = issue.scene === null ? null : plan.scenes.filter(s => !s.card_kind && s.index <= issue.scene!).length;
            const done = checked.includes(issue.id);
            return <li key={issue.id} data-checked={done}>
              <span className="finish-meta">{LABELS[issue.category]} · {number === null ? "Project" : `Scene ${number} · ${issue.at.toFixed(1)} s`}</span>
              <h4>{issue.title}</h4><p>{issue.detail}</p>
              <div className="actions"><button type="button" className="btn btn-quiet" onClick={() => onReview(issue.scene, issue.action)}>Review in {ACTIONS[issue.action]}</button>
                <button type="button" className="link-button" aria-pressed={done} onClick={() => setChecked(current => done ? current.filter(id => id !== issue.id) : [...current, issue.id])}>{done ? "Checked ✓" : "Mark checked"}</button></div>
            </li>;
          })}
        </ul>}
      {!expanded && issues.length > 8 && <button type="button" className="btn" onClick={() => setExpanded(true)}>Show all {issues.length} cues</button>}
      <p className="hint">{report.note} This review uses saved output settings. Save a new export preset to review its size. Check marks reset when the saved edit changes.</p>
    </>}
  </section>;
}
