/**
 * The API client.
 *
 * Every request is same-origin and relies on the session cookie, which is
 * HttpOnly and therefore unreadable from here — by design. This module never
 * holds a credential after {@link establishSession} has run.
 */

export interface StyleInfo {
  name: string;
  description: string;
}

export interface Capabilities {
  ffmpeg: { available: boolean; version?: string; has_libass?: boolean; error?: string };
  styles: StyleInfo[];
  aspects: string[];
  qualities: string[];
  library: { present: boolean; assets: number; path: string };
  sourcing: { adapters: string[]; available: boolean };
  profile: string;
  /** Fitting a person's own music needs a one-time download first (D-172). */
  music_component?: { ready: boolean; download_mb: number | null };
  /** The generated score's styles, and whether its sounds are installed (D-176). */
  score?: {
    ready: boolean;
    reason: string;
    styles: { name: string; label: string; description: string }[];
  };
}

export interface SourcingSettings {
  consent: boolean | null;
  has_been_asked: boolean;
  enabled: boolean;
  consent_version?: number;
  current_version?: number;
}

export type Theme = "system" | "dark" | "light";

export interface UserSettings {
  sourcing: SourcingSettings;
  api_keys: Record<string, string>;
  /** The app's look (D-185). */
  theme: Theme;
  adapters: string[];
  environment_keys: string[];
}

export interface UploadResult {
  upload_id: string;
  name: string;
  bytes: number;
  duration_seconds: number | null;
  /** The file has a picture of its own to show: "Use my video" applies (D-192). */
  has_video?: boolean;
}

export interface JobSummary {
  width?: number;
  height?: number;
  scenes?: number;
  matched_scenes?: number;
  /** Scenes that could carry imagery: every scene except cards. */
  illustratable_scenes?: number;
  fill_rate?: number;
  words?: number;
  language?: string;
  elapsed_seconds?: number;
  realtime_factor?: number;
  credits?: string[];
  /** Edits saved to the plan since the video was last rendered. */
  pending_edits?: number;
  /** Scenes whose image a person chose. */
  edited_scenes?: number;
  /** Where the installed app placed the finished video (D-156). */
  saved_to?: string | null;
  /** Plain scenes that offer close matches: one click from an image. */
  close_match_scenes?: number;
  /** Scenes showing a calm image on the recording's theme, not a match. */
  atmospheric_scenes?: number;
  /** The sound's measurements and checks at the last render (D-171). */
  sound?: SoundCheck | null;
  audio_mix?: AudioMix | null;
}

/** The person's sound settings, stored in the scene plan (D-171). */
export interface AudioMix {
  voice_db: number;
  music_db: number;
  speech_margin_db: number;
  destination: string;
  /** Voice polish (D-173); off, the voice is exactly the recording. */
  voice_polish: boolean;
  /** A generated score's group levels, dB (D-179). */
  score_levels: ScoreLevels;
}

/** A generated score's instrument groups (D-179). */
export const SCORE_GROUPS = ["piano", "strings", "percussion", "bass", "pads"] as const;
export type ScoreGroup = (typeof SCORE_GROUPS)[number];
export type ScoreLevels = Record<ScoreGroup, number>;

/** The video's music as the Sound card changes it (D-179). */
export interface MusicDraft {
  choice: "none" | "own" | "score";
  style: string | null;
  intensity: number;
  seed: number | null;
  /** A track uploaded in the studio, not yet applied (D-184), and its name. */
  upload_id?: string | null;
  upload_name?: string | null;
  /** The own track's credit, as the person states it. */
  credit?: string;
  /** Remove the person's track, so it is no longer offered. */
  forget_track?: boolean;
}

export interface MusicState extends MusicDraft {
  /** The person's own track, if this video had one to go back to. */
  track_name: string | null;
  track_credit: string;
  styles: { name: string; label: string; description: string }[];
  score_ready: boolean;
}

