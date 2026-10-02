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

export type MusicChoice = "none" | "own" | "score";

/**
 * The video's music (D-176): none, the person's own track (D-148), or a score
 * Voxframe composes for the speech in a chosen style. Either kind is mixed
 * under the voice and lowered whenever anyone speaks.
 */
function MusicPicker({
  choice,
  onChoice,
  scoreStyle,
  onScoreStyle,
  score,
  music,
  credit,
  onMusic,
  onCredit,
  onBusy,
  downloadMb,
}: {
  choice: MusicChoice;
  onChoice: (choice: MusicChoice) => void;
  scoreStyle: string;
  onScoreStyle: (style: string) => void;
  score: Capabilities["score"];
  music: UploadResult | null;
  credit: string;
  onMusic: (music: UploadResult | null) => void;
  onCredit: (credit: string) => void;
  onBusy: (busy: boolean) => void;
  /** Megabytes still to fetch before music can be fitted to speech, if any. */
  downloadMb: number | null;
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
          Optional. Music plays under the voice and is lowered whenever someone speaks.
        </p>
      </header>

      <fieldset className="choices">
        <legend className="sr-only">Music</legend>
        <Choice
          name="music-choice"
          value="none"
          checked={choice === "none"}
          onChange={() => onChoice("none")}
          title="No music"
          description="Just the voice."
        />
        <Choice
          name="music-choice"
          value="own"
          checked={choice === "own"}
          onChange={() => onChoice("own")}
          title="Use my own track"
          description="Fitted to the speech: cut on the beat, landing on the last word."
        />
        {/* Offered only where its sounds are installed (D-187). */}
        {(score?.ready || choice === "score") && (
          <Choice
            name="music-choice"
            value="score"
            checked={choice === "score"}
            onChange={() => onChoice("score")}
            title="Let Voxframe score it"
            description="Music composed for this recording, following the speaker."
          />
        )}
      </fieldset>

      {choice === "score" && (
        <>
          <Field label="Music style" htmlFor="score-style">
            <select
              id="score-style"
              value={scoreStyle}
              onChange={(event) => onScoreStyle(event.target.value)}
            >
              {(score?.styles ?? []).map((option) => (
                <option key={option.name} value={option.name}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
          {score?.styles.find((option) => option.name === scoreStyle)?.description && (
            <p className="muted">
              {score.styles.find((option) => option.name === scoreStyle)?.description} Each
              video gets its own variation.
            </p>
          )}
          {score && !score.ready && <Notice tone="warn">{score.reason}</Notice>}
        </>
      )}

      {choice !== "own" ? null : music ? (
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
      {choice === "own" && music && downloadMb !== null && (
        <p className="muted">
          The first time you use your own music, Voxframe downloads the tools that fit it to the
          speech (about {downloadMb} MB, once). It happens while your video is being made.
        </p>
      )}
      {error && <Notice tone="error">{error}</Notice>}

      {choice === "own" && music && (
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
  const [musicChoice, setMusicChoice] = useState<MusicChoice>("none");
  const [scoreStyle, setScoreStyle] = useState("inspiring");
  // A video's own picture is what its maker expects to see (D-192).
  const [useVideo, setUseVideo] = useState(Boolean(upload.has_video));

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

      {upload.has_video && (
        <div className="card">
          <header>
            <h2>Picture</h2>
            <p>Your file has a picture as well as sound.</p>
          </header>
          <fieldset className="choices">
            <legend className="sr-only">Picture</legend>
            <Choice
              name="picture"
              value="video"
              checked={useVideo}
              onChange={() => setUseVideo(true)}
              title="Use my video"
              description="You on screen, in sync, with pictures cutting in when you mention what they show."
            />
            <Choice
              name="picture"
              value="pictures"
              checked={!useVideo}
              onChange={() => setUseVideo(false)}
              title="Pictures only"
              description="Your voice over matched pictures, as for a sound recording."
            />
          </fieldset>
          {useVideo && aspect !== "16:9" && (
            <p className="muted" style={{ marginTop: 10 }}>
              Your video is cropped to the new shape and follows you as you move, and
              captions keep clear of your face. You can switch any scene between you
              and its picture in the studio.
            </p>
          )}
        </div>
      )}

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
        choice={musicChoice}
        onChoice={setMusicChoice}
        scoreStyle={scoreStyle}
        onScoreStyle={setScoreStyle}
        score={capabilities?.score}
        music={music}
        credit={musicCredit}
        onMusic={setMusic}
        onCredit={setMusicCredit}
        onBusy={setMusicBusy}
        downloadMb={
          capabilities?.music_component && !capabilities.music_component.ready
            ? capabilities.music_component.download_mb
            : null
        }
      />

      {capabilities && capabilities.library.assets === 0 && sourcingEnabled && (
        <Notice tone="info">
          Your library is empty, so Voxframe will search online for images while
          it makes this video. That adds a little time.
        </Notice>
      )}

      {capabilities && capabilities.library.assets === 0 && !sourcingEnabled && !useVideo && (
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
          disabled={
            musicBusy ||
            (musicChoice === "own" && music === null) ||
            (musicChoice === "score" && !capabilities?.score?.ready)
          }
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
              use_video: useVideo,
              music_upload_id: musicChoice === "own" ? (music?.upload_id ?? null) : null,
              music_credit: musicChoice === "own" ? musicCredit : "",
              score_style: musicChoice === "score" ? scoreStyle : null,
            })
          }
        >
          Make the video
        </button>
      </div>
    </>
  );
}
