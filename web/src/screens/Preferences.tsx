/**
 * The Settings screen: sourcing consent and API keys.
 *
 * Keys are stored in the user's own config directory, never the repository
 * (D-113). A stored key is shown masked and can be replaced but not read back,
 * which is the same contract a password field has.
 *
 * The "check" button exists because a key pasted with a trailing space, or one
 * that was revoked, otherwise announces itself as a video full of gradients an
 * hour later.
 */

import { useEffect, useState } from "react";
import {
  ApiError,
  checkForUpdates,
  checkKey,
  chooseLibraryFolder,
  getFolders,
  getSettings,
  openFolder,
  saveSettings,
  type FolderName,
  type Folders,
  type Theme,
  type UpdateCheck,
  type UserSettings,
} from "../api";
import { Field, Notice } from "../components";
import { ModelSetup } from "./GettingReady";

const ADAPTERS: Record<string, { label: string; url: string; note: string }> = {
  pexels: {
    label: "Pexels",
    url: "https://www.pexels.com/api/",
    note: "Free key. 200 requests an hour.",
  },
  pixabay: {
    label: "Pixabay",
    url: "https://pixabay.com/api/docs/",
    note: "Free key. 100 requests a minute.",
  },
};

type CheckState = { tone: "ok" | "error" | "busy"; text: string } | null;

const THEMES: { id: Theme; label: string }[] = [
  { id: "system", label: "Follow the system" },
  { id: "dark", label: "Dark" },
  { id: "light", label: "Light" },
];

/** Show a look at once; the server serves it from then on (D-185). */
function applyTheme(theme: Theme) {
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
}

/** Light, dark, or whatever the computer is set to (D-185). */
function Appearance({ initial }: { initial: Theme }) {
  const [theme, setTheme] = useState<Theme>(initial);
  const [error, setError] = useState<string | null>(null);

  const choose = async (next: Theme) => {
    const previous = theme;
    setTheme(next);
    applyTheme(next);
    setError(null);
    try {
      await saveSettings({ theme: next });
    } catch {
      setTheme(previous);
      applyTheme(previous);
      setError("The look could not be saved.");
    }
  };

  return (
    <div className="card">
      <header>
        <h2>Appearance</h2>
        <p>Dark or light. “Follow the system” matches your computer’s setting as it changes.</p>
      </header>
      <div className="filters" role="radiogroup" aria-label="Theme">
        {THEMES.map((option) => (
          <button
            key={option.id}
            type="button"
            role="radio"
            className="chip"
            aria-checked={theme === option.id}
            aria-pressed={theme === option.id}
            onClick={() => theme !== option.id && void choose(option.id)}
          >
            {option.label}
          </button>
        ))}
      </div>
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}

/**
 * The version, and a button to check for a newer one (D-155). Nothing is
 * asked of the internet until the button is pressed.
 */
const FOLDER_LABELS: Record<FolderName, string> = {
  library: "Library",
  videos: "Finished videos",
  data: "App data (cache and models)",
};

/** Where things are kept, with a way to open each and to move the library (D-156). */
function YourFolders() {
  const [folders, setFolders] = useState<Folders | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void getFolders().then(setFolders, () => setError("The folders could not be read."));
  }, []);

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      setFolders(await getFolders());
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "That could not be done.");
    } finally {
      setBusy(false);
    }
  };

  if (!folders) return error ? <Notice tone="error">{error}</Notice> : null;

  return (
    <div className="card">
      <header>
        <h2>Your folders</h2>
        <p>Where Voxframe keeps things on this computer.</p>
      </header>
      <dl className="detail-list folder-list">
        {(Object.keys(FOLDER_LABELS) as FolderName[]).map((name) => (
          <div key={name} className="folder-row">
            <dt>{FOLDER_LABELS[name]}</dt>
            <dd>
              <code>{folders[name]}</code>{" "}
              <button
                type="button"
                className="link-button"
                disabled={busy}
                onClick={() => void act(() => openFolder(name))}
              >
                Open folder
              </button>
            </dd>
          </div>
        ))}
      </dl>
      {folders.library_pending && (
        <Notice tone="info">
          The library will be <code>{folders.library_pending}</code> from the next time
          Voxframe starts.
        </Notice>
      )}
      {folders.library_from_environment ? (
        <p className="muted">
          The library folder is set by VOXFRAME_LIBRARY_PATH, which takes precedence.
        </p>
      ) : (
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={() => void act(() => chooseLibraryFolder())}
        >
          Change library folder…
        </button>
      )}
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}

