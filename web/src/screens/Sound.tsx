/**
 * The Sound card on the result screen (D-171).
 *
 * Voice level, music level, how far the music stays under the voice, and the
 * loudness for where the video is going. Moving a slider plays 15 seconds of
 * the mix at the new setting, made from the video's kept sound stems, without
 * rendering anything. "Apply" saves the settings to the scene plan and updates
 * the video, which re-renders only the sound: the pictures come from the cache.
 *
 * Voice polish (D-173) switches between the polished voice and the recording
 * exactly as it was; both are kept, so the preview compares them instantly.
 *
 * The music itself can change after the video is made (D-179): no music, a
 * generated score in any style, or the person's own track again. For a score,
 * "New variation" composes a new piece in the same style, intensity makes it
 * calmer or more driving, and the instrument groups have their own levels.
 * Group levels re-mix the kept score at once; a new style, variation or
 * intensity is composed when it is applied, still re-rendering only the sound.
 *
 * A track of the person's own can be added at any time (D-184): uploaded
 * here, heard in the 15-second preview under the voice before it is chosen,
 * credited as they state it, switched with the score and no music, and
 * removed. The preview always plays the music chosen in the card.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  type AudioMix,
  type MixState,
  type MusicDraft,
  SCORE_GROUPS,
  type ScoreGroup,
  type SoundCheck,
  getMix,
  previewMix,
  previewStyle,
  saveMix,
  saveMusic,
  uploadAudio,
} from "../api";
import { Notice } from "../components";
import { MusicLibraryPanel } from "./MusicLibrary";

const GROUP_LABELS: Record<ScoreGroup, string> = {
  piano: "Piano",
  strings: "Strings",
  percussion: "Percussion",
  bass: "Bass",
  pads: "Pads",
};

function sameMix(a: AudioMix, b: AudioMix): boolean {
  return (
    a.voice_db === b.voice_db &&
    a.music_db === b.music_db &&
    a.speech_margin_db === b.speech_margin_db &&
    a.destination === b.destination &&
    a.voice_polish === b.voice_polish &&
    SCORE_GROUPS.every((group) => a.score_levels[group] === b.score_levels[group])
  );
}

function sameMusic(a: MusicDraft, b: MusicDraft): boolean {
  if (a.choice !== b.choice || Boolean(a.forget_track) !== Boolean(b.forget_track)) return false;
  if (a.choice === "own") {
    return (a.upload_id ?? null) === (b.upload_id ?? null) &&
      (a.library_id ?? null) === (b.library_id ?? null) && (a.credit ?? "") === (b.credit ?? "");
  }
  if (a.choice !== "score") return true;
  return a.style === b.style && a.intensity === b.intensity && a.seed === b.seed;
}

function draftOf(state: MixState): MusicDraft {
  const { choice, style, intensity, seed } = state.music;
  return { choice, style, intensity, seed, credit: state.music.track_credit ?? "" };
}

/**
 * What the preview plays as the music: the card's choice, applied or not
 * (D-184). The track the video already has plays as it was fitted to the voice.
 */
function previewTrack(
  music: MusicDraft,
  saved: MusicDraft,
): { upload_id?: string | null; library_id?: string | null; kept?: boolean } {
  if (music.choice !== "own") return {};
  if (music.library_id) return { library_id: music.library_id };
  if (music.upload_id) return { upload_id: music.upload_id };
  return saved.choice === "own" ? {} : { kept: true };
}

/** A new variation: any seed the server would accept. */
function newSeed(): number {
  const value = new Uint32Array(1);
  crypto.getRandomValues(value);
  return value[0] % 2 ** 31;
}

function signed(db: number): string {
  return `${db > 0 ? "+" : ""}${db} dB`;
}

function intensityWords(value: number): string {
  if (value <= -0.75) return "much calmer than the speech";
  if (value < 0) return "a little calmer";
  if (value === 0) return "follows the speech";
  if (value < 0.75) return "a little more driving";
  return "much more driving";
}

