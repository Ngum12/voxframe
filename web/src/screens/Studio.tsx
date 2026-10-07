/**
 * The studio: the finished video, edited in place (D-180, D-181, D-182).
 *
 * The player stays fixed in view; every edit is in the side panel's tabs
 * (Scenes, Captions, Sound, Style); the timeline runs along the bottom. An
 * edit is saved to the plan at once and shown in the player as a preview
 * where it can be, and "Update video" makes the video again without leaving:
 * the status says what is being made and how far it has got.
 *
 * Undo and redo step through the plan's saved versions, so they cover every
 * edit: a picture, a caption, a card, the camera, the music and the mix.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  type CaptionLook,
  type EditResult,
  type OverlaysState,
  type Job,
  type PlanEditResult,
  type PlanHistory,
  type PlannedScene,
  type ScenePlan,
  type TimelinePlan,
  addTitle,
  artifactUrl,
  followJob,
  getCaptionLook,
  getCaptions,
  getJob,
  getOverlays,
  getPlan,
  getPlanHistory,
  getTimeline,
  openFolder,
  redoPlan,
  rerenderJob,
  setCaptions,
  storyToVideo,
  undoPlan,
  updateOverlay,
  videoToStory,
} from "../api";
import { Notice } from "../components";
import { CaptionStyle, SceneCaptionStyle } from "./CaptionStyle";
import { SceneDetail, TitleAdder, sceneThumbnail, sharesFrame, showsSpeaker } from "./Filmstrip";
import { LayoutPicker } from "./Layout";
import { PacePanel } from "./Pace";
import { PopupLayer, PopupsPanel } from "./Popups";
import { liveCaptionsSupported, useLiveCaptions } from "./LiveCaptions";
import { SoundPanel } from "./Sound";
import { Timeline, type TimedWord } from "./Timeline";

type Tab = "scenes" | "captions" | "popups" | "sound" | "style" | "pace";

/** The studio's panel sizes and whether each is open, kept between sessions (D-183). */
interface Layout {
  panel: number;
  panelOpen: boolean;
  timeline: number;
  timelineOpen: boolean;
}
const LAYOUT_KEY = "voxframe.studio.layout.v1";
const PANEL = { min: 300, max: 720, initial: 400 };
const LANE = { min: 40, max: 260, initial: 78 };

function loadLayout(): Layout {
  const fallback = { panel: PANEL.initial, panelOpen: true, timeline: LANE.initial, timelineOpen: true };
  try {
    const saved = JSON.parse(window.localStorage.getItem(LAYOUT_KEY) ?? "null") as Partial<Layout> | null;
    if (!saved) return fallback;
    const clamp = (value: unknown, range: { min: number; max: number }, initial: number) =>
      typeof value === "number" ? Math.min(range.max, Math.max(range.min, value)) : initial;
    return {
      panel: clamp(saved.panel, PANEL, PANEL.initial),
      panelOpen: saved.panelOpen !== false,
      timeline: clamp(saved.timeline, LANE, LANE.initial),
      timelineOpen: saved.timelineOpen !== false,
    };
  } catch {
    return fallback; // storage blocked or corrupt: the defaults, never an error
  }
}

/** A drag handle that is also a keyboard-operable separator (WAI-ARIA). */
function Resizer({
  orientation,
  label,
  value,
  range,
  onChange,
}: {
  orientation: "vertical" | "horizontal";
  label: string;
  value: number;
  range: { min: number; max: number };
  /** Called with the new size; grows as the handle moves left (vertical) or up (horizontal). */
  onChange: (size: number) => void;
}) {
  const start = useRef<{ at: number; size: number } | null>(null);
  const clamp = (size: number) => Math.round(Math.min(range.max, Math.max(range.min, size)));
  return (
    <div
      className={`studio-resizer ${orientation}`}
      role="separator"
      aria-orientation={orientation}
      aria-label={label}
      aria-valuenow={value}
      aria-valuemin={range.min}
      aria-valuemax={range.max}
      tabIndex={0}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        start.current = { at: orientation === "vertical" ? event.clientX : event.clientY, size: value };
      }}
      onPointerMove={(event) => {
        if (!start.current) return;
        const at = orientation === "vertical" ? event.clientX : event.clientY;
        onChange(clamp(start.current.size - (at - start.current.at)));
      }}
      onPointerUp={() => {
        start.current = null;
      }}
      onKeyDown={(event) => {
        const grow = orientation === "vertical" ? "ArrowLeft" : "ArrowUp";
        const shrink = orientation === "vertical" ? "ArrowRight" : "ArrowDown";
        if (event.key === grow || event.key === shrink) {
          event.preventDefault();
          onChange(clamp(value + (event.key === grow ? 24 : -24)));
        }
      }}
    />
  );
}
const TABS: { id: Tab; label: string }[] = [
  { id: "scenes", label: "Scenes" },
  { id: "captions", label: "Captions" },
  { id: "sound", label: "Sound" },
  { id: "style", label: "Style" },
  { id: "popups", label: "Pop-ups" },
  { id: "pace", label: "Hook & pace" },
];

