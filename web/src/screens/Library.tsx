/**
 * Screen 6 — the library (D-146).
 *
 * The photographs and clips Voxframe chooses from. A person adds their own,
 * sees where everything came from and under what licence, sees which videos
 * show each one, and takes things out. Provenance is shown on every item,
 * because it is what the credits will say.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  deleteLibraryAsset,
  libraryThumbnailUrl,
  listLibrary,
  uploadToLibrary,
  type LibraryAsset,
  type LibraryPage,
} from "../api";
import { Notice } from "../components";

const PAGE = 60;

const SOURCE_LABELS: Record<string, string> = {
  local: "Added by you",
  pexels: "Pexels",
  pixabay: "Pixabay",
  openverse: "Openverse",
};

const sourceLabel = (source: string) =>
  SOURCE_LABELS[source] ?? source.charAt(0).toUpperCase() + source.slice(1);

const ACCEPT = ".jpg,.jpeg,.png,.webp,.mp4,.mov,.webm,.m4v";

function plural(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`;
}

/** Add photos and clips, credited to the person (owner's decision 4). */
function Uploader({
  author: rememberedAuthor,
  onAdded,
}: {
  author: string;
  onAdded: (message: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [author, setAuthor] = useState(rememberedAuthor);
  const [license, setLicense] = useState("Own work");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setAuthor((current) => current || rememberedAuthor), [rememberedAuthor]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await uploadToLibrary(files, author, license);
      const parts = [`Added ${plural(result.added, "item", "items")} to your library.`];
      if (result.already_there) {
        parts.push(`${plural(result.already_there, "was", "were")} already there.`);
      }
      if (result.failed) parts.push(`${plural(result.failed, "could not", "could not")} be read.`);
      onAdded(parts.join(" "));
      setFiles([]);
      if (fileRef.current) fileRef.current.value = "";
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The files could not be added.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card library-upload" onSubmit={(event) => void submit(event)}>
      <header>
        <h2>Add your own photos and clips</h2>
        <p>
          Voxframe matches them to what is said, just like downloaded images, and
          credits them to you.
        </p>
      </header>

      <div className="field">
        <label className="label" htmlFor="library-files">Photos or clips</label>
        <input
          id="library-files"
          ref={fileRef}
          type="file"
          multiple
          accept={ACCEPT}
          onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
        />
        <p className="hint">JPEG, PNG or WebP photos; MP4, MOV or WebM clips. Up to 50 at a time.</p>
      </div>

      <div className="row">
        <div className="field">
          <label className="label" htmlFor="library-author">Credited to</label>
          <input
            id="library-author"
            type="text"
            value={author}
            maxLength={120}
            placeholder="Your name"
            onChange={(event) => setAuthor(event.target.value)}
          />
        </div>
        <div className="field">
          <label className="label" htmlFor="library-license">Licence</label>
          <input
            id="library-license"
            type="text"
            value={license}
            maxLength={120}
            onChange={(event) => setLicense(event.target.value)}
          />
        </div>
      </div>

      {error && <Notice tone="error">{error}</Notice>}

      <div className="actions">
        <button
          type="submit"
          className="btn btn-primary"
          disabled={busy || files.length === 0 || !author.trim()}
        >
          {busy
            ? "Adding…"
            : files.length > 1
              ? `Add ${files.length} to the library`
              : "Add to the library"}
        </button>
        {busy && (
          <span className="muted">
            The first time takes about twenty seconds while the matching model loads.
          </span>
        )}
      </div>
    </form>
  );
}

/** One asset, larger, with everything known about it and a way to remove it. */
function AssetDetail({
  asset,
  onDeleted,
}: {
  asset: LibraryAsset;
  onDeleted: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setConfirming(false);
    setError(null);
  }, [asset.id]);

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      await deleteLibraryAsset(asset.id);
      onDeleted();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "It could not be removed.");
      setBusy(false);
    }
  };

  return (
    <section className="card library-detail" aria-labelledby="asset-heading">
      <img
        className="scene-current"
        src={libraryThumbnailUrl(asset.id)}
        alt={`${asset.kind === "video" ? "Clip" : "Photo"} by ${asset.author}`}
      />
      <div>
        <h2 id="asset-heading">
          {asset.kind === "video" ? "Clip" : "Photo"} by {asset.author}
        </h2>
        <dl className="detail-list">
          <dt>Licence</dt>
          <dd>{asset.license}</dd>
          <dt>From</dt>
          <dd>
            {sourceLabel(asset.source)}
            {asset.source_url && (
              <>
                {" · "}
                <a href={asset.source_url} target="_blank" rel="noopener noreferrer">
                  original page
                </a>
              </>
            )}
          </dd>
          <dt>Size</dt>
          <dd>
            {asset.width}×{asset.height}
            {asset.duration ? `, ${asset.duration.toFixed(1)}s` : ""}
          </dd>
          <dt>In videos</dt>
          <dd>
            {asset.used_in.length === 0
              ? "Not used yet"
              : asset.used_in.map((use) => use.title).join(", ")}
          </dd>
        </dl>

        {error && <Notice tone="error">{error}</Notice>}

        {!confirming ? (
          <button type="button" className="btn btn-quiet" onClick={() => setConfirming(true)}>
            Remove from the library
          </button>
        ) : (
          <div className="confirm" role="alertdialog" aria-labelledby="confirm-text">
            <p id="confirm-text">
              {asset.used_in.length > 0
                ? `It is shown in ${plural(asset.used_in.length, "video", "videos")}. Those keep their finished files, but updating them will need another image for the scenes that show it. `
                : ""}
              {asset.uploaded_here
                ? "The file you added will be deleted."
                : "The file stays where it is; only the library's entry goes."}
            </p>
            <div className="caption-form-actions">
              <button
                type="button"
                className="btn btn-danger btn-small"
                disabled={busy}
                onClick={() => void remove()}
              >
                {busy ? "Removing…" : "Remove"}
              </button>
              <button
                type="button"
                className="btn btn-quiet btn-small"
                disabled={busy}
                onClick={() => setConfirming(false)}
              >
                Keep it
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

export function Library({ onChanged }: { onChanged: () => void }) {
  const [page, setPage] = useState<LibraryPage | null>(null);
  const [assets, setAssets] = useState<LibraryAsset[]>([]);
  const [source, setSource] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (offset = 0) => {
      try {
        const loaded = await listLibrary(source, offset, PAGE);
        setPage(loaded);
        setAssets((current) => (offset === 0 ? loaded.assets : [...current, ...loaded.assets]));
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : "The library could not be read.");
      }
    },
    [source],
  );

  useEffect(() => {
    setSelected(null);
    void load(0);
  }, [load]);

  const current = assets.find((asset) => asset.id === selected) ?? null;
  const sources = Object.entries(page?.sources ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <>
      <header style={{ marginBottom: 18 }}>
        <h1>Your library</h1>
        <p className="muted" style={{ marginTop: 4 }}>
          The photos and clips Voxframe chooses from. Everything here says where it
          came from, because that is what the credits will say.
        </p>
      </header>

      {error && <Notice tone="error">{error}</Notice>}
      {message && <Notice tone="info">{message}</Notice>}

      <Uploader
        author={page?.author ?? ""}
        onAdded={(text) => {
          setMessage(text);
          setSource("");
          void load(0);
          onChanged();
        }}
      />

      {page && page.all === 0 && (
        <Notice tone="info">
          Your library is empty. Add your own photos above, or turn on online search in
          Settings and Voxframe will add images as you make videos.
        </Notice>
      )}

      {page && page.all > 0 && (
        <>
          <div className="filters" role="group" aria-label="Show">
            <button
              type="button"
              className="chip"
              aria-pressed={source === ""}
              onClick={() => setSource("")}
            >
              All ({page.all})
            </button>
            {sources.map(([name, count]) => (
              <button
                key={name}
                type="button"
                className="chip"
                aria-pressed={source === name}
                onClick={() => setSource(name)}
              >
                {sourceLabel(name)} ({count})
              </button>
            ))}
          </div>

          <ul className="candidates library-grid" aria-label="Library">
            {assets.map((asset) => (
              <li
                key={asset.id}
                className="candidate"
                data-selected={asset.id === selected}
              >
                <button
                  type="button"
                  className="library-item"
                  aria-pressed={asset.id === selected}
                  onClick={() => setSelected(asset.id)}
                >
                  <img
                    src={libraryThumbnailUrl(asset.id)}
                    alt={`${asset.kind === "video" ? "Clip" : "Photo"} by ${asset.author}`}
                    loading="lazy"
                  />
                  {asset.kind === "video" && <span className="scene-badge">clip</span>}
                </button>
                <p className="candidate-note">
                  {asset.author} · {sourceLabel(asset.source)} · {asset.license}
                </p>
                <p className="candidate-note">
                  {asset.used_in.length > 0
                    ? `In ${plural(asset.used_in.length, "video", "videos")}`
                    : "Not used yet"}
                </p>
              </li>
            ))}
          </ul>

          {assets.length < page.total && (
            <div className="actions">
              <button type="button" className="btn" onClick={() => void load(assets.length)}>
                Show more ({page.total - assets.length} left)
              </button>
            </div>
          )}
        </>
      )}

      {current && (
        <AssetDetail
          asset={current}
          onDeleted={() => {
            setMessage("Removed from your library.");
            setSelected(null);
            void load(0);
            onChanged();
          }}
        />
      )}
    </>
  );
}