/** What voice polish measured and did (D-173). */
export interface PolishReport {
  noise_floor_db: number;
  reduction_db: number;
  reduction_note: string;
  boxiness_cut: boolean;
  room_tone_db: number;
  pause_floor_db: number;
  high_change_db: number;
  music_in_recording: boolean;
  problems: string[];
}

export interface Destination {
  id: string;
  label: string;
  lufs: number;
  true_peak: number;
}

/** What the last render measured, and whether it passed. */
export interface SoundCheck {
  destination: string;
  target_lufs: number;
  target_true_peak: number;
  integrated_lufs: number;
  true_peak: number;
  min_speech_margin_db: number | null;
  speech_margin_setting_db: number;
  clicks_at: number[];
  clipped: boolean;
  problems: string[];
  passed: boolean;
}

export interface MixState {
  audio_mix: AudioMix;
  destinations: Destination[];
  too_close: boolean;
  comfortable_margin_db: number;
  has_music: boolean;
  can_preview: boolean;
  last_check: SoundCheck | null;
  music: MusicState;
  polish: PolishReport | null;
  /** The recording already has music under the voice. */
  music_in_recording: boolean;
  pending_edits?: number;
}

export interface Job {
  id: string;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
  /** Whether starting it again makes sense: interrupted, failed or stopped. */
  resumable: boolean;
  stage: string;
  message: string;
  created_at: string;
  audio_name: string;
  error: string;
  warnings: string[];
  artifacts: string[];
  summary: JobSummary;
}

export interface RenderOptions {
  upload_id: string;
  aspect: string;
  quality: string;
  height: number;
  style: string | null;
  title: string;
  chapters: boolean;
  highlights_seconds: number | null;
  use_library: boolean;
  /** Show the recording's own picture, with matched pictures as cutaways (D-192). */
  use_video?: boolean;
  /** A music bed, uploaded like the recording (D-148). */
  music_upload_id?: string | null;
  /** Attribution for the track, for the credits. */
  music_credit?: string;
  /** Compose music for the video in this style instead (D-176). */
  score_style?: string | null;
}


/** One asset as the plan records it. Denormalised, so credits need no library. */
export interface PlanAsset {
  id: string;
  path: string;
  width: number;
  height: number;
  kind: string;
  duration: number | null;
  license_name: string;
  license_author: string;
  license_source: string;
  license_url: string;
  /** How closely a recorded candidate matched; null for the chosen one. */
  similarity: number | null;
  /** The image's subject is printed text, which competes with captions. */
  prints_text: boolean;
}

export interface PlanWord {
  text: string;
  start: number;
  end: number;
}

/** One scene, exactly as the renderer will read it. */
export interface PlannedScene {
  index: number;
  start_frame: number;
  end_frame: number;
  text: string;
  caption_text: string;
  caption_treatment?: CaptionTreatment | null;
  caption_emphasis?: number[];
  transition_after?: TransitionTreatment | null;
  words: PlanWord[];
  card_kind: string;
  card_text: string;
  queries: string[];
  query_source: string;
  asset: PlanAsset | null;
  alternatives: PlanAsset[];
  /** Candidates just under the threshold, offered as "use anyway". */
  near_misses: PlanAsset[];
  /** "user" once a person has chosen, swapped or removed the image. */
  asset_source: string;
  motion: string;
  motion_reason: string;
  /** What the scene shows when the video has the speaker's footage (D-192). */
  shot?: "picture" | "speaker";
  /** "user" once a person has chosen the shot. */
  shot_source?: string;
  shot_reason?: string;
  /** Where the scene starts in the recording; null for a card or no footage. */
  footage_start?: number | null;
  audio_start?: number | null;
  match_score: number;
  semantic_score: number;
  match_reason: string;
}

