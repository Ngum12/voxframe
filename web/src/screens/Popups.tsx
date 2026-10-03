/**
 * Pop-ups (D-198): things that appear on screen when a word is said. Text,
 * stickers, arrows and rings, counters, your own pictures, and a progress bar.
 *
 * Text, shapes and counters are drawn by libass with the captions, so the
 * player shows them exactly; stickers and pictures are shown here, over the
 * player, as the video will lay them. Each is tied to a word: it appears when
 * the word is said, and moves with it.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  type Entrance,
  type Overlay,
  type OverlaysState,
  type PlanHistory,
  type PlannedScene,
  type PopupSuggestion,
  type ShapeKind,
  type Sticker,
  addImageOverlay,
  addOverlay,
  getPopupSuggestions,
  getStickers,
  overlayImageUrl,
  removeOverlay,
  setProgressBar,
  stickerUrl,
  updateOverlay,
} from "../api";
import { Notice } from "../components";
import { captionTokens } from "./CaptionStyle";

const COLOURS: { id: string; hex: string; label: string }[] = [
  { id: "accent", hex: "#FFD700", label: "Gold" },
  { id: "white", hex: "#FFFFFF", label: "White" },
  { id: "red", hex: "#FF3B30", label: "Red" },
  { id: "green", hex: "#34C759", label: "Green" },
  { id: "blue", hex: "#0A84FF", label: "Blue" },
  { id: "black", hex: "#111111", label: "Black" },
];

const ENTRANCES: { id: Entrance; label: string }[] = [
  { id: "pop", label: "Pop in" },
  { id: "slide", label: "Slide up" },
  { id: "bounce", label: "Drop in" },
  { id: "fade", label: "Fade in" },
  { id: "none", label: "Just appear" },
];

const SHAPES: { id: ShapeKind; label: string }[] = [
  { id: "arrow_down", label: "↓ Arrow" },
  { id: "arrow_up", label: "↑ Arrow" },
  { id: "arrow_left", label: "← Arrow" },
  { id: "arrow_right", label: "→ Arrow" },
  { id: "ring", label: "◯ Ring" },
  { id: "underline", label: "▁ Underline" },
];

type Saved = (state: OverlaysState, history: PlanHistory) => void;

function label(overlay: Overlay, stickers: Sticker[]): string {
  if (overlay.kind === "text") return `“${overlay.text}”`;
  if (overlay.kind === "counter") return `Count to ${overlay.text}`;
  if (overlay.kind === "sticker") return stickers.find((s) => s.name === overlay.sticker)?.label ?? "Sticker";
  if (overlay.kind === "shape") return SHAPES.find((s) => s.id === overlay.shape)?.label ?? "Shape";
  return "Your picture";
}

export function PopupsPanel({
  jobId,
  scene,
  scenes,
  state,
  time,
  selected,
  onSelect,
  onSaved,
  onSeek,
}: {
  jobId: string;
  scene: PlannedScene;
  scenes: PlannedScene[];
  state: OverlaysState | null;
  time: number;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onSaved: Saved;
  onSeek: (seconds: number) => void;
}) {
  const [stickers, setStickers] = useState<Sticker[]>([]);
  const [suggestions, setSuggestions] = useState<PopupSuggestion[]>([]);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const tokens = captionTokens(scene);

  useEffect(() => {
    void getStickers().then(setStickers).catch(() => undefined);
  }, []);
  const overlayCount = state?.overlays.length ?? 0;
  useEffect(() => {
    void getPopupSuggestions(jobId).then(setSuggestions).catch(() => undefined);
  }, [jobId, overlayCount]);

  // The word being said at the playhead, in this scene: where a new pop-up goes.
  const wordNow = useMemo(() => {
    const index = scene.words.findIndex((word) => time < word.end + 0.05);
    return index < 0 ? Math.max(0, Math.min(tokens.length, scene.words.length) - 1) : Math.min(index, Math.max(0, tokens.length - 1));
  }, [scene.words, time, tokens.length]);

  const run = async (action: () => Promise<OverlaysState & PlanHistory & { id?: string }>) => {
    setError(null);
    try {
      const result = await action();
      onSaved(result, result);
      if (result.id) onSelect(result.id);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "That could not be changed.");
    }
  };
  const add = (fields: Partial<Overlay> & { kind: Overlay["kind"] }) =>
    void run(() => addOverlay(jobId, { scene: scene.index, word: wordNow, ...fields }));

  const here = (state?.overlays ?? []).filter((o) => o.scene === scene.index);

  return (
    <div className="popups">
      {suggestions.length > 0 && (
        <div className="card popup-suggestions">
          <header>
            <h2>Suggested</h2>
            <p>From your words. Nothing is added until you choose.</p>
          </header>
          <ul className="list-plain">
            {suggestions.slice(0, 6).map((suggestion) => {
              const sticker = stickers.find((s) => s.name === suggestion.value);
              const target = scenes.find((s) => s.index === suggestion.scene);
              return (
                <li key={`${suggestion.scene}-${suggestion.word}`} className="popup-suggestion">
                  {suggestion.kind === "sticker" ? (
                    <img src={stickerUrl(suggestion.value)} alt="" width={32} height={32} />
                  ) : (
                    <span className="popup-count" aria-hidden="true">
                      {suggestion.value}
                    </span>
                  )}
                  <span>
                    {suggestion.kind === "sticker" ? sticker?.label ?? suggestion.value : `Count to ${suggestion.value}`} on{" "}
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => {
                        const word = target?.words[suggestion.word];
                        if (word) onSeek(word.start);
                      }}
                    >
                      “{suggestion.because}”
                    </button>
                  </span>
                  <button
                    type="button"
                    className="btn btn-small"
                    onClick={() =>
                      void run(() =>
                        addOverlay(jobId, {
                          scene: suggestion.scene,
                          word: suggestion.word,
                          ...(suggestion.kind === "sticker"
                            ? { kind: "sticker", sticker: suggestion.value, x: 0.76, y: 0.3, size: 0.3 }
                            : { kind: "counter", text: suggestion.value, look: "bold", y: 0.26, size: 0.5 }),
                        }),
                      )
                    }
                  >
                    Add
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <div className="card">
        <header>
          <h2>Add a pop-up</h2>
          <p>
            It appears on <strong>“{tokens[wordNow] ?? "the first word"}”</strong>, the word at the playhead. Drag it
            in the player to place it.
          </p>
        </header>
        <div className="popup-tools">
          <button type="button" className="btn btn-small" onClick={() => add({ kind: "text", text: "Your text", y: 0.18, size: 0.32 })}>
            Text
          </button>
          <button type="button" className="btn btn-small" aria-expanded={picking} onClick={() => setPicking((p) => !p)}>
            Sticker
          </button>
          <button type="button" className="btn btn-small" onClick={() => add({ kind: "shape", shape: "arrow_down", colour: "red", y: 0.3, size: 0.14 })}>
            Arrow
          </button>
          <button type="button" className="btn btn-small" onClick={() => add({ kind: "shape", shape: "ring", y: 0.4, size: 0.45 })}>
            Ring
          </button>
          <button type="button" className="btn btn-small" onClick={() => add({ kind: "counter", text: "100", look: "bold", y: 0.26, size: 0.5 })}>
            Counter
          </button>
          <button type="button" className="btn btn-small" onClick={() => fileRef.current?.click()}>
            Picture
          </button>
          <input
            ref={fileRef}
            type="file"
            className="sr-only"
            accept=".jpg,.jpeg,.png,.webp"
            aria-label="Choose a picture to pop up"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void run(() => addImageOverlay(jobId, scene.index, wordNow, file));
            }}
          />
        </div>
        {picking && (
          <div className="sticker-grid" role="group" aria-label="Stickers">
            {stickers.map((sticker) => (
              <button
                key={sticker.name}
                type="button"
                title={sticker.label}
                aria-label={sticker.label}
                onClick={() => {
                  setPicking(false);
                  add({ kind: "sticker", sticker: sticker.name, x: 0.76, y: 0.3, size: 0.3 });
                }}
              >
                <img src={stickerUrl(sticker.name)} alt="" loading="lazy" />
              </button>
            ))}
          </div>
        )}
      </div>

      {here.length > 0 && (
        <div className="card">
          <header>
            <h2>In this scene</h2>
          </header>
          <ul className="list-plain popup-list">
            {here.map((overlay) => (
              <li key={overlay.id} data-selected={selected === overlay.id}>
                <div className="popup-row">
                  <button type="button" className="link-button" onClick={() => onSelect(selected === overlay.id ? null : overlay.id)}>
                    {label(overlay, stickers)} on “{tokens[overlay.word] ?? "…"}”
                  </button>
                  <button
                    type="button"
                    className="btn btn-quiet btn-small"
                    aria-label={`Take out ${label(overlay, stickers)}`}
                    onClick={() => void run(() => removeOverlay(jobId, overlay.id))}
                  >
                    Remove
                  </button>
                </div>
                {selected === overlay.id && (
                  <PopupEditor
                    key={overlay.id}
                    jobId={jobId}
                    overlay={overlay}
                    tokens={tokens}
                    onSaved={onSaved}
                    onError={setError}
                  />
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="card">
        <label className="toggle">
          <input
            type="checkbox"
            checked={state?.progress_bar ?? false}
            onChange={(event) => void run(() => setProgressBar(jobId, event.target.checked))}
          />
          <span className="text">
            <strong>A progress bar</strong>
            <span>A thin bar along the bottom that fills as the video plays: viewers stay to see it finish.</span>
          </span>
        </label>
      </div>

      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}

function PopupEditor({
  jobId,
  overlay,
  tokens,
  onSaved,
  onError,
}: {
  jobId: string;
  overlay: Overlay;
  tokens: string[];
  onSaved: Saved;
  onError: (message: string | null) => void;
}) {
  const [draft, setDraft] = useState(overlay);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => setDraft(overlay), [overlay]);

  const change = (changes: Partial<Overlay>, wait = false) => {
    const next = { ...draft, ...changes };
    setDraft(next);
    window.clearTimeout(timer.current);
    const save = async () => {
      onError(null);
      try {
        const result = await updateOverlay(jobId, next);
        onSaved(result, result);
      } catch (caught) {
        onError(caught instanceof ApiError ? caught.message : "That could not be changed.");
      }
    };
    if (wait) timer.current = window.setTimeout(() => void save(), 400);
    else void save();
  };
  const percent = (value: number) => `${Math.round(value * 100)}%`;

  return (
    <div className="popup-editor">
      {(draft.kind === "text" || draft.kind === "counter") && (
        <label className="field">
          <span className="label">{draft.kind === "counter" ? "Counts to" : "Says"}</span>
          <input
            type="text"
            value={draft.text}
            maxLength={80}
            onChange={(event) => change({ text: event.target.value }, true)}
          />
        </label>
      )}
      {(draft.kind === "text" || draft.kind === "counter") && (
        <div className="popup-chips" role="radiogroup" aria-label="Look">
          {(["pill", "bold", "note"] as const).map((look) => (
            <button key={look} type="button" role="radio" className="chip" aria-checked={draft.look === look} aria-pressed={draft.look === look} onClick={() => change({ look })}>
              {look === "pill" ? "Pill" : look === "bold" ? "Bold" : "Note card"}
            </button>
          ))}
        </div>
      )}
      {draft.kind === "shape" && (
        <div className="popup-chips" role="radiogroup" aria-label="Shape">
          {SHAPES.map((shape) => (
            <button key={shape.id} type="button" role="radio" className="chip" aria-checked={draft.shape === shape.id} aria-pressed={draft.shape === shape.id} onClick={() => change({ shape: shape.id })}>
              {shape.label}
            </button>
          ))}
        </div>
      )}
      {(draft.kind === "text" || draft.kind === "counter" || draft.kind === "shape") && (
        <div className="popup-colours" role="radiogroup" aria-label="Colour">
          {COLOURS.map((colour) => (
            <button
              key={colour.id}
              type="button"
              role="radio"
              aria-checked={draft.colour === colour.id}
              aria-label={colour.label}
              title={colour.label}
              style={{ background: colour.hex }}
              onClick={() => change({ colour: colour.id })}
            />
          ))}
        </div>
      )}
      <label className="field">
        <span className="label">Appears on</span>
        <select value={draft.word} onChange={(event) => change({ word: Number(event.target.value) })}>
          {tokens.map((token, index) => (
            <option key={index} value={index}>
              {token}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="label">Comes on</span>
        <select value={draft.entrance} onChange={(event) => change({ entrance: event.target.value as Entrance })}>
          {ENTRANCES.map((entrance) => (
            <option key={entrance.id} value={entrance.id}>
              {entrance.label}
            </option>
          ))}
        </select>
      </label>
      <label className="cap-size">
        <span>Stays</span>
        <input type="range" min={0.5} max={8} step={0.1} value={draft.seconds} onChange={(event) => change({ seconds: Number(event.target.value) }, true)} />
        <b>{draft.seconds.toFixed(1)}s</b>
      </label>
      <label className="cap-size">
        <span>Size</span>
        <input type="range" min={0.05} max={1} step={0.01} value={draft.size} aria-valuetext={percent(draft.size)} onChange={(event) => change({ size: Number(event.target.value) }, true)} />
        <b>{percent(draft.size)}</b>
      </label>
    </div>
  );
}

/**
 * Over the player: stickers and pictures as the video will lay them, and a
 * handle on the chosen pop-up to drag it into place.
 */
