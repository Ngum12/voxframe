/**
 * Sharing a scene's frame between you and its picture (D-197): one at a time,
 * a split screen (the picture above, you below, explaining it), or one inset
 * in the other in a round or rounded frame.
 *
 * Saved at once; the player previews the scene as it will look, and "Update
 * video" makes it.
 */

import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  DEFAULT_LAYOUT,
  type PlanHistory,
  type PlannedScene,
  type SceneLayout,
  setLayout,
} from "../api";
import { Notice } from "../components";

const KINDS: { id: SceneLayout["kind"]; label: string; note: string }[] = [
  { id: "full", label: "One at a time", note: "You or the picture fills the frame" },
  { id: "split", label: "Split screen", note: "The picture above, you below" },
  { id: "inset", label: "Picture in picture", note: "One small, over the other" },
];

const CORNERS: { label: string; x: number; y: number }[] = [
  { label: "Top left", x: 0.26, y: 0.2 },
  { label: "Top right", x: 0.74, y: 0.2 },
  { label: "Bottom left", x: 0.26, y: 0.62 },
  { label: "Bottom right", x: 0.74, y: 0.62 },
];

/** A drawing of each layout, in the video's shape. */
function Sketch({ kind, vertical }: { kind: SceneLayout["kind"]; vertical: boolean }) {
  const [w, h] = vertical ? [27, 48] : [48, 27];
  return (
    <svg className="layout-sketch" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <rect x="0.5" y="0.5" width={w - 1} height={h - 1} rx="2" className="frame" />
      {kind === "full" && <circle cx={w / 2} cy={h * 0.42} r={Math.min(w, h) * 0.18} className="you" />}
      {kind === "split" &&
        (vertical ? (
          <>
            <rect x="0.5" y="0.5" width={w - 1} height={h / 2 - 0.5} className="pic" />
            <circle cx={w / 2} cy={h * 0.72} r={w * 0.16} className="you" />
          </>
        ) : (
          <>
            <rect x="0.5" y="0.5" width={w / 2 - 0.5} height={h - 1} className="pic" />
            <circle cx={w * 0.75} cy={h / 2} r={h * 0.18} className="you" />
          </>
        ))}
      {kind === "inset" && (
        <>
          <rect x="0.5" y="0.5" width={w - 1} height={h - 1} rx="2" className="pic" />
          <circle cx={w * 0.72} cy={h * 0.24} r={Math.min(w, h) * 0.2} className="you ring" />
        </>
      )}
    </svg>
  );
}