/** The scene plan: the renderer's only input, and what the filmstrip shows. */
export interface ScenePlan {
  version: number;
  audio_path: string;
  audio_duration: number;
  fps: number;
  total_frames: number;
  aspect: string;
  style: string;
  caption_treatment?: CaptionTreatment | null;
  transition_treatment?: TransitionTreatment | null;
  language: string;
  language_probability: number;
  transcribe_model: string;
  embed_model: string;
  music_path: string;
  music_credit: string;
  /** Music generated for the video (D-176). */
  score?: { style: string; seed: number; intensity: number } | null;
  /** The recording's own picture, when the video shows the speaker (D-192). */
  footage?: { path: string; width: number; height: number; subject_x: number } | null;
  created_at: string;
  scenes: PlannedScene[];
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    // The cookie is the credential. "same-origin" is the default, but stating
    // it makes the intent explicit next to a comment that explains it.
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(init.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...(init.headers ?? {}),
    },
  });

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {
      // A non-JSON error body is not worth a second failure.
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * Exchange the launch token in the URL for a session cookie, then remove it.
 *
 * The token arrives in the query string because on the very first navigation
 * nothing has run in the page that could set a header. Leaving it there would
 * put a live credential in the address bar, in history, in any bookmark, and in
 * any screenshot of the window.
 *
 * So: exchange it once, then `history.replaceState` it away. The cookie that
 * replaces it is HttpOnly and SameSite=Strict, so this module cannot read it
 * back and no cross-site request carries it.
 */
export async function establishSession(): Promise<boolean> {
  const url = new URL(window.location.href);
  const token = url.searchParams.get("token");

  if (token) {
    try {
      await request("/api/session", {
        method: "POST",
        headers: { "x-voxframe-token": token },
      });
    } finally {
      // Strip it whether or not the exchange succeeded: a token that failed is
      // still a secret, and still does not belong in the address bar.
      url.searchParams.delete("token");
      window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    }
  }

  try {
    await request("/api/capabilities");
    return true;
  } catch {
    return false;
  }
}

export const getCapabilities = () => request<Capabilities>("/api/capabilities");

export const getSettings = () => request<UserSettings>("/api/settings");

export const saveSettings = (body: {
  sourcing_consent?: boolean;
  api_keys?: Record<string, string>;
  theme?: Theme;
}) => request<unknown>("/api/settings", { method: "PUT", body: JSON.stringify(body) });

export const checkKey = (adapter: string, key: string) =>
  request<{ adapter: string; valid: boolean; detail: string }>(
    "/api/settings/check-key",
    { method: "POST", body: JSON.stringify({ adapter, key }) },
  );

export async function uploadAudio(
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<UploadResult> {
  // XMLHttpRequest rather than fetch: fetch cannot report upload progress, and
  // a 400 MB lecture uploading with no feedback looks like a hang.
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/uploads");
    xhr.withCredentials = true;

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as UploadResult);
      } else {
        let detail = `Upload failed (${xhr.status})`;
        try {
          const parsed = JSON.parse(xhr.responseText);
          if (parsed?.detail) detail = parsed.detail;
        } catch {
          /* keep the generic message */
        }
        reject(new ApiError(xhr.status, detail));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "Upload failed: no response."));
    xhr.send(form);
  });
}

export const submitJob = (options: RenderOptions) =>
  request<Job>("/api/jobs", { method: "POST", body: JSON.stringify(options) });

export const getJob = (id: string) => request<Job>(`/api/jobs/${id}`);

export const listJobs = () => request<{ jobs: Job[] }>("/api/jobs");

export const cancelJob = (id: string) =>
  request<unknown>(`/api/jobs/${id}/cancel`, { method: "POST" });

export const artifactUrl = (id: string, name: string) =>
  `/api/jobs/${id}/artifacts/${name}`;

/**
 * Follow a job's progress.
 *
 * Returns an unsubscribe function. EventSource sends the session cookie
 * automatically on a same-origin URL, so no credential is handled here.
 */
