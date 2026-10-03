/**
 * The scene plan, as a filmstrip you can edit.
 *
 * The plan is the renderer's only input (D-011). This screen shows it in the
 * order a viewer sees it and answers the question a person actually has —
 * "why does my video look like that" — then offers what they can do about it.
 *
 * **Plain language first** (owner's step 4 decision 2). Each scene opens with a
 * sentence and the actions that apply to it: use a near miss anyway, choose
 * another, use your own photo, show a plain background. Similarity scores and
 * thresholds are real and worth keeping, but they are what an engineer asks,
 * so they live under a collapsed "Details".
 *
 * **An edit is saved at once and reaches the video on re-render.** Only the
 * scenes that changed are rendered again; the rest come from the cache
 * (D-101). A person's choice is never replaced by automatic matching (D-128).
 */

import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  addChapter,
  addTitle,
  applySearchResult,
  candidateThumbnailUrl,
  chooseImage,
  correctCaption,
  editCardText,
  getJob,
  getPlan,
  removeCard,
  removeImage,
  searchImages,
  searchPreviewUrl,
  setMotion,
  setShot,
  thumbnailUrl,
  uploadOwnImage,
  type EditResult,
  type PlanAsset,
  type PlanEditResult,
  type PlannedScene,
  type ScenePlan,
  type SearchResult,
} from "../api";
import { Choice, Notice } from "../components";

/** Frames to "1:04.5" — the form an editor expects. */
function timecode(frames: number, fps: number): string {
  const seconds = frames / fps;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return `${minutes}:${rest.toFixed(1).padStart(4, "0")}`;
}

/** A short reason a scene shows no imagery, in the user's terms. */
function explainEmpty(scene: PlannedScene): string {
  if (scene.asset_source === "user") return "You chose a plain background";
  const reason = scene.match_reason.toLowerCase();
  if (reason.includes("no image library")) return "No library was used";
  if (reason.includes("nothing in this scene can be searched")) return "Nothing here to search for";
  if (reason.includes("after penalties")) return "Only images of printed text matched";
  if (reason.includes("below") || reason.includes("threshold")) {
    return "Nothing matched closely enough";
  }
  if (reason.includes("repeat")) return "Skipped to avoid repeating an image";
  return scene.match_reason || "No match";
}

/**
 * The thumbnail URL for a scene, varied by which image it shows.
 *
 * The scene's thumbnail URL is the same before and after a swap, so without
 * this the browser would keep showing the old picture from its own cache.
 */
/** Whether a scene shows the speaker rather than a picture (D-192). */
export function sharesFrame(scene: PlannedScene): boolean {
  return !!scene.layout && scene.layout.kind !== "full" && scene.footage_start != null;
}

export function showsSpeaker(scene: PlannedScene): boolean {
  return scene.shot === "speaker" && scene.footage_start != null && !sharesFrame(scene);
}

export function sceneThumbnail(jobId: string, scene: PlannedScene): string {
  // Keyed on what is on screen, so switching a shot never shows a stale frame.
  const shown = sharesFrame(scene)
    ? `layout-${scene.asset?.id ?? ""}-${JSON.stringify(scene.layout)}`
    : showsSpeaker(scene)
      ? `you-${scene.footage_start}`
      : (scene.asset?.id ?? "");
  return `${thumbnailUrl(jobId, scene.index)}?v=${encodeURIComponent(shown)}`;
}

