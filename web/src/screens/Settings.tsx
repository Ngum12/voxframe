/**
 * Screen 2 — settings for this render.
 *
 * Deliberately short. The CLI has many flags; most are not decisions to put to
 * someone who just wants a video. Style is shown as named cards, aspect as
 * drawn shapes, and quality states its time cost. Everything else lives behind
 * "Advanced".
 */

import { useState } from "react";
import { ApiError, uploadAudio } from "../api";
import type { Capabilities, RenderOptions, UploadResult } from "../api";
import {
  Choice,
  Field,
  Notice,
  estimateRenderSeconds,
  formatEstimate,
} from "../components";
import { UploadedFile } from "./Upload";

const ASPECTS = [
  { value: "16:9", label: "Landscape", note: "YouTube, presentations", w: 44, h: 25 },
  { value: "1:1", label: "Square", note: "Feeds", w: 32, h: 32 },
  { value: "9:16", label: "Vertical", note: "Shorts, Reels, TikTok", w: 25, h: 44 },
];

//: Long enough that a highlights video is a meaningfully different thing.
const HIGHLIGHTS_FLOOR_SECONDS = 300;

const MUSIC_ACCEPTED = ".mp3,.wav,.m4a,.aac,.flac,.ogg,.opus";

/**
 * An optional music bed (D-148). Voxframe never supplies music of its own
 * (D-091); this is the person's own track, mixed under the voice and lowered
 * while anyone speaks.
 */
function MusicPicker({
  music,
  credit,
  onMusic,
  onCredit,
  onBusy,
}: {
  music: UploadResult | null;
  credit: string;
  onMusic: (music: UploadResult | null) => void;
  onCredit: (credit: string) => void;
  onBusy: (busy: boolean) => void;
}) {
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const choose = async (file: File) => {
    setError(null);
    setProgress(0);
    onBusy(true);
    try {
      onMusic(await uploadAudio(file, setProgress));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The track could not be uploaded.");
    } finally {
      setProgress(null);
      onBusy(false);
    }
  };

  return (
    <div className="card">
      <header>
        <h2>Music</h2>
        <p>
          Optional. Your own track, played under the voice and lowered whenever
          someone speaks. Voxframe never adds music of its own.
        </p>
      </header>

      {music ? (
        <p>
          <strong>{music.name}</strong>
          {music.duration_seconds ? ` · ${Math.round(music.duration_seconds)}s` : ""}{" "}
          <button type="button" className="link-button" onClick={() => onMusic(null)}>
            Remove
          </button>
        </p>
      ) : (
        <Field label="Music track" htmlFor="music-file" hint="MP3, WAV, M4A, FLAC or OGG.">
          <input
            id="music-file"
            type="file"
            accept={MUSIC_ACCEPTED}
            aria-describedby="music-file-hint"
            disabled={progress !== null}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void choose(file);
            }}
          />
        </Field>
      )}
      {progress !== null && (
        <p className="muted">Uploading… {Math.round(progress * 100)}%</p>
      )}
      {error && <Notice tone="error">{error}</Notice>}

      {music && (
        <Field
          label="Credit"
          htmlFor="music-credit"
          hint="If the track needs attribution, write it as it should appear in the credits. Left empty, the credits say it was supplied by you."
        >
          <input
            id="music-credit"
            type="text"
            value={credit}
            maxLength={300}
            placeholder="e.g. “Evening” by Jane Doe, CC BY 4.0"
            aria-describedby="music-credit-hint"
            onChange={(event) => onCredit(event.target.value)}
          />
        </Field>
      )}
    </div>
  );
}