export function followJob(
  id: string,
  handlers: {
    onProgress?: (event: { stage: string; message: string; fraction: number | null }) => void;
    onState?: (job: Job) => void;
    onError?: () => void;
  },
): () => void {
  const source = new EventSource(`/api/jobs/${id}/events`, { withCredentials: true });

  source.addEventListener("progress", (event) => {
    handlers.onProgress?.(JSON.parse((event as MessageEvent).data));
  });

  source.addEventListener("state", (event) => {
    handlers.onState?.(JSON.parse((event as MessageEvent).data) as Job);
    source.close();
  });

  source.onerror = () => {
    // EventSource retries on its own; this fires on every transient blip, so
    // it is reported but never treated as terminal.
    handlers.onError?.();
  };

  return () => source.close();
}

export const getPlan = (jobId: string) =>
  request<ScenePlan>(`/api/jobs/${jobId}/plan`);

/** URL of a scene's thumbnail. Resolved server-side; never a path from here. */
export const thumbnailUrl = (jobId: string, index: number) =>
  `/api/jobs/${jobId}/scenes/${index}/thumbnail`;

/**
 * URL of one of a scene's recorded candidates, for "use this image anyway".
 * The candidate is named by id; the server resolves the file.
 */
export const candidateThumbnailUrl = (jobId: string, index: number, assetId: string) =>
  `/api/jobs/${jobId}/scenes/${index}/candidates/${encodeURIComponent(assetId)}/thumbnail`;

export interface EditResult {
  scene: PlannedScene;
  pending_edits: number;
}

/** Show one of the scene's recorded candidates: a runner-up or a near miss. */
export const chooseImage = (jobId: string, index: number, assetId: string) =>
  request<EditResult>(`/api/jobs/${jobId}/scenes/${index}/image`, {
    method: "PUT",
    body: JSON.stringify({ action: "choose", asset_id: assetId }),
  });

/** One model a profile needs (D-157). */
export interface ModelNeed {
  label: string;
  purpose: string;
  mb: number;
  ready: boolean;
}

export interface ModelChoice {
  profile: "standard" | "lite";
  total_mb: number;
  to_download_mb: number;
  ready: boolean;
  models: ModelNeed[];
}

export interface ModelDownload {
  completed_assets: number;
  total_assets: number;
  state: "idle" | "downloading" | "updating" | "done" | "failed";
  current: string;
  received_mb: number;
  total_mb: number;
  seconds_left: number | null;
  message: string;
}

export interface ModelStatus {
  profile: "standard" | "lite";
  chosen: string | null;
  profile_from_environment: boolean;
  ready: boolean;
  choices: ModelChoice[];
  download: ModelDownload;
  models_folder: string;
}

export const getModelStatus = () => request<ModelStatus>("/api/setup/models");

/** Start downloading a profile's models. Nothing downloads until this is called. */
export const downloadModels = (profile: "standard" | "lite") =>
  request<ModelDownload>("/api/setup/models/download", {
    method: "POST",
    body: JSON.stringify({ profile }),
  });

/** Where Voxframe keeps things on this computer (D-156). */
export interface Folders {
  library: string;
  videos: string;
  data: string;
  /** A library chosen in Settings, used from the next start. */
  library_pending: string | null;
  library_from_environment: boolean;
}

export type FolderName = "library" | "videos" | "data";

export const getFolders = () => request<Folders>("/api/folders");

export const openFolder = (which: FolderName) =>
  request<{ opened: string }>("/api/folders/open", {
    method: "POST",
    body: JSON.stringify({ which }),
  });

/** Opens the system's folder window on this computer; the page sends no path. */
export const chooseLibraryFolder = () =>
  request<{ changed: boolean; library_pending?: string }>("/api/folders/library/choose", {
    method: "POST",
  });

export interface UpdateCheck {
  current: string;
  latest: string | null;
  newer: boolean;
  url: string;
}

/** Ask for the latest release. Only ever called by the button (D-155). */
export const checkForUpdates = () =>
  request<UpdateCheck>("/api/updates/check", { method: "POST" });

/** Show the speaker or the scene's picture (D-192). */
export const setShot = (jobId: string, index: number, shot: "speaker" | "picture") =>
  request<EditResult>(`/api/jobs/${jobId}/scenes/${index}/shot`, {
    method: "PUT",
    body: JSON.stringify({ shot }),
  });