export function LayoutPicker({
  jobId,
  scene,
  vertical,
  onSaved,
}: {
  jobId: string;
  scene: PlannedScene;
  vertical: boolean;
  onSaved: (scene: PlannedScene, history: PlanHistory) => void;
}) {
  const saved = scene.layout ?? DEFAULT_LAYOUT;
  const [draft, setDraft] = useState<SceneLayout>(saved);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => setDraft(scene.layout ?? DEFAULT_LAYOUT), [scene.index, scene.layout]);

  const save = async (next: SceneLayout) => {
    setError(null);
    try {
      const result = await setLayout(jobId, scene.index, next);
      onSaved(result.scene, result);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The layout could not be changed.");
    }
  };
  const change = (changes: Partial<SceneLayout>, wait = false) => {
    const next = { ...draft, ...changes };
    setDraft(next);
    window.clearTimeout(timer.current);
    if (wait) timer.current = window.setTimeout(() => void save(next), 300);
    else void save(next);
  };
  const percent = (value: number) => `${Math.round(value * 100)}%`;

  return (
    <div className="card layout-picker">
      <header>
        <h2>Layout</h2>
        <p>Show the picture and you together: the picture explains, you tell it.</p>
      </header>

      <fieldset className="choices layout-kinds">
        <legend className="sr-only">Layout</legend>
        {KINDS.map((option) => (
          <label key={option.id} className="choice">
            <input
              type="radio"
              name={`layout-${scene.index}`}
              value={option.id}
              checked={draft.kind === option.id}
              onChange={() => change({ kind: option.id })}
              aria-label={option.label}
              aria-describedby={`layout-${scene.index}-${option.id}`}
            />
            <Sketch kind={option.id} vertical={vertical} />
            <strong>{option.label}</strong>
            <span id={`layout-${scene.index}-${option.id}`}>{option.note}</span>
          </label>
        ))}
      </fieldset>

      {draft.kind === "split" && (
        <div className="layout-controls">
          <label className="cap-size">
            <span>{vertical ? "Picture height" : "Picture width"}</span>
            <input
              type="range"
              min={0.25}
              max={0.75}
              step={0.01}
              value={draft.split}
              aria-valuetext={percent(draft.split)}
              onChange={(event) => change({ split: Number(event.target.value) }, true)}
            />
            <b>{percent(draft.split)}</b>
          </label>
          <label className="toggle">
            <input
              type="checkbox"
              checked={draft.speaker_first}
              onChange={(event) => change({ speaker_first: event.target.checked })}
            />
            <span className="text">
              <strong>{vertical ? "You on top" : "You on the left"}</strong>
            </span>
          </label>
          <label className="toggle">
            <input
              type="checkbox"
              checked={draft.divider}
              onChange={(event) => change({ divider: event.target.checked })}
            />
            <span className="text">
              <strong>A line between them</strong>
              <span>Captions sit on it unless you have moved them.</span>
            </span>
          </label>
        </div>
      )}

      {draft.kind === "inset" && (
        <div className="layout-controls">
          <div className="cap-segments">
            <span className="layout-label">In the small frame</span>
            <div role="radiogroup" aria-label="In the small frame">
              {[
                { value: true, label: "You" },
                { value: false, label: "The picture" },
              ].map((option) => (
                <button
                  key={option.label}
                  type="button"
                  role="radio"
                  className="chip"
                  aria-checked={draft.inset_speaker === option.value}
                  aria-pressed={draft.inset_speaker === option.value}
                  onClick={() => change({ inset_speaker: option.value })}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          <div className="cap-segments">
            <span className="layout-label">Shape</span>
            <div role="radiogroup" aria-label="Shape">
              {(["circle", "rounded"] as const).map((shape) => (
                <button
                  key={shape}
                  type="button"
                  role="radio"
                  className="chip"
                  aria-checked={draft.inset_shape === shape}
                  aria-pressed={draft.inset_shape === shape}
                  onClick={() => change({ inset_shape: shape })}
                >
                  {shape === "circle" ? "Circle" : "Rounded"}
                </button>
              ))}
            </div>
          </div>
          <label className="cap-size">
            <span>Size</span>
            <input
              type="range"
              min={0.15}
              max={0.6}
              step={0.01}
              value={draft.inset_size}
              aria-valuetext={percent(draft.inset_size)}
              onChange={(event) => change({ inset_size: Number(event.target.value) }, true)}
            />
            <b>{percent(draft.inset_size)}</b>
          </label>
          <div className="cap-segments">
            <span className="layout-label">Where</span>
            <div role="group" aria-label="Where">
              {CORNERS.map((corner) => (
                <button
                  key={corner.label}
                  type="button"
                  className="chip"
                  aria-pressed={Math.abs(draft.inset_x - corner.x) < 0.02 && Math.abs(draft.inset_y - corner.y) < 0.02}
                  onClick={() => change({ inset_x: corner.x, inset_y: corner.y })}
                >
                  {corner.label}
                </button>
              ))}
            </div>
          </div>
          <label className="cap-size">
            <span>Across</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={draft.inset_x}
              aria-valuetext={percent(draft.inset_x)}
              onChange={(event) => change({ inset_x: Number(event.target.value) }, true)}
            />
          </label>
          <label className="cap-size">
            <span>Down</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={draft.inset_y}
              aria-valuetext={percent(draft.inset_y)}
              onChange={(event) => change({ inset_y: Number(event.target.value) }, true)}
            />
          </label>
        </div>
      )}

      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}
