import { useEffect, useRef, useState } from "react";
import { getCamera, previewCamera, saveCamera, type CameraControls,
  type CameraMove, type EditResult, type PlannedScene } from "../api";
import { Notice } from "../components";

const directions = [
  ["in", "Zoom in"], ["out", "Zoom out"], ["left", "Pan left"],
  ["right", "Pan right"], ["up", "Pan up"], ["down", "Pan down"],
] as const;

export function CameraStudio({ jobId, scene, onEdited }: {
  jobId: string; scene: PlannedScene; onEdited: (result: EditResult) => void;
}) {
  const [data, setData] = useState<CameraControls | null>(null);
  const [direction, setDirection] = useState<string>("auto");
  const [strength, setStrength] = useState(.5);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{url: string; seconds: number; note: string} | null>(null);
  const generation = useRef(0), player = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const current = ++generation.current;
    setData(null); setPreview(null); setError(null); setBusy(false);
    getCamera(jobId, scene.index).then(result => {
      if (generation.current !== current) return;
      setData(result); setDirection(result.on ? result.settings?.direction ?? "auto" : "still");
      setStrength(result.settings?.strength ?? .5);
    }).catch(e => { if (generation.current === current) setError(e.message); });
    return () => { generation.current++; };
  }, [jobId, scene]);
  useEffect(() => { setPreview(null); }, [direction, strength]);
  useEffect(() => {
    const mounted = player.current;
    return () => { mounted?.pause(); };
  }, [preview]);
  const explicit = direction !== "auto" && direction !== "still";
  async function act(action: "preview" | "save") {
    if (!data || busy) return;
    const current = generation.current;
    const choice: CameraControls = { revision: data.revision, on: direction !== "still",
      settings: explicit ? { direction: direction as CameraMove["direction"], strength } : null };
    setBusy(true); setError(null);
    try {
      if (action === "preview") {
        const result = await previewCamera(jobId, scene.index, choice);
        if (generation.current === current) setPreview(result);
      } else {
        const result = await saveCamera(jobId, scene.index, choice);
        if (generation.current === current) onEdited(result);
      }
    } catch (e) {
      if (generation.current === current) setError(e instanceof Error ? e.message : "The camera choice could not be prepared.");
    } finally { if (generation.current === current) setBusy(false); }
  }
  return <section className="camera-studio" aria-label="Photo camera movement">
    <div className="camera-heading"><span className="shorts-eyebrow">THE PHOTO, IN MOTION</span>
      <h3>Direct the camera.</h3></div>
    <fieldset disabled={busy || !data}>
      <label className="label">Camera direction<select aria-label="Camera direction" value={direction}
        onChange={e => setDirection(e.target.value)}>
        <option value="auto">Automatic · follow the style</option><option value="still">Hold still</option>
        {directions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      {explicit && <label className="label">Movement strength · {Math.round(strength * 100)}%
        <input type="range" aria-label="Movement strength" min={0} max={100} step={5}
          value={Math.round(strength * 100)} onChange={e => setStrength(Number(e.target.value) / 100)} />
      </label>}
      <p className="hint">A gentle move across the photo, timed to this scene. Pans crop slightly to make room for movement. Zero strength holds the full frame still.</p>
      <div className="caption-save-actions"><button className="btn" onClick={() => void act("preview")}>Preview camera movement</button>
        <button className="btn btn-primary" onClick={() => void act("save")}>Save camera movement</button></div>
    </fieldset>
    {busy && <p role="status">Preparing your camera choice…</p>}
    {error && <Notice tone="error">{error}</Notice>}
    {preview && <div className="shorts-preview">
      <video ref={player} controls muted playsInline preload="metadata" src={preview.url} aria-label="Rendered camera preview" />
      <p className="hint">{preview.seconds.toFixed(2)} s · {preview.note}</p>
    </div>}
    <p className="hint">Preview keeps your edit intact. Save, then Update video for the finished result. Undo restores the previous camera choice.</p>
  </section>;
}