/** Turn a scene's camera movement on or off (D-154). */
export const setMotion = (jobId: string, index: number, on: boolean) =>
  request<EditResult>(`/api/jobs/${jobId}/scenes/${index}/motion`, {
    method: "PUT",
    body: JSON.stringify({ on }),
  });

/** Show a plain background instead, deliberately. */
export const removeImage = (jobId: string, index: number) =>
  request<EditResult>(`/api/jobs/${jobId}/scenes/${index}/image`, {
    method: "PUT",
    body: JSON.stringify({ action: "remove" }),
  });

/** Use a photograph the person supplies for one scene. */
export function uploadOwnImage(jobId: string, index: number, file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<EditResult>(`/api/jobs/${jobId}/scenes/${index}/image/own`, {
    method: "POST",
    body: form,
  });
}

/**
 * The result of adding, removing or retitling a card. The whole plan comes
 * back, because adding or removing a card moves every later scene (D-145).
 */
export interface PlanEditResult {
  plan: ScenePlan;
  pending_edits: number;
}

/** Change what a title or chapter card says. */
export const editCardText = (jobId: string, index: number, text: string) =>
  request<PlanEditResult>(`/api/jobs/${jobId}/scenes/${index}/card`, {
    method: "PUT",
    body: JSON.stringify({ text }),
  });

/** Take a card out; later scenes move up. */
export const removeCard = (jobId: string, index: number) =>
  request<PlanEditResult>(`/api/jobs/${jobId}/scenes/${index}/card`, {
    method: "DELETE",
  });

/** Open the video with a title card. */
export const addTitle = (jobId: string, text: string) =>
  request<PlanEditResult>(`/api/jobs/${jobId}/cards`, {
    method: "POST",
    body: JSON.stringify({ kind: "title", text }),
  });

/** Start a chapter before a scene; without text it takes the scene's opening words. */
export const addChapter = (jobId: string, before: number, text = "") =>
  request<PlanEditResult>(`/api/jobs/${jobId}/cards`, {
    method: "POST",
    body: JSON.stringify({ kind: "chapter", before, text }),
  });

/** One asset in the library, as the Library screen shows it (D-146). */
export interface LibraryAsset {
  id: string;
  kind: "image" | "video";
  width: number;
  height: number;
  duration: number | null;
  author: string;
  license: string;
  source: string;
  source_url: string;
  added_at: string | null;
  /** Whether the Library screen added it, and so owns its file. */
  uploaded_here: boolean;
  used_in: { job_id: string; title: string }[];
}

export interface LibraryPage {
  total: number;
  all: number;
  sources: Record<string, number>;
  /** The name the person last credited their uploads to. */
  author: string;
  assets: LibraryAsset[];
}

export interface LibraryUploadResult {
  added: number;
  already_there: number;
  failed: number;
  similar: number;
}

export const listLibrary = (source = "", offset = 0, limit = 60) =>
  request<LibraryPage>(
    `/api/library?source=${encodeURIComponent(source)}&offset=${offset}&limit=${limit}`,
  );

export const libraryThumbnailUrl = (assetId: string) =>
  `/api/library/${encodeURIComponent(assetId)}/thumbnail`;

/** Add the person's own photos and clips, credited to them. */
export function uploadToLibrary(files: File[], author: string, license: string) {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  form.append("author", author);
  form.append("license", license);
  return request<LibraryUploadResult>("/api/library", { method: "POST", body: form });
}

export const deleteLibraryAsset = (assetId: string) =>
  request<{ removed: boolean; file_deleted: boolean }>(
    `/api/library/${encodeURIComponent(assetId)}`,
    { method: "DELETE" },
  );

/** One online search result. Named by a token; its address stays on the server. */
export interface SearchResult {
  token: string;
  title: string;
  author: string;
  license: string;
  source: string;
  width: number;
  height: number;
}

export interface SearchResponse {
  results: SearchResult[];
  /** Services that did not answer, by name only. */
  failed_sources: string[];
}