function SceneCard({
  scene,
  jobId,
  fps,
  selected,
  onSelect,
}: {
  scene: PlannedScene;
  jobId: string;
  fps: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const [thumbnailFailed, setThumbnailFailed] = useState(false);
  const duration = (scene.end_frame - scene.start_frame) / fps;
  const isCard = Boolean(scene.card_kind);
  const caption = scene.caption_text || scene.text;

  const speaker = showsSpeaker(scene);

  useEffect(() => setThumbnailFailed(false), [scene.asset?.id, speaker]);

  return (
    <li>
      <button
        type="button"
        className="scene"
        data-selected={selected}
        data-kind={isCard ? "card" : speaker ? "speaker" : scene.asset ? "asset" : "empty"}
        aria-pressed={selected}
        onClick={onSelect}
      >
        <span className="scene-frame">
          {isCard ? (
            <span className="scene-card-preview">
              <span className="scene-card-label">
                {scene.card_kind === "title" ? "Title" : "Chapter"}
              </span>
              <span className="scene-card-text">{scene.card_text}</span>
            </span>
          ) : (scene.asset || speaker) && !thumbnailFailed ? (
            <img
              src={sceneThumbnail(jobId, scene)}
              alt=""
              loading="lazy"
              onError={() => setThumbnailFailed(true)}
            />
          ) : (
            <span className="scene-empty">
              <span aria-hidden="true">—</span>
              <span className="scene-empty-why">{explainEmpty(scene)}</span>
              {scene.near_misses.length > 0 && (
                <span className="scene-empty-hint">
                  {scene.near_misses.length} close{" "}
                  {scene.near_misses.length === 1 ? "match" : "matches"}
                </span>
              )}
            </span>
          )}
          {speaker && <span className="scene-badge">you</span>}
          {!speaker && scene.asset?.kind === "video" && <span className="scene-badge">clip</span>}
          {scene.asset_source === "atmospheric" && (
            // Labelled on the card itself, so a filler is never mistaken for a
            // match even at a glance (D-137).
            <span className="scene-badge scene-badge-left scene-badge-atmospheric">
              atmospheric
            </span>
          )}
          {scene.asset_source === "user" && (
            <span className="scene-badge scene-badge-left">your choice</span>
          )}
        </span>

        <span className="scene-meta">
          <span className="scene-time">
            {timecode(scene.start_frame, fps)}
            <span className="scene-duration">{duration.toFixed(1)}s</span>
          </span>
          <span className="scene-text">
            {isCard ? scene.card_text : caption || <em>no speech</em>}
          </span>
        </span>
      </button>
    </li>
  );
}

/** One image the person could pick, with its thumbnail shown first. */
function Candidate({
  jobId,
  sceneIndex,
  asset,
  label,
  busy,
  usedIn,
  onChoose,
}: {
  jobId: string;
  sceneIndex: number;
  asset: PlanAsset;
  label: string;
  busy: boolean;
  /** Numbers of other scenes already showing this image. */
  usedIn: number[];
  onChoose: () => void;
}) {
  const noteId = `note-${sceneIndex}-${asset.id}`;
  const notes = [
    // Not hidden: the person may want it. But a repeated image reads as a
    // mistake to a viewer (D-087), so they should know before choosing.
    ...(usedIn.length > 0 ? [`Already in scene ${usedIn.join(", ")}`] : []),
    // Judged from the image itself (D-133): words in the picture compete with
    // the burned-in captions (D-082).
    ...(asset.prints_text ? ["Shows printed text, which can clash with the captions"] : []),
  ];
  return (
    <li
      className="candidate"
      data-used={usedIn.length > 0}
      data-text={asset.prints_text}
    >
      <img
        src={candidateThumbnailUrl(jobId, sceneIndex, asset.id)}
        alt={`Candidate image by ${asset.license_author}`}
        loading="lazy"
      />
      {notes.length > 0 && (
        <p className="candidate-note" id={noteId}>
          {notes.join(". ")}
        </p>
      )}
      <button
        type="button"
        className="btn"
        disabled={busy}
        aria-describedby={notes.length > 0 ? noteId : undefined}
        onClick={onChoose}
      >
        {label}
      </button>
    </li>
  );
}

/**
 * Search the image services for one scene (D-142).
 *
 * Starts from what the scene already searched for, so the first search is one
 * click. Nothing is downloaded in full until the person chooses; previews come
 * through the app, and each result shows its author, source and licence before
 * it is chosen, because that is what the credits will say.
 */
function OnlineSearch({
  jobId,
  scene,
  busy,
  onChoose,
}: {
  jobId: string;
  scene: PlannedScene;
  busy: boolean;
  onChoose: (token: string, query: string) => void;
}) {
  const [query, setQuery] = useState(scene.queries[0] ?? "");
  const [searched, setSearched] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [failed, setFailed] = useState<string[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSearching(true);
    setError(null);
    try {
      const response = await searchImages(jobId, scene.index, query);
      setResults(response.results);
      setFailed(response.failed_sources);
      setSearched(query);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The search could not be done.");
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="online-search">
      <form className="search-form" onSubmit={(event) => void submit(event)} role="search">
        <label htmlFor={`search-${scene.index}`}>Search online for</label>
        <div className="search-row">
          <input
            id={`search-${scene.index}`}
            type="search"
            value={query}
            maxLength={200}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="e.g. mountain summit at sunrise"
          />
          <button
            type="submit"
            className="btn btn-primary"
            disabled={searching || busy || !query.trim()}
          >
            {searching ? "Searching…" : "Search"}
          </button>
        </div>
      </form>

      {error && <Notice tone="error">{error}</Notice>}
      {failed.length > 0 && (
        <Notice tone="warn">
          {failed.join(" and ")} did not answer; these results are from the others.
        </Notice>
      )}
      {results && results.length === 0 && !error && (
        <p className="muted">Nothing found for “{searched}”. Try other words.</p>
      )}

      {results && results.length > 0 && (
        <ul className="candidates" aria-label="Search results">
          {results.map((result) => {
            const creditId = `credit-${result.token}`;
            return (
              <li className="candidate" key={result.token}>
                <img
                  src={searchPreviewUrl(jobId, result.token)}
                  alt={result.title || `Image by ${result.author}`}
                  loading="lazy"
                />
                <p className="candidate-note" id={creditId}>
                  {result.author} · {result.source} · {result.license}
                </p>
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  aria-describedby={creditId}
                  onClick={() => onChoose(result.token, searched)}
                >
                  Use this
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * Clean images first. A repeat or an image of printed text is offered, but not
 * recommended, so the one-click choice avoids both when anything else exists.
 * The sort is stable, so within each group the matcher's order is kept.
 */
function unusedFirst(assets: PlanAsset[], usedIn: (id: string) => number[]) {
  const concern = (asset: PlanAsset) =>
    Number(usedIn(asset.id).length > 0) + Number(asset.prints_text);
  return [...assets].sort((a, b) => concern(a) - concern(b));
}

/**
 * What a scene says, and a way to correct it.
 *
 * Misheard words are the most common thing a person will want to fix, so this
 * is a direct button rather than something buried in Details. The original is
 * always shown beside a correction, and restoring it is one click.
 */
function CaptionEditor({
  scene,
  busy,
  onSave,
}: {
  scene: PlannedScene;
  busy: boolean;
  onSave: (text: string) => void;
}) {
  const shown = scene.caption_text || scene.text;
  const corrected = Boolean(scene.caption_text && scene.caption_text !== scene.text);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(shown);
  const fieldId = `caption-${scene.index}`;

  useEffect(() => {
    setEditing(false);
    setDraft(scene.caption_text || scene.text);
  }, [scene.index, scene.caption_text, scene.text]);

  if (!editing) {
    return (
      <>
        <p className="scene-detail-text">{shown}</p>
        {corrected && (
          <p className="muted">
            Corrected. Originally heard as: “{scene.text}”{" "}
            <button
              type="button"
              className="link-button"
              disabled={busy}
              onClick={() => onSave(scene.text)}
            >
              Use what was heard
            </button>
          </p>
        )}
        <button
          type="button"
          className="btn btn-small"
          disabled={busy}
          onClick={() => setEditing(true)}
        >
          Edit caption
        </button>
      </>
    );
  }

  const unchanged = draft.trim().replace(/\s+/g, " ") === shown.trim().replace(/\s+/g, " ");

  return (
    <form
      className="caption-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (!unchanged && draft.trim()) onSave(draft);
        setEditing(false);
      }}
    >
      <label className="label" htmlFor={fieldId}>
        Caption for this scene
      </label>
      <textarea
        id={fieldId}
        rows={3}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        aria-describedby={`${fieldId}-hint`}
        autoFocus
      />
      <p className="hint" id={`${fieldId}-hint`}>
        Fix misheard words. The timing of each word is kept, even if one word
        becomes two.
      </p>
      <div className="caption-form-actions">
        <button
          type="submit"
          className="btn btn-primary btn-small"
          disabled={busy || unchanged || !draft.trim()}
        >
          Save caption
        </button>
        <button
          type="button"
          className="btn btn-quiet btn-small"
          onClick={() => {
            setDraft(shown);
            setEditing(false);
          }}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

/** Longest card text, matching the server (D-145). */
const MAX_CARD_CHARACTERS = 90;

/**
 * A card's text, editable in place, and a way to take the card out (D-145).
 * A card is drawn text: it has no image and no captions, only this.
 */
function CardEditor({
  scene,
  busy,
  onSave,
  onRemove,
}: {
  scene: PlannedScene;
  busy: boolean;
  onSave: (text: string) => void;
  onRemove: () => void;
}) {
  const [draft, setDraft] = useState(scene.card_text);
  const fieldId = `card-${scene.index}`;
  const kind = scene.card_kind === "title" ? "title" : "chapter";

  useEffect(() => setDraft(scene.card_text), [scene.index, scene.card_text]);

  const unchanged = draft.trim() === scene.card_text.trim();
  return (
    <form
      className="caption-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (!unchanged && draft.trim()) onSave(draft);
      }}
    >
      <label className="label" htmlFor={fieldId}>
        What the {kind} card says
      </label>
      <input
        id={fieldId}
        type="text"
        value={draft}
        maxLength={MAX_CARD_CHARACTERS}
        onChange={(event) => setDraft(event.target.value)}
      />
      <p className="hint">
        Shown for {kind === "title" ? "three" : "two"} seconds while the recording
        pauses. Long text is wrapped to fit the frame.
      </p>
      <div className="caption-form-actions">
        <button
          type="submit"
          className="btn btn-primary btn-small"
          disabled={busy || unchanged || !draft.trim()}
        >
          Save
        </button>
        <button type="button" className="btn btn-quiet btn-small" disabled={busy} onClick={onRemove}>
          Remove this card
        </button>
      </div>
    </form>
  );
}

/** Add a title card to a video that has none. Only ever what the person types (D-092). */
export function TitleAdder({
  busy,
  onAdd,
}: {
  busy: boolean;
  onAdd: (text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  if (!open) {
    return (
      <button type="button" className="btn btn-small" disabled={busy} onClick={() => setOpen(true)}>
        Add a title card
      </button>
    );
  }
  return (
    <form
      className="title-adder"
      onSubmit={(event) => {
        event.preventDefault();
        if (draft.trim()) onAdd(draft);
      }}
    >
      <label htmlFor="new-title" className="sr-only">
        Title
      </label>
      <input
        id="new-title"
        type="text"
        value={draft}
        maxLength={MAX_CARD_CHARACTERS}
        placeholder="The title, as it should appear"
        onChange={(event) => setDraft(event.target.value)}
        autoFocus
      />
      <button type="submit" className="btn btn-primary btn-small" disabled={busy || !draft.trim()}>
        Add
      </button>
      <button type="button" className="btn btn-quiet btn-small" onClick={() => setOpen(false)}>
        Cancel
      </button>
    </form>
  );
}

/** What the panel says first: one sentence, in the person's terms. */
function explanation(scene: PlannedScene): string {
  if (scene.asset) {
    if (scene.asset_source === "user") return "You chose this image.";
    if (scene.asset_source === "atmospheric") {
      return "Nothing matched this scene, so it shows a calm image on the recording's overall theme. It is atmosphere, not a match for what is said here.";
    }
    if (scene.match_reason.startsWith("visual metaphor")) {
      // Said plainly, so a metaphor is never taken for a literal match (D-136).
      return "Nothing said here can be photographed directly, so Voxframe chose an image that stands for its theme.";
    }
    return "Voxframe chose this as the closest match to what is said here.";
  }
  if (scene.asset_source === "user") return "You chose a plain background for this scene.";
  if (scene.near_misses.length > 0 && scene.match_reason.includes("repeat")) {
    // A forced repeat is offered, never taken (D-140).
    return "The best match is already shown elsewhere in this video, so this scene was left plain rather than repeat it. You can still use it, or another one below.";
  }
  if (scene.near_misses.length > 0) {
    return "Nothing matched closely enough to be used automatically, but these came close. If one fits, use it.";
  }
  if (scene.match_reason.includes("nothing in this scene can be searched")) {
    // A credit or filler with no subject: searching would only find noise (D-145).
    return "Nothing said here names something to show, so it shows a plain background. You can still choose an image for it.";
  }
  if (scene.match_reason.toLowerCase().includes("no image library")) {
    return "There were no images to choose from, so this scene shows a plain background.";
  }
  return "Nothing in your library resembled this scene, so it shows a plain background.";
}

/** Why a scene shows the speaker, in the words its plan recorded (D-192). */
function speakerExplanation(scene: PlannedScene): string {
  if (scene.shot_source === "user") return "You chose to be on screen in this scene.";
  const reason = scene.shot_reason ?? "";
  if (reason.includes("opens and closes")) {
    return "You are on screen: a video opens and closes on the person speaking.";
  }
  if (reason.includes("beside it cuts away")) {
    return "You are on screen: the scene next to this one cuts away to a picture.";
  }
  if (reason.includes("too long")) {
    return "You are on screen: a picture held this long would hide you rather than illustrate what you say.";
  }
  if (reason.includes("already cover")) {
    return "You are on screen: pictures already cover as much of the video as they should.";
  }
  return "You are on screen in this scene.";
}

export function SceneDetail({
  jobId,
  scene,
  fps,
  number,
  usedIn,
  sourcingEnabled,
  canStartChapter,
  onEdited,
  onPlanEdited,
  part = "all",
}: {
  jobId: string;
  scene: PlannedScene;
  fps: number;
  /** What to show: everything (the scene-plan page), or one tab's half in the studio. */
  part?: "all" | "picture" | "caption";
  /** Whether a chapter may start before this scene (D-145). */
  canStartChapter: boolean;
  /** A card edit: the whole plan changes, and which scene to show next. */
  onPlanEdited: (result: PlanEditResult, select: number) => void;
  /** Position among spoken scenes; cards are not counted. */
  number: number;
  /** Other scenes showing an image, by asset id. */
  usedIn: (assetId: string) => number[];
  /** Whether the person has turned online search on, with a key. */
  sourcingEnabled: boolean;
  onEdited: (result: EditResult) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const duration = (scene.end_frame - scene.start_frame) / fps;
  const caption = scene.caption_text || scene.text;

  useEffect(() => {
    setError(null);
    setSearchOpen(false);
  }, [scene.index]);

  const run = async (edit: () => Promise<EditResult>) => {
    setBusy(true);
    setError(null);
    try {
      onEdited(await edit());
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "That change could not be saved.");
    } finally {
      setBusy(false);
    }
  };

  const runPlan = async (edit: () => Promise<PlanEditResult>, select: number) => {
    setBusy(true);
    setError(null);
    try {
      onPlanEdited(await edit(), select);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "That change could not be saved.");
    } finally {
      setBusy(false);
    }
  };

  const heading = scene.card_kind
    ? `${scene.card_kind === "title" ? "Title" : "Chapter"} card`
    : `Scene ${number}`;

  return (
    <section className="card" aria-labelledby="scene-heading">
      <header>
        <h2 id="scene-heading">{heading}</h2>
        <p>
          {timecode(scene.start_frame, fps)} – {timecode(scene.end_frame, fps)} ·{" "}
          {duration.toFixed(1)}s
        </p>
      </header>

      {caption && part !== "picture" && (
        <>
          <h3>What is said</h3>
          <CaptionEditor
            scene={scene}
            busy={busy}
            onSave={(text) => void run(() => correctCaption(jobId, scene.index, text))}
          />
        </>
      )}

      {part === "caption" ? (
        error && <Notice tone="error">{error}</Notice>
      ) : scene.card_kind ? (
        <>
          {error && <Notice tone="error">{error}</Notice>}
          <CardEditor
            scene={scene}
            busy={busy}
            onSave={(text) =>
              void runPlan(() => editCardText(jobId, scene.index, text), scene.index)
            }
            onRemove={() =>
              void runPlan(() => removeCard(jobId, scene.index), Math.max(0, scene.index - 1))
            }
          />
        </>
      ) : (
        <>
          <h3 style={{ marginTop: 16 }}>What is on screen</h3>
          {scene.footage_start != null && (
            // The speaker or the picture (D-192): the first choice for a
            // video made with "Use my video", so it comes first.
            <fieldset className="choices shot-choice" disabled={busy}>
              <legend className="sr-only">Show you or the picture</legend>
              <Choice
                name={`shot-${scene.index}`}
                value="speaker"
                checked={showsSpeaker(scene)}
                onChange={() => void run(() => setShot(jobId, scene.index, "speaker"))}
                title="You"
                description="Your video, in sync with what you say."
              />
              <Choice
                name={`shot-${scene.index}`}
                value="picture"
                checked={!showsSpeaker(scene)}
                onChange={() => void run(() => setShot(jobId, scene.index, "picture"))}
                title={scene.asset ? "The picture" : "A plain background"}
                description={
                  scene.asset
                    ? "Cut away to the picture while you speak."
                    : "No picture matched; choose one below to cut away to it."
                }
              />
            </fieldset>
          )}
          {showsSpeaker(scene) ? (
            <>
              <img
                className="scene-current"
                src={sceneThumbnail(jobId, scene)}
                alt="You, in this scene"
              />
              <p className="scene-detail-text">{speakerExplanation(scene)}</p>
            </>
          ) : (
            <p className="scene-detail-text">{explanation(scene)}</p>
          )}

          {error && <Notice tone="error">{error}</Notice>}

          {showsSpeaker(scene) && (scene.asset || scene.alternatives.length > 0 || scene.near_misses.length > 0) && (
            <h3 style={{ marginTop: 16 }}>Pictures for this scene</h3>
          )}

          {scene.asset && !showsSpeaker(scene) && (
            <img
              className="scene-current"
              src={sceneThumbnail(jobId, scene)}
              alt={`The image shown in this scene, by ${scene.asset.license_author}`}
            />
          )}
          {scene.asset?.prints_text && (
            <p className="candidate-note">
              This image shows printed text, which can clash with the captions.
            </p>
          )}
          {scene.asset && scene.asset.kind !== "video" && !showsSpeaker(scene) && (
            // Per-scene on/off only; direction is a future idea (D-152, D-154).
            <label className="toggle" style={{ marginTop: 12 }}>
              <input
                type="checkbox"
                checked={scene.motion !== "none"}
                disabled={busy}
                onChange={(event) =>
                  void run(() => setMotion(jobId, scene.index, event.target.checked))
                }
              />
              <span className="text">
                <strong>Camera movement</strong>
                <span>A slow pan and zoom across the photo. Off holds it still.</span>
              </span>
            </label>
          )}

          {/* With no image yet, the close matches are the headline and are
              used "anyway". Once the scene has an image there is nothing to
              override, so they join the other choices as "use this instead". */}
          {!scene.asset && scene.near_misses.length > 0 && (
            <ul className="candidates" aria-label="Close matches">
              {unusedFirst(scene.near_misses, usedIn).map((asset) => (
                <Candidate
                  key={asset.id}
                  jobId={jobId}
                  sceneIndex={scene.index}
                  asset={asset}
                  label="Use this image anyway"
                  busy={busy}
                  usedIn={usedIn(asset.id)}
                  onChoose={() => void run(() => chooseImage(jobId, scene.index, asset.id))}
                />
              ))}
            </ul>
          )}

          {(() => {
            const others = scene.asset
              ? [...scene.alternatives, ...scene.near_misses]
              : scene.alternatives;
            if (others.length === 0) return null;
            return (
              <>
                <h3 style={{ marginTop: 16 }}>
                  {scene.asset ? "Choose another" : "Or use one of these"}
                </h3>
                <ul className="candidates" aria-label="Other images">
                  {unusedFirst(others, usedIn).map((asset) => (
                    <Candidate
                      key={asset.id}
                      jobId={jobId}
                      sceneIndex={scene.index}
                      asset={asset}
                      label={scene.asset ? "Use this instead" : "Use this"}
                      busy={busy}
                      usedIn={usedIn(asset.id)}
                      onChoose={() =>
                        void run(() => chooseImage(jobId, scene.index, asset.id))
                      }
                    />
                  ))}
                </ul>
              </>
            );
          })()}

          <div className="scene-actions">
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
            >
              Use your own photo or clip
            </button>
            {sourcingEnabled && (
              <button
                type="button"
                className="btn"
                disabled={busy}
                aria-expanded={searchOpen}
                onClick={() => setSearchOpen((open) => !open)}
              >
                {searchOpen ? "Close search" : "Search online"}
              </button>
            )}
            <input
              ref={fileRef}
              type="file"
              accept=".jpg,.jpeg,.png,.webp,.mp4,.webm,.mov,.m4v"
              className="sr-only"
              aria-label="Choose your own photo or video clip for this scene"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) void run(() => uploadOwnImage(jobId, scene.index, file));
              }}
            />
            {scene.asset && (
              <button
                type="button"
                className="btn btn-quiet"
                disabled={busy}
                onClick={() => void run(() => removeImage(jobId, scene.index))}
              >
                Show a plain background instead
              </button>
            )}
            {canStartChapter && (
              <button
                type="button"
                className="btn btn-quiet"
                disabled={busy}
                onClick={() =>
                  void runPlan(() => addChapter(jobId, scene.index), scene.index)
                }
              >
                Start a chapter here
              </button>
            )}
            {busy && <span className="muted">Saving…</span>}
          </div>

          {sourcingEnabled && searchOpen && (
            <OnlineSearch
              key={scene.index}
              jobId={jobId}
              scene={scene}
              busy={busy}
              onChoose={(token, query) =>
                void run(async () => {
                  const result = await applySearchResult(jobId, scene.index, token, query);
                  setSearchOpen(false);
                  return result;
                })
              }
            />
          )}
        </>
      )}

      {part !== "caption" && (
      <details className="scene-details">
        <summary>Details</summary>
        <dl className="detail-list">
          {!scene.card_kind && (
            <>
              <dt>Why</dt>
              <dd>{scene.match_reason || "No reason recorded."}</dd>
            </>
          )}
          {scene.asset && (
            <>
              <dt>Match score</dt>
              <dd>{scene.match_score.toFixed(3)}</dd>
              <dt>Similarity</dt>
              <dd>{scene.semantic_score.toFixed(3)}</dd>
              <dt>Image</dt>
              <dd>
                {scene.asset.width}×{scene.asset.height}{" "}
                {scene.asset.kind === "video" ? "clip" : "still"}
                {scene.asset.duration ? `, ${scene.asset.duration.toFixed(1)}s` : ""}
              </dd>
              <dt>Credit</dt>
              <dd>
                {scene.asset.license_author} · {scene.asset.license_name} ·{" "}
                {scene.asset.license_source}
              </dd>
            </>
          )}
          {scene.near_misses.length > 0 && (
            <>
              <dt>Close matches</dt>
              <dd>
                {scene.near_misses
                  .map((asset) => asset.similarity?.toFixed(3) ?? "?")
                  .join(", ")}{" "}
                similarity
              </dd>
            </>
          )}
          {scene.queries.length > 0 && (
            <>
              <dt>Searched for</dt>
              <dd>
                {scene.queries.join(" · ")}
                {scene.query_source === "user" ? " (edited by you)" : ""}
              </dd>
            </>
          )}
          <dt>Camera</dt>
          <dd>{scene.motion.replace(/_/g, " ")}</dd>
          <dt>Frames</dt>
          <dd>
            {scene.start_frame}–{scene.end_frame}
          </dd>
        </dl>
      </details>
      )}
    </section>
  );
}

export function Filmstrip({
  jobId,
  pendingEdits: initialPending,
  sourcingEnabled,
  onRerender,
}: {
  jobId: string;
  sourcingEnabled: boolean;
  pendingEdits: number;
  onRerender: () => Promise<void>;
}) {
  const [plan, setPlan] = useState<ScenePlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [pending, setPending] = useState(initialPending);
  const [addingTitle, setAddingTitle] = useState(false);
  const [titleError, setTitleError] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getPlan(jobId)
      .then((loaded) => {
        if (cancelled) return;
        setPlan(loaded);
        // Open on the first scene worth looking at: one that could use help.
        const first = loaded.scenes.find(
          (scene) => !scene.card_kind && !scene.asset && scene.near_misses.length > 0,
        );
        if (first) setSelected(first.index);
      })
      .then(() => getJob(jobId))
      .then((job) => {
        // Edits may have been made on an earlier visit to this screen, so the
        // count comes from the server rather than from whatever the page held.
        if (!cancelled && job) setPending(job.summary.pending_edits ?? 0);
      })
      .catch((caught) => {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Could not load the plan.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!plan) return <p className="muted">Loading the scene plan…</p>;

  const filled = plan.scenes.filter((scene) => scene.asset).length;
  const cards = plan.scenes.filter((scene) => scene.card_kind).length;
  const illustratable = plan.scenes.length - cards;
  const nearMissScenes = plan.scenes.filter(
    (scene) => !scene.asset && scene.near_misses.length > 0,
  ).length;
  const current = plan.scenes[Math.min(selected, plan.scenes.length - 1)];

  // Where each image appears, numbered as the person sees scenes (cards are
  // not counted). Matching could not bar an image used by a LATER scene -- it
  // had not been placed yet -- so the offer has to say so here (D-127).
  const sceneNumber = (index: number) =>
    plan.scenes.slice(0, index + 1).filter((scene) => !scene.card_kind).length;
  const placements = new Map<string, number[]>();
  for (const scene of plan.scenes) {
    if (!scene.asset) continue;
    const list = placements.get(scene.asset.id) ?? [];
    list.push(scene.index);
    placements.set(scene.asset.id, list);
  }
  const usedIn = (assetId: string) =>
    (placements.get(assetId) ?? [])
      .filter((index) => index !== current.index)
      .map(sceneNumber);

  const applyEdit = (result: EditResult) => {
    const scenes = plan.scenes.map((scene) =>
      scene.index === result.scene.index ? result.scene : scene,
    );
    setPlan({ ...plan, scenes });
    setPending(result.pending_edits);
  };

  // Adding or removing a card moves every later scene, so the whole plan is
  // replaced and the scene to show is chosen by position (D-145).
  const applyPlanEdit = (result: PlanEditResult, select: number) => {
    setPlan(result.plan);
    setPending(result.pending_edits);
    setSelected(Math.min(select, result.plan.scenes.length - 1));
  };

  const hasTitle = plan.scenes.some((scene) => scene.card_kind === "title");
  const firstSpoken = plan.scenes.findIndex((scene) => !scene.card_kind);
  const canStartChapter =
    !current.card_kind &&
    current.index > firstSpoken &&
    !plan.scenes[current.index - 1]?.card_kind;

  return (
    <>
      <header style={{ marginBottom: 18 }}>
        <h1>The scene plan</h1>
        <p className="muted" style={{ marginTop: 4 }}>
          Every scene in order. Choose a scene to see why it looks the way it does,
          and change it.
        </p>
      </header>

      {pending > 0 && (
        <div className="pending-bar" role="status">
          <span>
            <strong>
              {pending} {pending === 1 ? "change" : "changes"}
            </strong>{" "}
            not yet in the video. Only the scenes you changed are rendered again.
          </span>
          <button
            type="button"
            className="btn btn-primary"
            disabled={rendering}
            onClick={async () => {
              setRendering(true);
              try {
                await onRerender();
              } catch (caught) {
                setError(
                  caught instanceof Error ? caught.message : "Could not start the render.",
                );
                setRendering(false);
              }
            }}
          >
            {rendering ? "Starting…" : "Update the video"}
          </button>
        </div>
      )}

      <dl className="facts" style={{ marginBottom: 18 }}>
        <div>
          <dt>Scenes</dt>
          <dd>{plan.scenes.length}</dd>
        </div>
        <div>
          <dt>With imagery</dt>
          <dd>
            {filled}
            <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>
              {" "}
              / {illustratable}
            </span>
          </dd>
        </div>
        <div>
          <dt>Close matches</dt>
          <dd>{nearMissScenes}</dd>
        </div>
        <div>
          <dt>Length</dt>
          <dd>{timecode(plan.total_frames, plan.fps)}</dd>
        </div>
        <div>
          <dt>Style</dt>
          <dd style={{ fontSize: 14 }}>{plan.style}</dd>
        </div>
      </dl>

      {filled === 0 && illustratable > 0 && nearMissScenes === 0 && (
        <Notice tone="warn">
          <strong>No scene found an image.</strong> You can use your own photo on any
          scene below, and photos you add to your Library are used in future videos.
        </Notice>
      )}

      {nearMissScenes > 0 && (
        <Notice tone="info">
          {nearMissScenes} {nearMissScenes === 1 ? "scene has" : "scenes have"} close
          matches that were just below the bar for automatic use. Have a look — one
          may well fit.
        </Notice>
      )}

      {!hasTitle && (
        <div className="filmstrip-tools">
          <TitleAdder
            busy={addingTitle}
            onAdd={async (text) => {
              setAddingTitle(true);
              setTitleError(null);
              try {
                applyPlanEdit(await addTitle(jobId, text), 0);
              } catch (caught) {
                setTitleError(
                  caught instanceof ApiError ? caught.message : "The title could not be added.",
                );
              } finally {
                setAddingTitle(false);
              }
            }}
          />
          {titleError && <Notice tone="error">{titleError}</Notice>}
        </div>
      )}

      <ol className="filmstrip" aria-label={`${plan.scenes.length} scenes in order`}>
        {plan.scenes.map((scene) => (
          <SceneCard
            key={scene.index}
            scene={scene}
            jobId={jobId}
            fps={plan.fps}
            selected={scene.index === current.index}
            onSelect={() => setSelected(scene.index)}
          />
        ))}
      </ol>

      <SceneDetail
        jobId={jobId}
        scene={current}
        fps={plan.fps}
        number={sceneNumber(current.index)}
        usedIn={usedIn}
        sourcingEnabled={sourcingEnabled}
        canStartChapter={canStartChapter}
        onEdited={applyEdit}
        onPlanEdited={applyPlanEdit}
      />

      <p className="muted" style={{ marginTop: 16 }}>
        Transcribed with {plan.transcribe_model}
        {plan.language ? ` · ${plan.language}` : ""}
        {plan.embed_model ? ` · matched with ${plan.embed_model}` : ""} · {plan.fps} fps
      </p>
    </>
  );
}