function Updates() {
  const [result, setResult] = useState<UpdateCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const check = async () => {
    setChecking(true);
    setError(null);
    try {
      setResult(await checkForUpdates());
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The check could not be done.");
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="card">
      <header>
        <h2>Updates</h2>
        <p>
          Voxframe never checks on its own. The button asks GitHub, once, whether a
          newer version has been released.
        </p>
      </header>
      <div className="actions" style={{ marginTop: 0 }}>
        <button type="button" className="btn" disabled={checking} onClick={() => void check()}>
          {checking ? "Checking…" : "Check for updates"}
        </button>
        {result && (
          <span className="muted" role="status">
            {result.latest === null
              ? `You have ${result.current}. No release has been published yet.`
              : result.newer
                ? `Version ${result.latest.replace(/^v/, "")} is available; you have ${result.current}.`
                : `You have the latest version, ${result.current}.`}
          </span>
        )}
      </div>
      {result?.newer && (
        <p style={{ marginTop: 8 }}>
          <a href={result.url} target="_blank" rel="noreferrer noopener">
            Download it from GitHub
          </a>
        </p>
      )}
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}

export function Preferences({ onChanged }: { onChanged: () => void }) {
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [checks, setChecks] = useState<Record<string, CheckState>>({});
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    void getSettings().then(setSettings);
  }, []);

  if (!settings) {
    return <p className="muted">Loading…</p>;
  }

  const setConsent = async (enabled: boolean) => {
    await saveSettings({ sourcing_consent: enabled });
    setSettings(await getSettings());
    onChanged();
  };

  const storeKey = async (adapter: string) => {
    const value = drafts[adapter] ?? "";
    await saveSettings({ api_keys: { [adapter]: value } });
    setDrafts({ ...drafts, [adapter]: "" });
    setSettings(await getSettings());
    setSaved(true);
    onChanged();
  };

  const runCheck = async (adapter: string) => {
    setChecks({ ...checks, [adapter]: { tone: "busy", text: "Checking…" } });
    try {
      const result = await checkKey(adapter, drafts[adapter] ?? "");
      setChecks({
        ...checks,
        [adapter]: {
          tone: result.valid ? "ok" : "error",
          text: result.detail,
        },
      });
    } catch (error) {
      setChecks({
        ...checks,
        [adapter]: {
          tone: "error",
          text: error instanceof Error ? error.message : "Check failed.",
        },
      });
    }
  };

  return (
    <>
      <header style={{ marginBottom: 20 }}>
        <h1>Settings</h1>
      </header>

      {saved && <Notice tone="ok">Saved.</Notice>}

      <Appearance initial={settings.theme ?? "system"} />

      <div className="card">
        <header>
          <h2>Imagery sourcing</h2>
          <p>
            Sends a few words from your transcript to free stock photo libraries
            to illustrate scenes. Your audio and transcript are never sent.
          </p>
        </header>

        <label className="toggle">
          <input
            type="checkbox"
            checked={settings.sourcing.consent === true}
            onChange={(event) => void setConsent(event.target.checked)}
          />
          <span className="text">
            <strong>Search online for images</strong>
            <span>
              {/* Three states, because consent without a key searches
                  nothing useful (D-116, D-132). */}
              {settings.sourcing.consent !== true
                ? "Off. Only your own library is used; unmatched scenes show a plain background."
                : settings.sourcing.enabled
                  ? "On. Scenes your library cannot fill are searched for online while the video is made."
                  : "On, but no key is set. Add a free Pexels or Pixabay key below — without one, searching finds very little."}
            </span>
          </span>
        </label>
      </div>

      <div className="card">
        <header>
          <h2>API keys</h2>
          <p>
            Optional. Openverse needs no key, but its permissively-licensed pool
            is mostly clipart — a free Pexels or Pixabay key makes a large
            difference to how many scenes find a good photograph.
          </p>
        </header>

        {Object.entries(ADAPTERS).map(([adapter, info]) => {
          const stored = settings.api_keys[adapter];
          const fromEnvironment = settings.environment_keys.includes(adapter);
          const check = checks[adapter];

          return (
            <div key={adapter} style={{ marginBottom: 22 }}>
              <Field
                label={info.label}
                htmlFor={`key-${adapter}`}
                hint={info.note}
              >
                <div className="key-row">
                  <input
                    id={`key-${adapter}`}
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    disabled={fromEnvironment}
                    placeholder={stored ? `Stored (${stored})` : "Paste your key"}
                    value={drafts[adapter] ?? ""}
                    onChange={(event) =>
                      setDrafts({ ...drafts, [adapter]: event.target.value })
                    }
                  />
                  <div style={{ display: "flex", gap: 6 }}>
                    <button
                      type="button"
                      className="btn"
                      disabled={fromEnvironment || (!drafts[adapter] && !stored)}
                      onClick={() => void runCheck(adapter)}
                    >
                      Check key
                    </button>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={fromEnvironment || !drafts[adapter]}
                      onClick={() => void storeKey(adapter)}
                    >
                      Save
                    </button>
                  </div>
                </div>
              </Field>

              {fromEnvironment && (
                <p className="muted" style={{ marginTop: -12 }}>
                  Set in your environment or <code>.env</code>, which takes
                  precedence. Change it there.
                </p>
              )}

              {check && (
                <p className="status-line" data-tone={check.tone} role="status">
                  <span aria-hidden="true">
                    {check.tone === "ok" ? "✓" : check.tone === "error" ? "✗" : "·"}
                  </span>
                  {check.text}
                </p>
              )}

              <p className="muted" style={{ marginTop: 6 }}>
                <a href={info.url} target="_blank" rel="noreferrer noopener">
                  Get a free {info.label} key
                </a>
              </p>
            </div>
          );
        })}

        <p className="muted">
          Keys are stored in your own configuration directory on this machine,
          never in the Voxframe project, and are never included in a rendered
          video, a log or a scene plan.
        </p>
      </div>

      <div className="card">
        <header>
          <h2>Models</h2>
          <p>Downloaded once; everything then runs on this computer.</p>
        </header>
        <ModelSetup />
      </div>

      <YourFolders />

      <Updates />
    </>
  );
}