function CheckSummary({ check }: { check: SoundCheck }) {
  const facts = [
    `${check.integrated_lufs.toFixed(1)} LUFS (aiming for ${check.target_lufs})`,
    `loudest peak ${check.true_peak.toFixed(1)} dBTP`,
  ];
  if (check.min_speech_margin_db !== null) {
    facts.push(`music at least ${check.min_speech_margin_db.toFixed(1)} dB under the voice`);
  }
  return (
    <div className="sound-check">
      <p>
        <strong>{check.passed ? "Sound checks passed" : "Sound checks found a problem"}</strong>
        {" · "}
        <span className="muted">{facts.join(" · ")}</span>
      </p>
      {check.problems.length > 0 && (
        <ul className="list-plain">
          {check.problems.map((problem, index) => (
            <li key={index}>{problem}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface Draft {
  mix: AudioMix;
  music: MusicDraft;
}

export function SoundPanel({
  jobId,
  playhead,
  onApply,
}: {
  jobId: string;
  /** Where the video is paused, so the preview plays the part being watched. */
  playhead: () => number;
  onApply: () => Promise<void>;
}) {
  const [state, setState] = useState<MixState | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [past, setPast] = useState<Draft[]>([]);
  const [future, setFuture] = useState<Draft[]>([]);
  const [voiceOnly, setVoiceOnly] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [styleUrl, setStyleUrl] = useState<string | null>(null);
  const [styleBusy, setStyleBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const trackInput = useRef<HTMLInputElement>(null);
  const player = useRef<HTMLAudioElement>(null);
  const stylePlayer = useRef<HTMLAudioElement>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    getMix(jobId)
      .then((loaded) => {
        if (cancelled) return;
        setState(loaded);
        setDraft({ mix: loaded.audio_mix, music: draftOf(loaded) });
      })
      .catch((reason: Error) => !cancelled && setError(reason.message));
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  useEffect(() => () => {
    if (styleUrl) URL.revokeObjectURL(styleUrl);
  }, [styleUrl]);

  const listen = useCallback(
    async (settings: AudioMix, onlyVoice: boolean, music: MusicDraft) => {
      if (!state?.can_preview) return;
      try {
        setError(null);
        const url = await previewMix(
          jobId,
          settings,
          Math.max(0, playhead() - 2),
          onlyVoice || music.choice === "none",
          previewTrack(music, draftOf(state)),
        );
        setPreviewUrl(url);
        window.setTimeout(() => void player.current?.play().catch(() => undefined), 0);
      } catch (reason) {
        setError((reason as Error).message);
      }
    },
    [jobId, playhead, state],
  );

  const hearStyle = async (name: string) => {
    setStyleBusy(true);
    setError(null);
    try {
      setStyleUrl(await previewStyle(name));
      window.setTimeout(() => void stylePlayer.current?.play().catch(() => undefined), 0);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setStyleBusy(false);
    }
  };

  const change = (next: Draft, { preview = true }: { preview?: boolean } = {}) => {
    if (!draft) return;
    setPast((history) => [...history, draft]);
    setFuture([]);
    setDraft(next);
    if (!preview) return;
    // One preview when the slider settles, not one per step while it moves.
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void listen(next.mix, voiceOnly, next.music), 350);
  };
  const changeMix = (mix: AudioMix) => draft && change({ ...draft, mix });
  const changeMusic = (music: MusicDraft, preview = false) =>
    draft && change({ ...draft, music }, { preview });

  const addTrack = async (file: File) => {
    if (!draft) return;
    setAdding(true);
    setError(null);
    try {
      const uploaded = await uploadAudio(file);
      changeMusic(
        {
          ...draft.music,
          choice: "own",
          library_id: null,
          upload_id: uploaded.upload_id,
          upload_name: uploaded.name,
          credit: "",
          forget_track: false,
        },
        true,
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setAdding(false);
      if (trackInput.current) trackInput.current.value = "";
    }
  };

  const undo = () => {
    if (!draft || past.length === 0) return;
    const previous = past[past.length - 1];
    setPast(past.slice(0, -1));
    setFuture([draft, ...future]);
    setDraft(previous);
  };

  const redo = () => {
    if (!draft || future.length === 0) return;
    const next = future[0];
    setFuture(future.slice(1));
    setPast([...past, draft]);
    setDraft(next);
  };

  if (error && !state) return <Notice tone="error">{error}</Notice>;
  if (!state || !draft) return null;

  const { mix, music } = draft;
  const savedMusic = draftOf(state);
  const musicChanged = !sameMusic(music, savedMusic);
  const mixChanged = !sameMix(mix, state.audio_mix);
  const changed = musicChanged || mixChanged;
  const hasMusic = music.choice !== "none";
  const tooClose = mix.speech_margin_db < state.comfortable_margin_db;
  const destination = state.destinations.find((entry) => entry.id === mix.destination);
  const styles = state.music.styles;
  const scoreStyle = music.style ?? styles[0]?.name ?? null;
  const trackName = music.upload_name ?? (music.forget_track ? null : state.music.track_name);
  // Group levels can be heard at once only on the score the video already has.
  const groupsAudible = savedMusic.choice === "score" && !musicChanged && state.can_preview;

  return (
    <div className="card sound-panel">
      <header>
        <h2>Sound</h2>
        <p>
          Balance the voice and the music, and set how loud the video is for where it is going.
          Moving a slider plays 15 seconds from where the video is paused. Applying it updates
          only the sound; the pictures are kept.
        </p>
      </header>

      <div className="sound-polish" role="group" aria-labelledby="voice-polish-label">
        <span className="label" id="voice-polish-label">Voice</span>
        <div className="filters">
          <button
            type="button"
            className="chip"
            aria-pressed={mix.voice_polish}
            onClick={() => !mix.voice_polish && changeMix({ ...mix, voice_polish: true })}
          >
            Polished
          </button>
          <button
            type="button"
            className="chip"
            aria-pressed={!mix.voice_polish}
            onClick={() => mix.voice_polish && changeMix({ ...mix, voice_polish: false })}
          >
            Original
          </button>
        </div>
        <span className="hint">
          {mix.voice_polish
            ? "Gentle noise reduction, clearer tone and an even level. " +
              (state.polish?.reduction_note ?? "")
            : "Exactly as recorded, nothing changed."}
        </span>
      </div>

      <div className="sound-music" role="group" aria-labelledby="music-choice-label">
        <span className="label" id="music-choice-label">Music</span>
        <div className="filters">
          <button
            type="button"
            className="chip"
            aria-pressed={music.choice === "none"}
            onClick={() => changeMusic({ ...music, choice: "none" }, true)}
          >
            No music
          </button>
          {/* Offered only where its sounds are installed (D-187). */}
          {(state.music.score_ready || music.choice === "score") && (
            <button
              type="button"
              className="chip"
              aria-pressed={music.choice === "score"}
              disabled={!state.music.score_ready && music.choice !== "score"}
              onClick={() =>
                changeMusic({
                  ...music,
                  choice: "score",
                  style: scoreStyle,
                  seed: music.seed ?? newSeed(),
                })
              }
            >
              Let Voxframe score it
            </button>
          )}
          {trackName && (
            <button
              type="button"
              className="chip"
              aria-pressed={music.choice === "own"}
              onClick={() => changeMusic({ ...music, choice: "own" }, true)}
            >
              Your track: {trackName}
            </button>
          )}
          <button
            type="button"
            className="chip"
            disabled={adding}
            onClick={() => trackInput.current?.click()}
          >
            {adding ? "Adding your track…" : trackName ? "Use a different track…" : "Add your own track…"}
          </button>
          <input
            ref={trackInput}
            type="file"
            accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg,.opus"
            hidden
            aria-label="Choose a music track"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void addTrack(file);
            }}
          />
        </div>

        <details className="music-library-picker">
          <summary>Choose from your music library</summary>
          <MusicLibraryPanel onChoose={(track) => changeMusic({
            ...music, choice: "own", library_id: track.id, upload_id: null,
            upload_name: track.title, credit: track.credit || `${track.title} (supplied by the user)`,
            forget_track: false,
          }, true)} />
        </details>

        {music.choice === "own" && trackName && (
          <div className="own-track">
            <label className="field" htmlFor="track-credit">
              <span className="label">Credit</span>
              <input
                id="track-credit"
                type="text"
                maxLength={300}
                value={music.credit ?? ""}
                placeholder="For example: “Morning” by A. Composer, CC BY 4.0"
                aria-describedby="track-credit-hint"
                onChange={(event) => changeMusic({ ...music, credit: event.target.value })}
              />
              <span className="hint" id="track-credit-hint">
                Written into the video’s credits exactly as you type it. Left empty, the track is
                credited by its file name.
              </span>
            </label>
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() =>
                changeMusic(
                  { ...music, choice: "none", library_id: null, upload_id: null, upload_name: null, forget_track: true },
                  true,
                )
              }
            >
              Remove this track
            </button>
          </div>
        )}

        {music.choice === "score" && (
          <div className="score-controls">
            <div className="score-row">
              <label className="field" htmlFor="score-style-edit">
                <span className="label">Style</span>
                <select
                  id="score-style-edit"
                  value={scoreStyle ?? ""}
                  onChange={(event) => changeMusic({ ...music, style: event.target.value })}
                >
                  {styles.map((option) => (
                    <option key={option.name} value={option.name}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="btn btn-quiet"
                disabled={styleBusy || !scoreStyle}
                onClick={() => scoreStyle && void hearStyle(scoreStyle)}
              >
                {styleBusy ? "Preparing…" : "Hear this style"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => changeMusic({ ...music, style: scoreStyle, seed: newSeed() })}
              >
                New variation
              </button>
            </div>
            <p className="hint">
              {styles.find((option) => option.name === scoreStyle)?.description}
            </p>
            {styleUrl && (
              <audio ref={stylePlayer} controls src={styleUrl} aria-label="Style preview" />
            )}

            <label className="field" htmlFor="score-intensity">
              <span className="label">
                Intensity <span className="muted">{intensityWords(music.intensity)}</span>
              </span>
              <input
                id="score-intensity"
                type="range"
                min={-1}
                max={1}
                step={0.25}
                value={music.intensity}
                onChange={(event) =>
                  changeMusic({ ...music, intensity: Number(event.target.value) })
                }
              />
            </label>

            <div className="sound-grid score-groups">
              {SCORE_GROUPS.map((group) => (
                <label className="field" htmlFor={`group-${group}`} key={group}>
                  <span className="label">
                    {GROUP_LABELS[group]}{" "}
                    <span className="muted">
                      {mix.score_levels[group] <= -30 ? "off" : signed(mix.score_levels[group])}
                    </span>
                  </span>
                  <input
                    id={`group-${group}`}
                    type="range"
                    min={-30}
                    max={6}
                    step={1}
                    value={mix.score_levels[group]}
                    onChange={(event) =>
                      change(
                        {
                          music,
                          mix: {
                            ...mix,
                            score_levels: {
                              ...mix.score_levels,
                              [group]: Number(event.target.value),
                            },
                          },
                        },
                        { preview: groupsAudible },
                      )
                    }
                  />
                </label>
              ))}
            </div>
          </div>
        )}

        {musicChanged && (
          <p className="muted">
            {music.choice === "score"
              ? "The new music is composed when you apply it; only the sound is made again."
              : "The change is made when you apply it; only the sound is made again."}
          </p>
        )}
      </div>

      {state.music_in_recording && (
        <Notice tone={hasMusic ? "warn" : "info"}>
          {hasMusic
            ? "Your recording already has music in it, and this video adds more on top: two " +
              "layers of music may clash. “No music” may sound better. It is your call."
            : "Your recording already has music in it, so “No music” is likely the " +
              "best choice for this video."}
        </Notice>
      )}

      <div className="sound-grid">
        <label className="field" htmlFor="voice-level">
          <span className="label">
            Voice <span className="muted">{signed(mix.voice_db)}</span>
          </span>
          <input
            id="voice-level"
            type="range"
            min={-12}
            max={12}
            step={0.5}
            value={mix.voice_db}
            onChange={(event) => changeMix({ ...mix, voice_db: Number(event.target.value) })}
          />
        </label>

        <label className="field" htmlFor="music-level">
          <span className="label">
            Music{" "}
            <span className="muted">{hasMusic ? signed(mix.music_db) : "no music in this video"}</span>
          </span>
          <input
            id="music-level"
            type="range"
            min={-30}
            max={12}
            step={0.5}
            value={mix.music_db}
            disabled={!hasMusic}
            onChange={(event) => changeMix({ ...mix, music_db: Number(event.target.value) })}
          />
        </label>

        <label className="field" htmlFor="music-under-voice">
          <span className="label">
            Music under the voice by <span className="muted">{mix.speech_margin_db} dB</span>
          </span>
          <input
            id="music-under-voice"
            type="range"
            min={3}
            max={30}
            step={1}
            value={mix.speech_margin_db}
            disabled={!hasMusic}
            aria-describedby="music-under-voice-hint"
            onChange={(event) =>
              changeMix({ ...mix, speech_margin_db: Number(event.target.value) })
            }
          />
          <span className="hint" id="music-under-voice-hint">
            How far the music drops while someone speaks. More is gentler on the voice.
          </span>
        </label>

        <label className="field" htmlFor="destination">
          <span className="label">Made for</span>
          <select
            id="destination"
            value={mix.destination}
            onChange={(event) => changeMix({ ...mix, destination: event.target.value })}
          >
            {state.destinations.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label} ({entry.lufs} LUFS)
              </option>
            ))}
          </select>
        </label>
      </div>

      {tooClose && hasMusic && (
        <Notice tone="warn">
          The music will sit only {mix.speech_margin_db} dB under the voice. Below{" "}
          {state.comfortable_margin_db} dB it starts to compete with the words. It is your call;
          nothing is changed for you.
        </Notice>
      )}

      <div className="sound-preview">
        {state.can_preview ? (
          <>
            <button type="button" className="btn" onClick={() => void listen(mix, voiceOnly, music)}>
              Play 15 seconds
            </button>
            <label className="toggle">
              <input
                type="checkbox"
                checked={voiceOnly}
                onChange={(event) => {
                  setVoiceOnly(event.target.checked);
                  void listen(mix, event.target.checked, music);
                }}
              />{" "}
              Voice only
            </label>
            {previewUrl && (
              <audio ref={player} controls src={previewUrl} aria-label="Sound preview" />
            )}
          </>
        ) : (
          <p className="muted">Update this video once to hear previews of its sound.</p>
        )}
      </div>

      {error && <Notice tone="error">{error}</Notice>}

      {state.last_check && <CheckSummary check={state.last_check} />}

      <div className="actions">
        <button type="button" className="btn btn-quiet" onClick={undo} disabled={past.length === 0}>
          Undo
        </button>
        <button type="button" className="btn btn-quiet" onClick={redo} disabled={future.length === 0}>
          Redo
        </button>
        <span className="spacer" />
        {destination && (
          <span className="muted" style={{ fontSize: 13 }}>
            {destination.label}: {destination.lufs} LUFS, peaks under {destination.true_peak} dBTP
          </span>
        )}
        <button
          type="button"
          className="btn btn-primary"
          disabled={!changed || busy}
          onClick={async () => {
            setBusy(true);
            try {
              if (musicChanged) await saveMusic(jobId, music);
              if (mixChanged) await saveMix(jobId, mix);
              await onApply();
            } catch (reason) {
              setError((reason as Error).message);
              setBusy(false);
            }
          }}
        >
          {busy ? "Updating…" : "Apply to the video"}
        </button>
      </div>
    </div>
  );
}