const ARTIFACT_LABELS: Record<string, string> = {
  video: "Video (MP4)",
  srt: "Captions (SRT)",
  vtt: "Captions (VTT)",
  plan: "Scene plan (JSON)",
};

type Status =
  | { kind: "idle" }
  | { kind: "updating"; message: string; fraction: number | null }
  | { kind: "failed"; message: string };

function clock(seconds: number): string {
  const s = Math.max(0, seconds);
  const minutes = Math.floor(s / 60);
  return `${minutes}:${(s % 60).toFixed(1).padStart(4, "0")}`;
}

/**
 * Every spoken word, on the video's timeline. The plan's word times are
 * already on the video's clock, cards included (D-144), exactly as the burned
 * captions use them; adding the cards again put every word late (D-189).
 */
function timedWords(plan: ScenePlan): TimedWord[] {
  const words: TimedWord[] = [];
  for (const scene of plan.scenes) {
    if (scene.card_kind) continue;
    for (const word of scene.words) {
      words.push({ text: word.text, start: word.start, end: word.end, scene: scene.index });
    }
  }
  return words;
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  if (target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return true;
  return target instanceof HTMLInputElement && target.type !== "checkbox" && target.type !== "radio";
}

export function Studio({
  jobId,
  initialJob,
  sourcingEnabled,
  onShowPlan,
  onAgain,
}: {
  jobId: string;
  initialJob: Job;
  sourcingEnabled: boolean;
  onShowPlan: () => void;
  onAgain: () => void;
}) {
  const [job, setJob] = useState(initialJob);
  const [plan, setPlan] = useState<ScenePlan | null>(null);
  const [history, setHistory] = useState<PlanHistory | null>(null);
  const [tab, setTab] = useState<Tab>("scenes");
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [videoVersion, setVideoVersion] = useState(0);
  const [planVersion, setPlanVersion] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [addingTitle, setAddingTitle] = useState(false);
  const [layout, setLayout] = useState<Layout>(loadLayout);
  const [videoElement, setVideoElement] = useState<HTMLVideoElement | null>(null);
  const [captions, setCaptionsDoc] = useState<string | null>(null);
  const [look, setLook] = useState<CaptionLook | null>(null);
  const [liveFailed, setLiveFailed] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [dragAnchor, setDragAnchor] = useState<number | null>(null);
  const [popups, setPopups] = useState<OverlaysState | null>(null);
  const [selectedPopup, setSelectedPopup] = useState<string | null>(null);
  // The video as it will be made: the plan with its cuts and cold open (D-199).
  const [timeline, setTimeline] = useState<TimelinePlan | null>(null);
  const video = useRef<HTMLVideoElement | null>(null);
  const frame = useRef<HTMLDivElement>(null);
  const shortcuts = useRef<HTMLDialogElement>(null);
  const toastTimer = useRef<number | undefined>(undefined);
  const resumeAt = useRef(0);

  const say = useCallback((text: string) => {
    setToast(text);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 2800);
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
    } catch {
      /* not kept this time; the layout still works */
    }
  }, [layout]);
  const togglePanel = useCallback(() => setLayout((l) => ({ ...l, panelOpen: !l.panelOpen })), []);
  const toggleTimeline = useCallback(() => setLayout((l) => ({ ...l, timelineOpen: !l.timelineOpen })), []);

  const reload = useCallback(async () => {
    const [loaded, where] = await Promise.all([getPlan(jobId), getPlanHistory(jobId)]);
    setPlan(loaded);
    setHistory(where);
    setPlanVersion((v) => v + 1);
  }, [jobId]);

  useEffect(() => {
    reload().catch((caught: Error) => setError(caught.message));
  }, [reload]);

  // --- live captions (D-196) ---------------------------------------------------

  // The video without its captions, with the captions drawn over it from the
  // plan as it is now. A video made before there was a studio copy, or a
  // browser that cannot draw them, plays the captioned video instead.
  const live = job.artifacts.includes("studio") && liveCaptionsSupported() && !liveFailed;
  useEffect(() => {
    if (!plan) return;
    let current = true;
    const timer = window.setTimeout(() => {
      // Both at once, so the drawn captions and the controls change together.
      void Promise.all([
        getCaptionLook(jobId),
        live ? getCaptions(jobId) : Promise.resolve(null),
        getOverlays(jobId),
        getTimeline(jobId),
      ])
        .then(([loadedLook, document, overlays, loadedTimeline]) => {
          if (!current) return;
          setLook(loadedLook);
          setPopups(overlays);
          setTimeline(loadedTimeline);
          if (document !== null) setCaptionsDoc(document);
        })
        .catch(() => current && live && setLiveFailed(true));
    }, 80);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [jobId, plan, live, videoVersion]);
  useLiveCaptions(live ? videoElement : null, live ? captions : null, () => setLiveFailed(true));

  // While the captions are dragged, the drawn captions follow the finger;
  // they settle when the document for their new place arrives.
  useEffect(() => setDragAnchor(null), [look]);
  useEffect(() => {
    const canvas = frame.current?.querySelector<HTMLCanvasElement>("canvas.JASSUB");
    if (!canvas) return;
    const shift = dragAnchor !== null && look ? dragAnchor - look.effective.anchor_y : 0;
    const height = frame.current?.getBoundingClientRect().height ?? 0;
    canvas.style.transform = shift ? `translateY(${shift * height}px)` : "";
  }, [dragAnchor, look, captions]);

  // --- the player ------------------------------------------------------------

  useEffect(() => {
    if (!playing) return;
    let frameId = 0;
    const step = () => {
      if (video.current) setTime(video.current.currentTime);
      frameId = requestAnimationFrame(step);
    };
    frameId = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frameId);
  }, [playing]);

  // The player plays the video, on its own clock: the plan with its cuts
  // and cold open applied (D-199). Edits go to the plan's scenes.
  const view: ScenePlan | null = timeline ?? plan;
  const duration = view ? view.total_frames / view.fps : 0;
  const seek = useCallback(
    (seconds: number) => {
      const to = Math.max(0, Math.min(seconds, Math.max(0, duration - 0.05)));
      if (video.current) video.current.currentTime = to;
      setTime(to);
    },
    [duration],
  );
  const togglePlay = useCallback(() => {
    const element = video.current;
    if (!element) return;
    if (element.paused) void element.play().catch(() => undefined);
    else element.pause();
  }, []);

  const words = useMemo(() => (view ? timedWords(view) : []), [view]);
  const sceneAt = useCallback(
    (seconds: number): PlannedScene | null => {
      if (!view) return null;
      const at = Math.floor(seconds * view.fps);
      return view.scenes.find((s) => at >= s.start_frame && at < s.end_frame) ?? view.scenes[view.scenes.length - 1] ?? null;
    },
    [view],
  );
  // The scene at the playhead in the video, and the plan's scene it is.
  const videoScene = sceneAt(time);
  const storyIndex = (s: PlannedScene) => (s as PlannedScene & { story_index?: number }).story_index ?? s.index;
  const scene = (videoScene && plan?.scenes.find((s) => s.index === storyIndex(videoScene))) ?? null;
  const storyTime = videoToStory(timeline, time) ?? (scene && plan ? scene.start_frame / plan.fps : time);
  const seekStory = useCallback((seconds: number) => seek(storyToVideo(timeline, seconds)), [seek, timeline]);
  const sceneNumber = (index: number) =>
    plan ? plan.scenes.slice(0, index + 1).filter((s) => !s.card_kind).length : 0;

  const goToScene = useCallback(
    (offset: number) => {
      if (!view || !videoScene) return;
      const position = view.scenes.findIndex((s) => s.index === videoScene.index);
      const here = videoScene.start_frame / view.fps;
      // Back goes to the start of this scene first, as editors do.
      const target =
        offset < 0 && time - here > 1 ? videoScene : view.scenes[Math.max(0, Math.min(view.scenes.length - 1, position + offset))];
      seek(target.start_frame / view.fps + 0.001);
    },
    [view, videoScene, time, seek],
  );

  // --- edits, history and updating ------------------------------------------------

  const pending = history?.pending ?? job.summary.pending_edits ?? 0;
  const changed = new Set(history?.changed_scenes ?? []);
  const changedPictures = new Set(history?.changed_pictures ?? []);

  const saveAnchor = useCallback(
    async (anchor: number) => {
      if (!look) return;
      try {
        const result = await setCaptions(jobId, { ...look.choice, anchor_y: Math.round(anchor * 1000) / 1000 });
        setPlan((current) => current && { ...current, captions: result.captions });
        setHistory(result);
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : "The captions could not be moved.");
        setDragAnchor(null);
      }
    },
    [jobId, look],
  );

  const afterEdit = useCallback(
    (result: EditResult | PlanEditResult) => {
      if ("plan" in result) setPlan(result.plan);
      else setPlan((current) => current && { ...current, scenes: current.scenes.map((s) => (s.index === result.scene.index ? result.scene : s)) });
      void getPlanHistory(jobId).then(setHistory);
    },
    [jobId],
  );

  const step = useCallback(
    async (direction: "undo" | "redo") => {
      if (status.kind === "updating") return;
      const can = direction === "undo" ? history?.can_undo : history?.can_redo;
      if (!can) return;
      const label = direction === "undo" ? history?.undo_label : history?.redo_label;
      try {
        await (direction === "undo" ? undoPlan(jobId) : redoPlan(jobId));
        await reload();
        say(`${direction === "undo" ? "Undone" : "Redone"}${label ? `: ${label}` : ""}`);
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : "That could not be undone.");
      }
    },
    [history, jobId, reload, say, status.kind],
  );

  const update = useCallback(async () => {
    if (status.kind === "updating") return;
    setError(null);
    resumeAt.current = video.current?.currentTime ?? 0;
    video.current?.pause();
    setStatus({ kind: "updating", message: "Starting", fraction: 0 });
    try {
      await rerenderJob(jobId);
    } catch (caught) {
      setStatus({ kind: "failed", message: caught instanceof Error ? caught.message : "Could not start." });
      return;
    }
    let settled = false;
    const finish = async (finished: Job) => {
      if (settled) return;
      if (finished.state === "running" || finished.state === "queued") return;
      settled = true;
      stop();
      window.clearInterval(poll);
      setJob(finished);
      if (finished.state === "succeeded") {
        setStatus({ kind: "idle" });
        setVideoVersion((v) => v + 1);
        await reload();
        say("Video updated. Unchanged scenes and sound came from the cache.");
      } else {
        setStatus({ kind: "failed", message: finished.error || finished.message || "The update stopped." });
      }
    };
    const stop = followJob(jobId, {
      onProgress: (event) => setStatus({ kind: "updating", message: event.message, fraction: event.fraction }),
      onState: (finished) => void finish(finished),
    });
    const poll = window.setInterval(() => {
      void getJob(jobId)
        .then(finish)
        .catch(() => undefined);
    }, 4000);
  }, [jobId, reload, say, status.kind]);

  // --- the keyboard ------------------------------------------------------------

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      const target = event.target as HTMLElement | null;
      const mod = event.ctrlKey || event.metaKey;
      if (shortcuts.current?.open) return;
      if (isTyping(target)) return;
      const onRange = target instanceof HTMLInputElement && target.type === "range";
      const key = event.key;
      if (key === " ") {
        if (target?.closest("button, a, summary, [role=tab]")) return; // Space presses the focused control
        event.preventDefault();
        togglePlay();
      } else if ((key === "ArrowRight" || key === "ArrowLeft") && !mod && !onRange && !target?.closest("[role=tablist]")) {
        event.preventDefault();
        goToScene(key === "ArrowRight" ? 1 : -1);
      } else if (key === "." && !mod) seek((video.current?.currentTime ?? time) + 1);
      else if (key === "," && !mod) seek((video.current?.currentTime ?? time) - 1);
      else if (mod && key.toLowerCase() === "z" && event.shiftKey) {
        event.preventDefault();
        void step("redo");
      } else if (mod && key.toLowerCase() === "z") {
        event.preventDefault();
        void step("undo");
      } else if (mod && key.toLowerCase() === "y") {
        event.preventDefault();
        void step("redo");
      } else if (key === "?") shortcuts.current?.showModal();
      else if (key === "[" && !mod) togglePanel();
      else if (key === "]" && !mod) toggleTimeline();
      else if (["1", "2", "3", "4", "5", "6"].includes(key) && !mod) setTab(TABS[Number(key) - 1].id);
      else if ((key === "+" || key === "=") && !mod) window.dispatchEvent(new CustomEvent("voxframe:zoom", { detail: 1.5 }));
      else if (key === "-" && !mod) window.dispatchEvent(new CustomEvent("voxframe:zoom", { detail: 1 / 1.5 }));
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [goToScene, seek, step, time, togglePlay, togglePanel, toggleTimeline]);

  // --- rendering ----------------------------------------------------------------

  if (error && !plan) return <Notice tone="error">{error}</Notice>;
  if (!plan || !scene || !view || !videoScene) return <p className="muted studio-loading">Opening the studio…</p>;

  const summary = job.summary ?? {};
  const hasVideo = job.artifacts.includes("video");
  const position = plan.scenes.findIndex((s) => s.index === scene.index);
  const previewing =
    changedPictures.has(scene.index) && (!!scene.asset || showsSpeaker(scene) || sharesFrame(scene));
  const statusText =
    status.kind === "updating"
      ? `Updating your video… ${status.fraction !== null ? `${Math.round(status.fraction * 100)}%` : ""}`
      : status.kind === "failed"
        ? "The update stopped"
        : pending > 0
          ? `${pending} ${pending === 1 ? "change" : "changes"} not yet in the video`
          : "Your video is ready";
  const usedIn = (assetId: string) =>
    plan.scenes.filter((s) => s.asset?.id === assetId && s.index !== scene.index).map((s) => sceneNumber(s.index));
  const firstSpoken = plan.scenes.findIndex((s) => !s.card_kind);
  const canStartChapter = !scene.card_kind && scene.index > firstSpoken && !plan.scenes[scene.index - 1]?.card_kind;
  const sceneWords = words.filter((w) => w.scene === videoScene.index);

  return (
    <div className="studio" data-timeline={layout.timelineOpen ? "open" : "closed"}>
      <div className="studio-bar">
        <div className="studio-project">
          <strong>{plan.scenes.find((s) => s.card_kind === "title")?.card_text || job.audio_name || "Your video"}</strong>
          <span>
            {clock(duration)} · {plan.aspect} · {plan.style}
            {plan.score ? ` · ${plan.score.style} score` : plan.music_path ? " · your track" : ""}
          </span>
        </div>
        <span className="spacer" />
        <div className="studio-status" data-state={status.kind === "idle" ? (pending > 0 ? "pending" : "ready") : status.kind} role="status" aria-live="polite">
          <span className="dot" aria-hidden="true" />
          <span>{statusText}</span>
          {status.kind === "updating" && (
            <span className="studio-status-bar" aria-hidden="true">
              <i style={{ width: `${Math.round((status.fraction ?? 0) * 100)}%` }} />
            </span>
          )}
        </div>
        <button
          type="button"
          className="btn btn-quiet"
          disabled={!history?.can_undo || status.kind === "updating"}
          title={history?.undo_label ? `Undo: ${history.undo_label} (Ctrl+Z)` : "Undo (Ctrl+Z)"}
          onClick={() => void step("undo")}
        >
          Undo
        </button>
        <button
          type="button"
          className="btn btn-quiet"
          disabled={!history?.can_redo || status.kind === "updating"}
          title={history?.redo_label ? `Redo: ${history.redo_label} (Ctrl+Shift+Z)` : "Redo (Ctrl+Shift+Z)"}
          onClick={() => void step("redo")}
        >
          Redo
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={pending === 0 || status.kind === "updating"}
          onClick={() => void update()}
        >
          Update video
        </button>
        <button
          type="button"
          className="btn btn-quiet btn-small"
          aria-pressed={layout.panelOpen}
          title="Show or hide the side panel ([)"
          onClick={togglePanel}
        >
          Panel
        </button>
        <button
          type="button"
          className="btn btn-quiet btn-small"
          aria-pressed={layout.timelineOpen}
          title="Show or hide the timeline (])"
          onClick={toggleTimeline}
        >
          Timeline
        </button>
        <details className="studio-menu">
          <summary className="btn btn-quiet">Download</summary>
          <div className="studio-menu-list" role="menu">
            {job.artifacts.filter((name) => name !== "studio").map((name) => (
              <a key={name} role="menuitem" href={artifactUrl(job.id, name)} download>
                {ARTIFACT_LABELS[name] ?? name}
              </a>
            ))}
            {summary.saved_to && (
              <button type="button" role="menuitem" onClick={() => void openFolder("videos").catch(() => undefined)}>
                Open the videos folder
              </button>
            )}
            <button type="button" role="menuitem" onClick={onShowPlan}>
              The scene plan, scene by scene
            </button>
          </div>
        </details>
        <button type="button" className="btn btn-quiet" aria-label="Keyboard shortcuts" onClick={() => shortcuts.current?.showModal()}>
          ?
        </button>
      </div>

      <div
        className="studio-work"
        data-panel={layout.panelOpen ? "open" : "closed"}
        style={{ "--panel-width": `${layout.panel}px` } as React.CSSProperties}
      >
        <section className="studio-stage" aria-label="Player">
          <div className="studio-player">
            <div className="studio-frame" ref={frame} style={{ aspectRatio: plan.aspect.replace(":", " / ") }}>
              {hasVideo && (
                <video
                  ref={(element) => {
                    video.current = element;
                    setVideoElement(element);
                  }}
                  key={`${videoVersion}-${live ? "live" : "burned"}`}
                  src={`${artifactUrl(job.id, live ? "studio" : "video")}?v=${videoVersion}`}
                  preload="auto"
                  muted={muted}
                  onPlay={() => setPlaying(true)}
                  onPause={() => {
                    setPlaying(false);
                    if (video.current) setTime(video.current.currentTime);
                  }}
                  onSeeked={() => video.current && setTime(video.current.currentTime)}
                  onLoadedMetadata={() => {
                    if (video.current && resumeAt.current) video.current.currentTime = resumeAt.current;
                  }}
                  onClick={togglePlay}
                  aria-label="The video"
                />
              )}
              {previewing && (
                <img className="studio-preview" src={sceneThumbnail(jobId, scene)} alt="" />
              )}
              {(changed.has(scene.index) || history?.captions_changed) && (
                <span className="studio-preview-badge">Preview · not yet in the video</span>
              )}
              <PopupLayer
                jobId={jobId}
                showPictures={live}
                state={popups}
                time={time}
                selected={tab === "popups" ? selectedPopup : null}
                frame={frame}
                onMoved={(moved) => {
                  setPopups((current) => current && { ...current, overlays: current.overlays.map((o) => (o.id === moved.id ? moved : o)) });
                  void updateOverlay(jobId, moved)
                    .then((result) => {
                      setPlan((current) => current && { ...current });
                      setHistory(result);
                    })
                    .catch((caught: unknown) => setError(caught instanceof ApiError ? caught.message : "The pop-up could not be moved."));
                }}
              />
              {placing && look && (
                <CaptionGuide
                  anchor={dragAnchor ?? look.effective.anchor_y}
                  frame={frame}
                  onMove={setDragAnchor}
                  onDrop={(anchor) => void saveAnchor(anchor)}
                />
              )}
            </div>
          </div>
          <div className="studio-transport">
            <button type="button" className="studio-play" onClick={togglePlay} aria-label={playing ? "Pause (Space)" : "Play (Space)"}>
              {playing ? (
                <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M2 1h4v12H2zM8 1h4v12H8z" fill="currentColor" /></svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 1l10 6-10 6z" fill="currentColor" /></svg>
              )}
            </button>
            <span className="studio-time">
              <b>{clock(time)}</b> / {clock(duration)}
            </span>
            <input
              className="studio-scrub"
              type="range"
              min={0}
              max={duration}
              step={0.05}
              value={time}
              aria-label="Position in the video"
              onChange={(event) => seek(Number(event.target.value))}
            />
            <span className="studio-scene-name">
              {scene.card_kind ? `${scene.card_kind === "title" ? "Title" : "Chapter"} card` : `Scene ${sceneNumber(scene.index)}`} of{" "}
              {plan.scenes.length}
            </span>
            <button type="button" className="btn btn-quiet btn-small" aria-pressed={muted} onClick={() => setMuted((m) => !m)}>
              {muted ? "Sound off" : "Sound on"}
            </button>
            <button type="button" className="btn btn-quiet btn-small" onClick={() => void frame.current?.requestFullscreen?.()}>
              Full screen
            </button>
          </div>
          {(status.kind === "failed" || job.warnings.length > 0 || error) && (
            <div className="studio-notes">
              {status.kind === "failed" && <Notice tone="error">{status.message}</Notice>}
              {error && <Notice tone="error">{error}</Notice>}
              {job.warnings.map((warning, index) => (
                <Notice key={index} tone="warn">
                  {warning}
                </Notice>
              ))}
            </div>
          )}
        </section>

        {layout.panelOpen && (
          <Resizer
            orientation="vertical"
            label="Resize the side panel"
            value={layout.panel}
            range={PANEL}
            onChange={(panel) => setLayout((l) => ({ ...l, panel }))}
          />
        )}
        {layout.panelOpen && (
        <aside className="studio-panel" aria-label="Edit">
          <div className="studio-tabs" role="tablist" aria-label="Edit">
            {TABS.map((entry, index) => (
              <button
                key={entry.id}
                type="button"
                role="tab"
                id={`tab-${entry.id}`}
                aria-controls={`panel-${entry.id}`}
                aria-selected={tab === entry.id}
                tabIndex={tab === entry.id ? 0 : -1}
                title={`${entry.label} (${index + 1})`}
                onClick={() => setTab(entry.id)}
                onKeyDown={(event) => {
                  if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
                  event.preventDefault();
                  const next = TABS[(index + (event.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
                  setTab(next.id);
                  document.getElementById(`tab-${next.id}`)?.focus();
                }}
              >
                {entry.label}
              </button>
            ))}
          </div>

          <div className="studio-tabpanel" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
            {tab === "scenes" && (
              <>
                {position === 0 && !plan.scenes.some((s) => s.card_kind === "title") && (
                  <div className="studio-tool">
                    <TitleAdder
                      busy={addingTitle}
                      onAdd={async (text) => {
                        setAddingTitle(true);
                        try {
                          afterEdit(await addTitle(jobId, text));
                        } catch (caught) {
                          setError(caught instanceof ApiError ? caught.message : "The title could not be added.");
                        } finally {
                          setAddingTitle(false);
                        }
                      }}
                    />
                  </div>
                )}
                <SceneDetail
                  key={`${scene.index}-${planVersion}`}
                  jobId={jobId}
                  scene={scene}
                  fps={plan.fps}
                  number={sceneNumber(scene.index)}
                  usedIn={usedIn}
                  sourcingEnabled={sourcingEnabled}
                  canStartChapter={canStartChapter}
                  onEdited={afterEdit}
                  onPlanEdited={(result, select) => {
                    afterEdit(result);
                    const target = result.plan.scenes[Math.min(select, result.plan.scenes.length - 1)];
                    if (target) seekStory(target.start_frame / result.plan.fps + 0.001);
                  }}
                  part="picture"
                />
                {plan.footage && !scene.card_kind && scene.footage_start != null && (
                  <LayoutPicker
                    jobId={jobId}
                    scene={scene}
                    vertical={plan.aspect !== "16:9"}
                    onSaved={(edited, where) => {
                      setPlan((current) => current && { ...current, scenes: current.scenes.map((s) => (s.index === edited.index ? edited : s)) });
                      setHistory(where);
                    }}
                  />
                )}
              </>
            )}

            {tab === "captions" &&
              (scene.card_kind ? (
                <Notice tone="info">A card has no words: they begin in the next scene.</Notice>
              ) : (
                <>
                  {!live && (
                    <Notice tone="info">
                      {job.artifacts.includes("studio") || !liveCaptionsSupported()
                        ? "This browser cannot draw captions live: changes show once you update the video."
                        : "Update the video once and caption changes will show in the player the moment you make them."}
                    </Notice>
                  )}
                  <CaptionStyle
                    jobId={jobId}
                    look={look}
                    placing={placing}
                    onPlacing={setPlacing}
                    onSaved={(choice, where) => {
                      setPlan((current) => current && { ...current, captions: choice });
                      setHistory(where);
                    }}
                  />
                  <SceneCaptionStyle
                    key={`${scene.index}-${planVersion}-style`}
                    jobId={jobId}
                    scene={scene}
                    onSaved={(edited, where) => {
                      setPlan((current) => current && { ...current, scenes: current.scenes.map((s) => (s.index === edited.index ? edited : s)) });
                      setHistory(where);
                    }}
                  />
                  <div className="studio-words" role="group" aria-label="Words in this scene: choose one to jump to it">
                    {sceneWords.map((word, index) => (
                      <button
                        key={index}
                        type="button"
                        className={time >= word.start && time < word.end + 0.08 ? "now" : ""}
                        onClick={() => seek(word.start)}
                      >
                        {word.text}
                      </button>
                    ))}
                  </div>
                  <SceneDetail
                    key={`${scene.index}-${planVersion}-caption`}
                    jobId={jobId}
                    scene={scene}
                    fps={plan.fps}
                    number={sceneNumber(scene.index)}
                    usedIn={usedIn}
                    sourcingEnabled={sourcingEnabled}
                    canStartChapter={false}
                    onEdited={afterEdit}
                    onPlanEdited={afterEdit}
                    part="caption"
                  />
                </>
              ))}

            {tab === "popups" &&
              (scene.card_kind ? (
                <Notice tone="info">A card has no words for a pop-up to appear on: choose a spoken scene.</Notice>
              ) : (
                <PopupsPanel
                  jobId={jobId}
                  scene={scene}
                  scenes={plan.scenes}
                  state={popups}
                  time={storyTime}
                  selected={selectedPopup}
                  onSelect={setSelectedPopup}
                  onSeek={seekStory}
                  onSaved={(state, where) => {
                    setPopups(state);
                    setHistory(where);
                    // The live document draws the text, shapes and counters.
                    setPlan((current) => current && { ...current });
                  }}
                />
              ))}

            {tab === "pace" && (
              <PacePanel
                jobId={jobId}
                time={time}
                refresh={planVersion}
                footage={!!plan.footage}
                onSeek={seek}
                onSaved={(where) => {
                  setHistory(where);
                  // The timeline, the captions and the pop-ups' times follow.
                  setPlan((current) => current && { ...current });
                }}
              />
            )}

            {tab === "sound" && hasVideo && (
              <SoundPanel
                key={planVersion}
                jobId={jobId}
                playhead={() => video.current?.currentTime ?? 0}
                onApply={async () => {
                  await reload();
                  await update();
                }}
              />
            )}

            {tab === "style" && (
              <div className="card">
                <header>
                  <h2>How it was made</h2>
                  <p>
                    {plan.style} template · {plan.aspect} · {summary.width}×{summary.height} · transcribed with{" "}
                    {plan.transcribe_model}
                    {plan.language ? ` (${plan.language})` : ""}
                  </p>
                </header>
                <dl className="facts">
                  <div><dt>Scenes</dt><dd>{summary.scenes ?? plan.scenes.length}</dd></div>
                  <div><dt>With imagery</dt><dd>{summary.matched_scenes ?? 0}</dd></div>
                  <div><dt>Words</dt><dd>{summary.words ?? words.length}</dd></div>
                  <div><dt>Render time</dt><dd>{Math.round(summary.elapsed_seconds ?? 0)}s</dd></div>
                </dl>
                <p className="muted">
                  The template and shape change the whole edit, so they cannot be changed here:
                  make the video again from the recording to use another.
                </p>
                <button type="button" className="btn" onClick={onAgain}>
                  Make another video
                </button>
                {(summary.credits ?? []).length > 0 && (
                  <>
                    <h3 style={{ marginTop: 18 }}>Credits</h3>
                    <p className="muted">
                      Most sources require attribution. These lines describe exactly what was used in
                      this video, and are included in the download.
                    </p>
                    <ul className="list-plain">
                      {(summary.credits ?? []).map((line, index) => (
                        <li key={index}>{line}</li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            )}
          </div>
        </aside>
        )}
      </div>

      {layout.timelineOpen && (
        <Resizer
          orientation="horizontal"
          label="Resize the timeline"
          value={layout.timeline}
          range={LANE}
          onChange={(timeline) => setLayout((l) => ({ ...l, timeline }))}
        />
      )}
      {layout.timelineOpen && (
      <Timeline
        jobId={jobId}
        plan={view}
        words={words}
        time={time}
        currentScene={videoScene.index}
        changed={changed}
        onSeek={seek}
        sceneLane={layout.timeline}
        pieces={timeline?.pieces}
        onScene={(index) => {
          const target = view.scenes.find((s) => s.index === index);
          if (target) seek(target.start_frame / view.fps + 0.001);
          setTab("scenes");
          setLayout((l) => (l.panelOpen ? l : { ...l, panelOpen: true }));
        }}
      />
      )}

      <dialog ref={shortcuts} className="studio-dialog" aria-labelledby="shortcuts-title">
        <h2 id="shortcuts-title">Keyboard shortcuts</h2>
        <dl>
          <dt><kbd>Space</kbd></dt><dd>Play or pause</dd>
          <dt><kbd>←</kbd> <kbd>→</kbd></dt><dd>Previous or next scene</dd>
          <dt><kbd>,</kbd> <kbd>.</kbd></dt><dd>Back or forward one second</dd>
          <dt><kbd>Ctrl</kbd>+<kbd>Z</kbd></dt><dd>Undo the last change</dd>
          <dt><kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd></dt><dd>Redo</dd>
          <dt><kbd>1</kbd>–<kbd>6</kbd></dt><dd>Scenes, Captions, Sound, Style, Pop-ups, Hook &amp; pace</dd>
          <dt><kbd>+</kbd> <kbd>−</kbd></dt><dd>Zoom the timeline</dd>
          <dt><kbd>[</kbd> <kbd>]</kbd></dt><dd>Hide or show the side panel, the timeline</dd>
          <dt><kbd>?</kbd></dt><dd>This list</dd>
        </dl>
        <p className="muted">Shortcuts never act while you are typing in a field.</p>
        <form method="dialog">
          <button className="btn btn-primary">Close</button>
        </form>
      </dialog>

      <div className={`studio-toast${toast ? " on" : ""}`} role="status" aria-live="polite">
        {toast}
      </div>
    </div>
  );
}

/**
 * A line across the player marking where the middle of the captions sits,
 * dragged to move them (D-196). Also moved with the arrow keys.
 */
function CaptionGuide({
  anchor,
  frame,
  onMove,
  onDrop,
}: {
  anchor: number;
  frame: React.RefObject<HTMLDivElement>;
  onMove: (anchor: number) => void;
  onDrop: (anchor: number) => void;
}) {
  const dragging = useRef(false);
  const clamp = (value: number) => Math.min(0.95, Math.max(0.05, value));
  const at = (clientY: number) => {
    const box = frame.current?.getBoundingClientRect();
    return box ? clamp((clientY - box.top) / box.height) : anchor;
  };
  return (
    <div
      className="caption-guide"
      style={{ top: `${anchor * 100}%` }}
      role="slider"
      tabIndex={0}
      aria-label="Where the captions sit"
      aria-valuemin={5}
      aria-valuemax={95}
      aria-valuenow={Math.round(anchor * 100)}
      aria-valuetext={`${Math.round(anchor * 100)}% down the picture`}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        dragging.current = true;
        onMove(at(event.clientY));
      }}
      onPointerMove={(event) => dragging.current && onMove(at(event.clientY))}
      onPointerUp={(event) => {
        if (!dragging.current) return;
        dragging.current = false;
        onDrop(at(event.clientY));
      }}
      onKeyDown={(event) => {
        if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
        event.preventDefault();
        event.stopPropagation();
        onDrop(clamp(anchor + (event.key === "ArrowUp" ? -0.02 : 0.02)));
      }}
    >
      <span>Captions</span>
    </div>
  );
}