/** Search the image services for one scene (D-142). */
export const searchImages = (jobId: string, index: number, query: string) =>
  request<SearchResponse>(`/api/jobs/${jobId}/scenes/${index}/search`, {
    method: "POST",
    body: JSON.stringify({ query }),
  });

export const searchPreviewUrl = (jobId: string, token: string) =>
  `/api/jobs/${jobId}/search/${encodeURIComponent(token)}/preview`;

/** Download one search result and use it for the scene. */
export const applySearchResult = (
  jobId: string,
  index: number,
  token: string,
  query: string,
) =>
  request<EditResult>(
    `/api/jobs/${jobId}/scenes/${index}/search/${encodeURIComponent(token)}`,
    { method: "POST", body: JSON.stringify({ query }) },
  );

/** Render the plan again, as edited. Same job, same id. */
export const getMix = (jobId: string) => request<MixState>(`/api/jobs/${jobId}/mix`);

export const saveMix = (jobId: string, mix: AudioMix) =>
  request<MixState>(`/api/jobs/${jobId}/mix`, { method: "PUT", body: JSON.stringify(mix) });

export const saveMusic = (jobId: string, music: MusicDraft) =>
  request<MixState>(`/api/jobs/${jobId}/music`, {
    method: "PUT",
    body: JSON.stringify({
      choice: music.choice,
      style: music.style,
      intensity: music.intensity,
      seed: music.seed,
      upload_id: music.choice === "own" ? music.upload_id ?? null : null,
      credit: music.choice === "own" ? music.credit ?? null : null,
      forget_track: music.forget_track ?? false,
    }),
  });

/**
 * A short sample of a music style (D-179), as a playable URL. The first one
 * for a style takes a few seconds to make; after that it is kept.
 */
export async function previewStyle(name: string): Promise<string> {
  const response = await fetch(`/api/score/styles/${encodeURIComponent(name)}/preview`, {
    credentials: "same-origin",
    headers: { Accept: "audio/wav" },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // A non-JSON error body is not worth a second failure.
    }
    throw new ApiError(response.status, detail);
  }
  return URL.createObjectURL(await response.blob());
}

/**
 * A few seconds of the mix at settings not yet applied, as a playable URL.
 * The caller revokes it when done with it.
 */
export async function previewMix(
  jobId: string,
  mix: AudioMix,
  start: number,
  voiceOnly: boolean,
  /** A track to hear in place of the video's music (D-184). */
  track: { upload_id?: string | null; kept?: boolean } = {},
): Promise<string> {
  const response = await fetch(`/api/jobs/${jobId}/mix/preview`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "audio/wav" },
    body: JSON.stringify({
      mix,
      start,
      seconds: 15,
      voice_only: voiceOnly,
      music_upload_id: track.upload_id ?? null,
      kept_track: track.kept ?? false,
    }),
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // A non-JSON error body is not worth a second failure.
    }
    throw new ApiError(response.status, detail);
  }
  return URL.createObjectURL(await response.blob());
}

/** Where the plan stands in its edit history (D-182). */
export interface PlanHistory {
  can_undo: boolean;
  can_redo: boolean;
  /** Changes not yet in the video: the distance from the version last rendered. */
  pending: number;
  undo_label: string;
  redo_label: string;
  /** Scenes that differ from what the video shows, for previews in the player. */
  changed_scenes: number[];
  /** Of those, the scenes whose picture changed: the player shows the new one. */
  changed_pictures: number[];
}

export const getPlanHistory = (jobId: string) =>
  request<PlanHistory>(`/api/jobs/${jobId}/plan/history`);

export const undoPlan = (jobId: string) =>
  request<PlanHistory>(`/api/jobs/${jobId}/plan/undo`, { method: "POST" });

export const redoPlan = (jobId: string) =>
  request<PlanHistory>(`/api/jobs/${jobId}/plan/redo`, { method: "POST" });

