/**
 * The studio's caption controls (D-196): how the words move, how a caption
 * comes on screen, its size and place, and the words to emphasise.
 *
 * Every choice is saved to the plan at once and drawn live in the player from
 * the same document the video will burn in, so what is seen here is what the
 * video will show.
 */

import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  type CaptionAnimation,
  type CaptionChoice,
  type CaptionLook,
  type CaptionTransition,
  type PlanHistory,
  type PlannedScene,
  setCaptions,
  setSceneCaptions,
} from "../api";
import { Notice } from "../components";

export const ANIMATIONS: { id: CaptionAnimation; label: string; note: string }[] = [
  { id: "highlight", label: "Highlight", note: "The spoken word in colour" },
  { id: "pop", label: "Pop", note: "Each word pops in as it is said" },
  { id: "bounce", label: "Bounce", note: "The spoken word jumps" },
  { id: "spotlight", label: "Spotlight", note: "A box glides word to word" },
  { id: "karaoke", label: "Karaoke", note: "The line fills as it is said" },
  { id: "typewriter", label: "Typewriter", note: "Builds up word by word" },
  { id: "plain", label: "Plain", note: "No movement" },
];

const TRANSITIONS: { id: CaptionTransition; label: string }[] = [
  { id: "cut", label: "Cut" },
  { id: "fade", label: "Fade" },
  { id: "pop", label: "Pop in" },
  { id: "slide", label: "Slide up" },
  { id: "zoom", label: "Zoom in" },
];

/** A small moving example of each style: three words, on a loop. */
function Demo({ kind }: { kind: CaptionAnimation }) {
  return (
    <span className="cap-demo" data-kind={kind} aria-hidden="true">
      {["Make", "it", "pop"].map((word, index) => (
        <span key={word} style={{ "--i": index } as React.CSSProperties}>
          {word}
        </span>
      ))}
    </span>
  );
}

/** The words a scene's captions show: the corrected text when there is one. */
export function captionTokens(scene: PlannedScene): string[] {
  const corrected = scene.caption_text.trim();
  if (corrected) return corrected.split(/\s+/);
  return scene.words.map((word) => word.text.trim()).filter(Boolean);
}

export function CaptionStyle({
  jobId,
  look,
  placing,
  onPlacing,
  onSaved,
}: {
  jobId: string;
  look: CaptionLook | null;
  placing: boolean;
  onPlacing: (on: boolean) => void;
  onSaved: (choice: CaptionChoice, history: PlanHistory) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState(look?.choice.size ?? 1);
  const sizeTimer = useRef<number | undefined>(undefined);
  useEffect(() => setSize(look?.choice.size ?? 1), [look?.choice.size]);

  if (!look) return <p className="muted">Loading the captions…</p>;
  const { choice, effective, template } = look;

  const save = async (changes: Partial<CaptionChoice>) => {
    setError(null);
    try {
      const result = await setCaptions(jobId, { ...choice, ...changes });
      onSaved(result.captions, result);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The captions could not be changed.");
    }
  };

  return (
    <div className="card caption-style">
      <header>
        <h2>Caption style</h2>
        <p>Shown live in the player. Every word moves exactly when it is said.</p>
      </header>

      <fieldset className="choices cap-styles">
        <legend>How the words move</legend>
        {ANIMATIONS.map((option) => (
          <label key={option.id} className="choice cap-style">
            <input
              type="radio"
              name="caption-animation"
              value={option.id}
              checked={effective.animation === option.id}
              onChange={() => void save({ animation: option.id })}
            />
            <Demo kind={option.id} />
            <strong>
              {option.label}
              {template.animation === option.id && <em className="cap-template"> · template</em>}
            </strong>
            <span>{option.note}</span>
          </label>
        ))}
      </fieldset>

      <fieldset className="cap-segments">
        <legend>How each caption comes on screen</legend>
        <div role="radiogroup" aria-label="How each caption comes on screen">
          {TRANSITIONS.map((option) => (
            <button
              key={option.id}
              type="button"
              role="radio"
              className="chip"
              aria-checked={effective.transition === option.id}
              aria-pressed={effective.transition === option.id}
              onClick={() => void save({ transition: option.id })}
            >
              {option.label}
            </button>
          ))}
        </div>
      </fieldset>

      <div className="cap-row">
        <label className="cap-size">
          <span>Size</span>
          <input
            type="range"
            min={0.6}
            max={1.8}
            step={0.05}
            value={size}
            aria-valuetext={`${Math.round(size * 100)}%`}
            onChange={(event) => {
              const value = Number(event.target.value);
              setSize(value);
              window.clearTimeout(sizeTimer.current);
              sizeTimer.current = window.setTimeout(() => void save({ size: value }), 350);
            }}
          />
          <b>{Math.round(size * 100)}%</b>
        </label>
        <label className="toggle cap-caps">
          <input
            type="checkbox"
            checked={effective.uppercase}
            onChange={(event) => void save({ uppercase: event.target.checked })}
          />
          <span className="text">
            <strong>ALL CAPS</strong>
          </span>
        </label>
      </div>

      <div className="cap-place">
        <button
          type="button"
          className="btn btn-small"
          aria-pressed={placing}
          onClick={() => onPlacing(!placing)}
        >
          {placing ? "Done moving" : "Move them in the player"}
        </button>
        {effective.placed && (
          <button type="button" className="btn btn-quiet btn-small" onClick={() => void save({ anchor_y: null })}>
            Put them back
          </button>
        )}
        <span className="muted">
          {placing ? "Drag the line to where the captions should sit." : effective.placed ? "Placed by you." : "Where the template puts them."}
        </span>
      </div>

      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}

export function SceneCaptionStyle({
  jobId,
  scene,
  onSaved,
}: {
  jobId: string;
  scene: PlannedScene;
  onSaved: (scene: PlannedScene, history: PlanHistory) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const tokens = captionTokens(scene);
  const emphasis = new Set(scene.emphasis ?? []);

  const save = async (animation: CaptionAnimation | null, marked: number[]) => {
    setError(null);
    try {
      const result = await setSceneCaptions(jobId, scene.index, animation, marked);
      onSaved(result.scene, result);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The scene's captions could not be changed.");
    }
  };

  return (
    <div className="card caption-scene">
      <header>
        <h2>This scene</h2>
        <p>Tap the words to make bigger and coloured: the ones that matter.</p>
      </header>
      <div className="cap-emphasis" role="group" aria-label="Emphasised words">
        {tokens.map((token, index) => (
          <button
            key={index}
            type="button"
            className="chip"
            aria-pressed={emphasis.has(index)}
            onClick={() => {
              const next = new Set(emphasis);
              if (next.has(index)) next.delete(index);
              else next.add(index);
              void save(scene.caption_animation ?? null, [...next].sort((a, b) => a - b));
            }}
          >
            {token}
          </button>
        ))}
      </div>
      <label className="cap-scene-style">
        <span>Movement in this scene</span>
        <select
          value={scene.caption_animation ?? ""}
          onChange={(event) =>
            void save((event.target.value || null) as CaptionAnimation | null, [...emphasis])
          }
        >
          <option value="">Same as the whole video</option>
          {ANIMATIONS.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}
