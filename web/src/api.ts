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
}

export interface SourcingSettings {
  consent: boolean | null;
  has_been_asked: boolean;
  enabled: boolean;
  consent_version?: number;
  current_version?: number;
}

export interface UserSettings {
  sourcing: SourcingSettings;
  api_keys: Record<string, string>;
  adapters: string[];
  environment_keys: string[];
}

export interface UploadResult {
  upload_id: string;
  name: string;
  bytes: number;
  duration_seconds: number | null;
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
  /** A music bed, uploaded like the recording (D-148). */
  music_upload_id?: string | null;
  /** Attribution for the track, for the credits. */
  music_credit?: string;
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
  language: string;
  language_probability: number;
  transcribe_model: string;
  embed_model: string;
  music_path: string;
  music_credit: string;
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
  state: "idle" | "downloading" | "done" | "failed";
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