export const rerenderJob = (jobId: string) =>
  request<Job>(`/api/jobs/${jobId}/render`, { method: "POST" });

/** Start an interrupted, failed or stopped job again. */
export const resumeJob = (jobId: string) =>
  request<Job>(`/api/jobs/${jobId}/resume`, { method: "POST" });

/**
 * Correct what a scene's captions say. The original transcript is kept, and
 * word timings are re-derived from it at render time (D-062). Sending back
 * exactly what was heard removes the correction.
 */
export const correctCaption = (jobId: string, index: number, text: string) =>
  request<EditResult>(`/api/jobs/${jobId}/scenes/${index}/caption`, {
    method: "PUT",
    body: JSON.stringify({ text }),
  });

export interface CaptionTreatment {
  animation: "highlight" | "karaoke" | "pop" | "typewriter" | "emphasis" | "plain" | "spotlight" | "pulse";
  position: "bottom" | "center" | "top";
  accent: string; color: string; size: number;
  backing: "box" | "band" | "outline"; uppercase: boolean;
  words_per_page: number; max_lines: number; lift: number; emphasis_scale: number;
}
export interface CaptionControls {
  treatment: CaptionTreatment; emphasis: number[]; words: PlanWord[];
  presets: Record<string, CaptionTreatment>; inherited: boolean;
}
export const getCaptionControls = (jobId: string, index: number) =>
  request<CaptionControls>(`/api/jobs/${jobId}/scenes/${index}/captions`);
export const saveCaptionLook = (jobId: string, index: number, treatment: CaptionTreatment | null, emphasis: number[], all_scenes: boolean) =>
  request<PlanEditResult>(`/api/jobs/${jobId}/scenes/${index}/captions`, {
    method: "PUT", body: JSON.stringify({ treatment, emphasis, all_scenes }),
  });
export const suggestCaptionEmphasis = (jobId: string, index: number) =>
  request<{ emphasis: number[]; method: string }>(`/api/jobs/${jobId}/scenes/${index}/captions/suggest`, { method: "POST" });
export const previewCaptions = (jobId: string, index: number, treatment: CaptionTreatment, emphasis: number[]) =>
  request<{ url: string; note: string }>(`/api/jobs/${jobId}/scenes/${index}/captions/preview`, {
    method: "POST", body: JSON.stringify({ treatment, emphasis }),
  });

export interface TransitionTreatment {
  kind: "cut" | "crossfade" | "dip_to_black" | "slide" | "push" | "zoom" | "soft_blur";
  seconds: number; direction: "left" | "right" | "up" | "down";
}
export interface TransitionControls {
  treatment: TransitionTreatment; source: "scene" | "video" | "template";
  resolved: { kind: string; frames: number; reason: string };
  max_frames: number; presets: Record<string, TransitionTreatment>;
}
export const getTransitionControls = (jobId: string, index: number) =>
  request<TransitionControls>(`/api/jobs/${jobId}/scenes/${index}/transition`);
export const saveTransition = (jobId: string, index: number, treatment: TransitionTreatment | null, all_joins: boolean) =>
  request<PlanEditResult>(`/api/jobs/${jobId}/scenes/${index}/transition`, {
    method: "PUT", body: JSON.stringify({ treatment, all_joins }),
  });
export const previewTransition = (jobId: string, index: number, treatment: TransitionTreatment) =>
  request<{ url: string; note: string }>(`/api/jobs/${jobId}/scenes/${index}/transition/preview`, {
    method: "POST", body: JSON.stringify({ treatment }),
  });

export interface PacingControls {
  seconds: number;
  cuts: { id: string; scene: number; start_frame: number; end_frame: number;
    start: number; end: number; seconds: number; before: string; after: string }[];
}
export const getPacing = (jobId: string) => request<PacingControls>(`/api/jobs/${jobId}/pacing`);
export const savePacing = (jobId: string, cuts: string[]) =>
  request<PlanEditResult>(`/api/jobs/${jobId}/pacing`, { method: "PUT", body: JSON.stringify({ cuts }) });
