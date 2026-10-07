import { useEffect, useRef, useState } from "react";
import { exportDestinationVariants, getDestinationVariants, previewDestinationVariant, type BatchJob, type ShortExport, type VariantControls, type VariantPreview } from "../api";
import { Notice } from "../components";

type Platform = ShortExport["platform"];
export function DestinationVariants({jobId, jobs}: {jobId: string; jobs: BatchJob[]}) {
  const [open, setOpen] = useState(false), [controls, setControls] = useState<VariantControls | null>(null);
  const [sources, setSources] = useState<string[]>([]), [platforms, setPlatforms] = useState<Platform[]>(["youtube"]);
  const [settings, setSettings] = useState<Partial<Record<Platform, ShortExport>>>({});
  const [previews, setPreviews] = useState<Record<string, {video: VariantPreview; reviewed: boolean}>>({});
  const [busy, setBusy] = useState(false), [status, setStatus] = useState(""), [error, setError] = useState<string | null>(null);
  const [guides, setGuides] = useState(true), [refresh, setRefresh] = useState(0);
  const generation = useRef(0), media = useRef<HTMLDivElement>(null);
  const stamp = jobs.filter(item => !item.variant_source).map(item => `${item.job.id}:${item.job.state}:${item.job.summary?.pending_edits}`).join();
  useEffect(() => { generation.current++; setControls(null); setSources([]); setSettings({}); setPreviews({}); setBusy(false); setError(null); setStatus(""); return () => { generation.current++; }; }, [jobId]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    getDestinationVariants(jobId).then(data => {
      if (!alive) return;
      setControls(data);
      setSettings(current => ({...Object.fromEntries(Object.entries(data.profiles).map(([key, value]) => [key, value.settings])), ...current}));
      setSources(current => current.filter(id => data.sources.some(source => source.job.id === id)));
    }).catch(e => { if (alive) setError(e instanceof Error ? e.message : "Could not load destination clips."); });
    return () => { alive = false; };
  }, [jobId, open, stamp, refresh]);
  const choices = (controls?.sources ?? []).filter(source => sources.includes(source.job.id)).flatMap(source => platforms.flatMap(platform => {
    const config = settings[platform];
    if (!config) return [];
    const choice = {source_job: source.job.id, revision: source.revision, settings: config};
    return [{key: JSON.stringify(choice), choice, title: source.job.audio_name, label: controls!.profiles[platform].label}];
  }));
  const choiceStamp = choices.map(choice => choice.key).join();
  useEffect(() => { media.current?.querySelectorAll("video").forEach(video => video.pause()); }, [open, choiceStamp]);
  function change(platform: Platform, patch: Partial<ShortExport>) { setSettings(current => ({...current, [platform]: {...current[platform]!, ...patch}})); setStatus(""); }
  async function preview() {
    const version = generation.current;
    setBusy(true); setError(null);
    try {
      for (let i = 0; i < choices.length; i++) {
        const item = choices[i];
        if (previews[item.key]) continue;
        setStatus(`Previewing destination version ${i + 1} of ${choices.length}…`);
        const video = await previewDestinationVariant(jobId, item.choice);
        if (generation.current !== version) return;
        setPreviews(current => ({...current, [item.key]: {video, reviewed: false}}));
      }
      setStatus("Destination previews ready. Watch and hear each version before export.");
    } catch (e) { if (generation.current === version) { setError(e instanceof Error ? e.message : "Preview failed."); setStatus(""); } }
    finally { if (generation.current === version) setBusy(false); }
  }
  const reviewed = choices.length > 0 && choices.every(item => previews[item.key]?.reviewed);
  async function queue() {
    const version = generation.current;
    setBusy(true); setError(null);
    try {
      const result = await exportDestinationVariants(jobId, choices.map(item => previews[item.key].video.variant_id));
      if (generation.current === version) setStatus(`${result.jobs.length} destination exports queued. Find them in Your clip exports below.`);
    } catch (e) { if (generation.current === version) setError(e instanceof Error ? e.message : "Could not export destination versions."); }
    finally { if (generation.current === version) setBusy(false); }
  }
  return <details className="story-composer destination-variants" onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>Destination versions · one clip, every stage</summary>
    {open && <div className="composer-body">
      <p>Turn finished clips into separately editable Shorts, Reels, TikTok and WhatsApp exports. Preview the current saved edits with each destination’s portrait guides and sound target.</p>
      <fieldset disabled={busy}>
        <button className="btn" onClick={() => { setError(null); setRefresh(value => value + 1); }}>Refresh source clips</button>
        <button className="btn" disabled={!controls?.sources.length} onClick={() => setSources(controls!.sources.slice(0, 6).map(source => source.job.id))}>Select finished source clips</button>
        <div className="collection-options">{controls?.sources.map(source => <label className="check" key={source.job.id}>
          <input type="checkbox" aria-label={`Use source ${source.job.audio_name}`} checked={sources.includes(source.job.id)} disabled={sources.length >= 6 && !sources.includes(source.job.id)} onChange={e => setSources(current => e.target.checked ? [...current, source.job.id] : current.filter(id => id !== source.job.id))} />
          <span>{source.job.audio_name} · {source.seconds.toFixed(1)}s</span>
        </label>)}</div>
        {controls && !controls.sources.length && <p className="hint">Finish a batch clip export first. Clips must be between 3 and 60 seconds.</p>}
        <div className="destination-profiles">{Object.entries(controls?.profiles ?? {}).map(([key, profile]) => {
          const platform = key as Platform, config = settings[platform];
          if (!config) return null;
          return <section className="story-block" key={platform}>
            <label className="check"><input type="checkbox" aria-label={`Make ${profile.label} version`} checked={platforms.includes(platform)} onChange={e => setPlatforms(current => e.target.checked ? [...current, platform] : current.filter(item => item !== platform))} /><strong>{profile.label}</strong></label>
            <p className="hint">Sound target: {profile.target_lufs} LUFS · {profile.true_peak} dBTP</p>
            <label className="label">Export height<select aria-label={`${profile.label} export height`} value={config.height} onChange={e => change(platform, {height: Number(e.target.value) as 1280 | 1920})}><option value={1280}>1280 pixels</option><option value={1920}>1920 pixels</option></select></label>
            <label className="check"><input type="checkbox" checked={config.progress} onChange={e => change(platform, {progress: e.target.checked})} />Progress line</label>
            <details><summary>Placement guides</summary><div className="destination-guide-controls">{(["top", "bottom", "left", "right"] as const).map(side => <label className="label" key={side}>{side} inset (%)<input type="number" aria-label={`${profile.label} ${side} inset`} min={side === "bottom" ? 8 : 4} max={side === "bottom" ? 35 : 25} step={1} value={Math.round(config.safe_area[side] * 100)} onChange={e => change(platform, {safe_area: {...config.safe_area, [side]: Number(e.target.value) / 100}})} /></label>)}</div></details>
          </section>;
        })}</div>
        <p className="hint">Uses each clip’s current saved edit, including pending edits. Source clips stay intact. Placement guides help position captions and text; platform overlays can change.</p>
        <button className="btn btn-primary" disabled={!choices.length || !choices.some(item => !previews[item.key])} onClick={() => void preview()}>Preview destination versions</button>
        <label className="check"><input type="checkbox" checked={guides} onChange={e => setGuides(e.target.checked)} />Show placement guides on previews</label>
      </fieldset>
      <div className="destination-previews" ref={media}>{choices.map(item => {
        const result = previews[item.key];
        if (!result) return null;
        const area = result.video.settings.safe_area;
        return <section className="story-block" key={item.key}>
          <h4>{item.title} · {item.label}</h4><p className="hint">{result.video.settings.height}px · {result.video.destination}</p>
          <div className="destination-media"><video controls playsInline preload="metadata" src={result.video.url} aria-label={`${item.label} destination preview`} />
            {guides && <div className="destination-guide" aria-label="Caption and text placement guide" style={{top: `${area.top * 100}%`, bottom: `${area.bottom * 100}%`, left: `${area.left * 100}%`, right: `${area.right * 100}%`}} />}
          </div>
          <p className="hint">{result.video.music_note}</p>
          {result.video.sound && <p className="hint">Measured sound: {result.video.sound.integrated_lufs?.toFixed(1) ?? "unavailable"} LUFS · {result.video.sound.true_peak?.toFixed(1) ?? "unavailable"} dBTP{!result.video.sound.passed && " · Check sound before export"}</p>}
          <label className="check"><input type="checkbox" disabled={busy} checked={result.reviewed} onChange={e => { const checked = e.target.checked; setPreviews(current => ({...current, [item.key]: {...current[item.key], reviewed: checked}})); }} />I reviewed this {item.label} version</label>
        </section>;
      })}</div>
      <button className="btn btn-primary" disabled={busy || !reviewed} onClick={() => void queue()}>Export reviewed destination versions</button>
      {status && <p role="status">{status}</p>}{error && <Notice tone="error">{error}</Notice>}
    </div>}
  </details>;
}
