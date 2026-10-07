import { useEffect, useRef, useState } from "react";
import { type ShortChoice, type ShortPreview } from "../api";
import { Notice } from "../components";

export interface StoryVariant<Choice = ShortChoice> { key: string; label: string; choice: Choice; preview: ShortPreview; warnings?: string[] }

export function StoryComparison<Choice = ShortChoice>({ variants, busy, onChoose, complete = false, canChoose }: {
  variants: StoryVariant<Choice>[]; busy: boolean; onChoose: (choice: Choice) => void; complete?: boolean; canChoose?: (choice: Choice) => boolean;
}) {
  const [aKey, setAKey] = useState(variants[0].key);
  const [bKey, setBKey] = useState(variants[1].key);
  const [audio, setAudio] = useState<0 | 1>(0);
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState<[boolean, boolean]>([false, false]);
  const [time, setTime] = useState(0), [duration, setDuration] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const players = useRef<(HTMLVideoElement | null)[]>([null, null]);
  const generation = useRef(0);
  const a = variants.find(v => v.key === aKey) ?? variants[0];
  const b = variants.find(v => v.key === bKey && v.key !== a.key) ?? variants.find(v => v.key !== a.key)!;
  const pair = [a, b];
  const stop = () => { generation.current++; players.current.forEach(player => player?.pause()); setPlaying(false); };
  useEffect(() => {
    generation.current++;
    const mounted = [...players.current];
    mounted.forEach(player => { if (player) { player.pause(); player.currentTime = 0; } });
    setPlaying(false); setTime(0); setError(null);
    setReady([!!mounted[0] && mounted[0].readyState >= 3, !!mounted[1] && mounted[1].readyState >= 3]);
    const durations = mounted.map(player => player?.duration ?? 0);
    setDuration(durations.every(d => Number.isFinite(d) && d > 0) ? Math.min(...durations) : 0);
    return () => { generation.current++; mounted.forEach(player => player?.pause()); };
  }, [a.preview.url, b.preview.url]);
  useEffect(() => {
    // Mute the old voice first; two audible previews must never overlap.
    players.current.forEach(player => { if (player) player.muted = true; });
    if (players.current[audio]) players.current[audio]!.muted = false;
  }, [audio, a.preview.url, b.preview.url]);
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const tick = () => {
      const master = players.current[audio], follower = players.current[1 - audio];
      if (!master || !follower || master.paused || follower.paused) { stop(); return; }
      if (Math.abs(master.currentTime - follower.currentTime) > .1) follower.currentTime = master.currentTime;
      setTime(master.currentTime);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, audio]);
  async function play() {
    if (playing) { stop(); return; }
    const current = ++generation.current;
    setError(null);
    const at = time >= duration - .05 ? 0 : time;
    players.current.forEach(player => { if (player) player.currentTime = at; });
    try {
      await Promise.all(players.current.map(player => player?.play()));
      if (current === generation.current) setPlaying(true);
    } catch {
      if (current === generation.current) { stop(); setError("Playback could not start. Press Play comparison to try again."); }
    }
  }
  function seek(seconds: number) {
    stop(); setTime(seconds);
    players.current.forEach(player => { if (player) player.currentTime = seconds; });
  }
  return <section className="story-comparison" aria-label={complete ? "Compare complete auditions" : "Compare story edits"}>
    <span className="shorts-eyebrow">THE SAME STORY, TWO WAYS</span>
    <h3>{complete ? "Watch and hear the difference." : "Watch the difference."}</h3>
    <p>{complete ? "Compare complete treatments of your current story. One timeline, one soundtrack at a time." : "Compare rendered edits of this passage. One timeline, one voice at a time."}</p>
    <div className="comparison-pair">{pair.map((variant, index) => <article key={index}>
      <label className="label">Version {index === 0 ? "A" : "B"}<select aria-label={`Comparison version ${index === 0 ? "A" : "B"}`}
        value={variant.key} disabled={busy || playing} onChange={event => index === 0 ? setAKey(event.target.value) : setBKey(event.target.value)}>
        {variants.map(option => <option key={option.key} value={option.key} disabled={index === 1 && option.key === a.key}>{option.label}</option>)}
      </select></label>
      <p className="comparison-title">{variant.label}</p>
      <video key={variant.preview.url} ref={player => { players.current[index] = player; }} playsInline preload="auto"
        muted aria-label={`Comparison preview ${index === 0 ? "A" : "B"}`} src={variant.preview.url}
        onCanPlay={event => {
          if (players.current[index] !== event.currentTarget) return;
          setReady(current => { const next: [boolean, boolean] = [...current]; next[index] = true; return next; });
          const durations = players.current.map(player => player?.duration ?? 0);
          if (durations.every(d => Number.isFinite(d) && d > 0)) setDuration(Math.min(...durations));
        }}
        onWaiting={() => { if (playing) stop(); }} onEnded={() => { setTime(duration); stop(); }}
        onError={() => { stop(); setError("A comparison preview could not load. Render that version again."); }} />
      {complete && !!variant.warnings?.length && <details><summary>Music and sound notes · {variant.warnings.length}</summary>
        {variant.warnings.map((warning, i) => <p className="hint" key={i}>{warning}</p>)}
      </details>}
      <p className="hint">{variant.preview.seconds.toFixed(2)} s · {audio === index ? "Sound on" : "Silent comparison"}</p>
      <button className="btn btn-primary" type="button" disabled={busy || (canChoose ? !canChoose(variant.choice) : false)} onClick={() => { stop(); onChoose(variant.choice); }}>Use version {index === 0 ? "A" : "B"}</button>
    </article>)}</div>
    <div className="comparison-transport">
      <button className="btn" type="button" disabled={!ready.every(Boolean) || !duration} onClick={() => void play()}>{playing ? "Pause comparison" : "Play comparison"}</button>
      <label className="label">Hear<select aria-label="Comparison sound" value={audio} onChange={event => setAudio(Number(event.target.value) as 0 | 1)}>
        <option value={0}>Version A</option><option value={1}>Version B</option></select></label>
      <label className="label">Shared playhead · {time.toFixed(1)} / {duration.toFixed(1)} s<input aria-label="Comparison playhead" type="range" min={0} max={duration || 1} step={.05} value={Math.min(time, duration || 1)} disabled={!ready.every(Boolean)} onChange={event => seek(Number(event.target.value))} /></label>
    </div>
    {error && <Notice tone="error">{error}</Notice>}
    <p className="hint">{complete ? "These drafts include their selected soundtrack. Choosing a version saves the exact previewed direction, captions, track and mix together; Undo restores your previous edit." : "Drafts share the same word range and shape. Added music is heard after Update video. Choosing a version saves its passage, direction and caption choice together; Undo restores your previous edit."}</p>
  </section>;
}
