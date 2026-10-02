/**
 * The studio's timeline (D-180): a ruler, the scenes, the words, and a lane
 * kept for the music (the score plan's Stage 4), under one playhead.
 *
 * Clicking the ruler, a scene or a word moves the video there. Each word owns
 * its slice of time, on alternating rows, so none covers another. Only what
 * is in view is drawn: a 17-minute talk has about 2,500 words, and the
 * timeline must stay smooth with all of them.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { type ScenePlan } from "../api";
import { sceneThumbnail, showsSpeaker } from "./Filmstrip";

export interface TimedWord {
  text: string;
  start: number;
  end: number;
  scene: number;
}

const MIN_ZOOM = 12;
const MAX_ZOOM = 200;

function clock(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

export function Timeline({
  jobId,
  plan,
  words,
  time,
  currentScene,
  changed,
  sceneLane,
  onSeek,
  onScene,
}: {
  jobId: string;
  plan: ScenePlan;
  words: TimedWord[];
  time: number;
  currentScene: number;
  changed: Set<number>;
  /** The scenes lane's height, pixels: the timeline's resizable part (D-183). */
  sceneLane: number;
  onSeek: (seconds: number) => void;
  onScene: (index: number) => void;
}) {
  const duration = plan.total_frames / plan.fps;
  // Pixels a second: long talks start zoomed out so they fit a few screens.
  const [zoom, setZoom] = useState(() => Math.max(MIN_ZOOM, Math.min(64, 2400 / Math.max(1, duration))));
  const lanes = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ left: 0, width: 1200 });
  const width = Math.ceil(duration * zoom) + 48;

  useEffect(() => {
    const onZoom = (event: Event) => {
      const factor = (event as CustomEvent<number>).detail;
      setZoom((z) => Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, z * factor)));
    };
    window.addEventListener("voxframe:zoom", onZoom);
    return () => window.removeEventListener("voxframe:zoom", onZoom);
  }, []);

  // Keep the playhead in view.
  useEffect(() => {
    const element = lanes.current;
    if (!element) return;
    const x = time * zoom;
    if (x < element.scrollLeft + 40 || x > element.scrollLeft + element.clientWidth - 80) {
      element.scrollLeft = Math.max(0, x - element.clientWidth / 3);
    }
  }, [time, zoom]);

  useEffect(() => {
    const element = lanes.current;
    if (!element) return;
    const measure = () => setView({ left: element.scrollLeft, width: element.clientWidth });
    measure();
    element.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    return () => {
      element.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
    };
  }, []);

  const ticks = useMemo(() => {
    const every = zoom >= 40 ? 5 : zoom >= 16 ? 10 : 30;
    const list: number[] = [];
    for (let s = 0; s <= duration; s += every) list.push(s);
    return list;
  }, [duration, zoom]);

  // Only the words near the view are drawn.
  const from = (view.left - 200) / zoom;
  const to = (view.left + view.width + 200) / zoom;
  const visible = words
    .map((word, index) => ({ word, index }))
    .filter(({ word }) => word.end >= from && word.start <= to);

  const seekFromClick = (event: React.MouseEvent<HTMLElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    onSeek((event.clientX - box.left) / zoom);
  };

  return (
    <section
      className="studio-timeline"
      aria-label="Timeline"
      style={{ "--scene-lane": `${sceneLane}px` } as React.CSSProperties}
    >
      <div className="timeline-names">
        <div>
          <span aria-hidden="true">Time</span>
          <span className="timeline-zoom">
            <button
              type="button"
              aria-label="Zoom the timeline out (−)"
              onClick={() => setZoom((z) => Math.max(MIN_ZOOM, z / 1.5))}
            >
              −
            </button>
            <button
              type="button"
              aria-label="Zoom the timeline in (+)"
              onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z * 1.5))}
            >
              +
            </button>
          </span>
        </div>
        <div aria-hidden="true">Scenes</div>
        <div aria-hidden="true">Words</div>
        <div aria-hidden="true">Music</div>
      </div>
      <div className="timeline-lanes" ref={lanes}>
        <div className="timeline-inner" style={{ width }}>
          <div className="timeline-ruler" onClick={seekFromClick} aria-hidden="true">
            {ticks.map((s) => (
              <span key={s} style={{ left: s * zoom }}>
                {clock(s)}
              </span>
            ))}
          </div>
          <div className="timeline-lane timeline-scenes" role="list" aria-label="Scenes">
            {plan.scenes.map((scene) => {
              const left = (scene.start_frame / plan.fps) * zoom;
              const sceneWidth = ((scene.end_frame - scene.start_frame) / plan.fps) * zoom - 3;
              const label = scene.card_kind
                ? `${scene.card_kind === "title" ? "Title" : "Chapter"}: ${scene.card_text}`
                : scene.caption_text || scene.text;
              return (
                <div role="listitem" key={scene.index} className="timeline-clip-wrap" style={{ left, width: Math.max(4, sceneWidth) }}>
                  <button
                    type="button"
                    className={`timeline-clip${scene.card_kind ? " card-clip" : ""}`}
                    aria-current={scene.index === currentScene}
                    aria-label={`${label.slice(0, 80)}, at ${clock(scene.start_frame / plan.fps)}`}
                    title={label}
                    onClick={() => onScene(scene.index)}
                  >
                    {(scene.asset || showsSpeaker(scene)) && sceneWidth > 28 && (
                      <img src={sceneThumbnail(jobId, scene)} alt="" loading="lazy" />
                    )}
                    {changed.has(scene.index) && <i className="timeline-changed" aria-hidden="true" />}
                    <span>{label}</span>
                  </button>
                </div>
              );
            })}
          </div>
          <div className="timeline-lane timeline-words" aria-label="Words">
            {visible.map(({ word, index }) => {
              const next = words[index + 1];
              const room = (next && next.scene === word.scene ? next.start : word.end + 0.5) - word.start;
              const now = time >= word.start && time < word.end + 0.08;
              return (
                <button
                  key={index}
                  type="button"
                  className={`timeline-word ${index % 2 ? "low" : "high"}${now ? " now" : ""}`}
                  style={{ left: word.start * zoom, width: Math.max(12, room * zoom - 2) }}
                  title={word.text}
                  aria-label={`${word.text}, at ${clock(word.start)}`}
                  tabIndex={-1}
                  onClick={() => onSeek(word.start)}
                >
                  {word.text}
                </button>
              );
            })}
          </div>
          <div className="timeline-lane timeline-music" aria-label="Music">
            <div className="timeline-music-lane">
              <span>
                {plan.score
                  ? `Music: generated score, ${plan.score.style}`
                  : plan.music_path
                    ? "Music: your own track"
                    : "No music"}
              </span>
            </div>
          </div>
          <div className="timeline-playhead" style={{ left: time * zoom }} aria-hidden="true" />
        </div>
      </div>
    </section>
  );
}