export function PopupLayer({
  jobId,
  state,
  time,
  selected,
  frame,
  onMoved,
  showPictures,
}: {
  jobId: string;
  showPictures: boolean;
  state: OverlaysState | null;
  time: number;
  selected: string | null;
  frame: React.RefObject<HTMLDivElement>;
  onMoved: (overlay: Overlay) => void;
}) {
  const [dragging, setDragging] = useState<{ id: string; x: number; y: number } | null>(null);
  if (!state) return null;
  const at = (overlay: Overlay) => (dragging?.id === overlay.id ? dragging : overlay);
  const place = (clientX: number, clientY: number) => {
    const box = frame.current?.getBoundingClientRect();
    if (!box) return null;
    const clamp = (v: number) => Math.min(1, Math.max(0, v));
    return { x: clamp((clientX - box.left) / box.width), y: clamp((clientY - box.top) / box.height) };
  };

  return (
    <div className="popup-layer">
      {state.overlays.map((overlay) => {
        const [start, end] = state.times[overlay.id] ?? [0, 0];
        const showing = time >= start && time < end;
        const chosen = overlay.id === selected;
        const picture = overlay.kind === "sticker" || overlay.kind === "image";
        if (!showing && !chosen) return null;
        const { x, y } = at(overlay);
        const style = {
          left: `${x * 100}%`,
          top: `${y * 100}%`,
          width: `${overlay.size * 100}%`,
        } as React.CSSProperties;
        return (
          <div
            key={`${overlay.id}-${start}`}
            className="popup-item"
            data-kind={overlay.kind}
            data-chosen={chosen}
            data-entrance={overlay.entrance}
            data-showing={showing}
            style={style}
            onPointerDown={(event) => {
              if (!chosen) return;
              event.currentTarget.setPointerCapture(event.pointerId);
              const spot = place(event.clientX, event.clientY);
              if (spot) setDragging({ id: overlay.id, ...spot });
            }}
            onPointerMove={(event) => {
              if (dragging?.id !== overlay.id) return;
              const spot = place(event.clientX, event.clientY);
              if (spot) setDragging({ id: overlay.id, ...spot });
            }}
            onPointerUp={() => {
              if (dragging?.id !== overlay.id) return;
              onMoved({ ...overlay, x: dragging.x, y: dragging.y });
              setDragging(null);
            }}
          >
            {picture && showing && showPictures && (
              <img
                src={overlay.kind === "sticker" ? stickerUrl(overlay.sticker) : overlayImageUrl(jobId, overlay.id)}
                alt=""
                draggable={false}
              />
            )}
            {chosen && <span className="popup-handle" aria-hidden="true" />}
          </div>
        );
      })}
    </div>
  );
}