export function Settings({
  upload,
  capabilities,
  sourcingEnabled,
  onBack,
  onStart,
}: {
  upload: UploadResult;
  capabilities: Capabilities | null;
  sourcingEnabled: boolean;
  onBack: () => void;
  onStart: (options: Omit<RenderOptions, "upload_id">) => void;
}) {
  const [aspect, setAspect] = useState("16:9");
  const [style, setStyle] = useState("documentary");
  const [quality, setQuality] = useState("standard");
  const [height, setHeight] = useState(720);
  const [title, setTitle] = useState("");
  const [chapters, setChapters] = useState(true);
  const [highlights, setHighlights] = useState(false);
  const [highlightSeconds, setHighlightSeconds] = useState(150);
  const [advanced, setAdvanced] = useState(false);
  const [music, setMusic] = useState<UploadResult | null>(null);
  const [musicCredit, setMusicCredit] = useState("");
  const [musicBusy, setMusicBusy] = useState(false);

  const duration = upload.duration_seconds ?? 0;
  const longEnoughForHighlights = duration >= HIGHLIGHTS_FLOOR_SECONDS;
  const estimate = estimateRenderSeconds(
    highlights && longEnoughForHighlights ? highlightSeconds : duration,
    height,
    quality,
  );

  return (
    <>
      <header style={{ marginBottom: 20 }}>
        <h1>How should it look?</h1>
      </header>

      <UploadedFile result={upload} />

      <div className="card">
        <header>
          <h2>Style</h2>
          <p>Each one changes the pacing and the edit, not only the colours.</p>
        </header>

        <fieldset className="choices">
          <legend className="sr-only">Style template</legend>
          {(capabilities?.styles ?? []).map((option) => (
            <Choice
              key={option.name}
              name="style"
              value={option.name}
              checked={style === option.name}
              onChange={setStyle}
              title={option.name.replace(/-/g, " ")}
              description={option.description}
            />
          ))}
        </fieldset>

        <fieldset className="choices">
          <legend>Shape</legend>
          {ASPECTS.map((option) => (
            <Choice
              key={option.value}
              name="aspect"
              value={option.value}
              checked={aspect === option.value}
              onChange={setAspect}
            >
              <span className="shape">
                <span
                  className="box"
                  style={{ width: option.w, height: option.h }}
                  aria-hidden="true"
                />
                <strong>{option.label}</strong>
                <span>{option.note}</span>
              </span>
            </Choice>
          ))}
        </fieldset>

        <fieldset className="choices">
          <legend>Quality</legend>
          <Choice
            name="quality"
            value="draft"
            checked={quality === "draft"}
            onChange={setQuality}
            title="Draft"
            description="Fastest. Good for checking the edit."
          />
          <Choice
            name="quality"
            value="standard"
            checked={quality === "standard"}
            onChange={setQuality}
            title="Standard"
            description="The usual choice."
          />
          <Choice
            name="quality"
            value="high"
            checked={quality === "high"}
            onChange={setQuality}
            title="High"
            description="Slower, larger file."
          />
        </fieldset>
      </div>

      <div className="card">
        <header>
          <h2>Titles and length</h2>
        </header>

        <Field
          label="Title card"
          htmlFor="title"
          hint="Optional. Leave it empty for no title card — one is never made up from the filename."
        >
          <input
            id="title"
            type="text"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder=""
            aria-describedby="title-hint"
          />
        </Field>

        <label className="toggle">
          <input
            type="checkbox"
            checked={chapters}
            onChange={(event) => setChapters(event.target.checked)}
          />
          <span className="text">
            <strong>Chapter cards at long pauses</strong>
            <span>Only for recordings over three minutes.</span>
          </span>
        </label>

        {longEnoughForHighlights && (
          <>
            <label className="toggle">
              <input
                type="checkbox"
                checked={highlights}
                onChange={(event) => setHighlights(event.target.checked)}
              />
              <span className="text">
                <strong>Make a short highlights version</strong>
                <span>
                  Picks passages by speech density, imagery and position — it
                  does not understand the content.
                </span>
              </span>
            </label>

            {highlights && (
              <Field label="Target length (seconds)" htmlFor="highlight-seconds">
                <input
                  id="highlight-seconds"
                  type="number"
                  min={30}
                  max={Math.max(60, Math.floor(duration))}
                  step={10}
                  value={highlightSeconds}
                  onChange={(event) =>
                    setHighlightSeconds(Number(event.target.value) || 150)
                  }
                  style={{ maxWidth: 160 }}
                />
              </Field>
            )}
          </>
        )}
      </div>

      <MusicPicker
        music={music}
        credit={musicCredit}
        onMusic={setMusic}
        onCredit={setMusicCredit}
        onBusy={setMusicBusy}
      />

      {capabilities && capabilities.library.assets === 0 && sourcingEnabled && (
        <Notice tone="info">
          Your library is empty, so Voxframe will search online for images while
          it makes this video. That adds a little time.
        </Notice>
      )}

      {capabilities && capabilities.library.assets === 0 && !sourcingEnabled && (
        <Notice tone="warn">
          <strong>Scenes will show a plain background.</strong> There are no
          images in your library yet. Add your own photos in the Library, or turn
          on “Search online for images” in Settings. After the video is made you
          can also add your own photo to any scene.
        </Notice>
      )}

      <div className="card">
        <button
          type="button"
          className="btn btn-quiet"
          aria-expanded={advanced}
          aria-controls="advanced-panel"
          onClick={() => setAdvanced(!advanced)}
          style={{ padding: 0 }}
        >
          {advanced ? "▾" : "▸"} Advanced
        </button>

        {advanced && (
          <div id="advanced-panel" style={{ marginTop: 16 }}>
            <Field
              label="Height (pixels)"
              htmlFor="height"
              hint="Taller takes longer. 720 is a good default for most uses."
            >
              <select
                id="height"
                value={height}
                onChange={(event) => setHeight(Number(event.target.value))}
                style={{ maxWidth: 200 }}
              >
                <option value={480}>480</option>
                <option value={720}>720</option>
                <option value={1080}>1080</option>
              </select>
            </Field>
            <p className="muted">
              Transcription model and similarity threshold follow your
              configuration. The scene plan records exactly what was used.
            </p>
          </div>
        )}
      </div>

      <div className="actions">
        <button type="button" className="btn" onClick={onBack}>
          Back
        </button>
        <span className="muted spacer">
          Estimated render: {formatEstimate(estimate)}
        </span>
        <button
          type="button"
          className="btn btn-primary"
          disabled={musicBusy}
          onClick={() =>
            onStart({
              aspect,
              quality,
              height,
              style,
              title,
              chapters,
              highlights_seconds:
                highlights && longEnoughForHighlights ? highlightSeconds : null,
              use_library: true,
              music_upload_id: music?.upload_id ?? null,
              music_credit: musicCredit,
            })
          }
        >
          Make the video
        </button>
      </div>
    </>
  );
}
