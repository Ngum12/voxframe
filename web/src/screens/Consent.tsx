/**
 * First-run sourcing consent.
 *
 * Imagery sourcing is the one feature that sends anything off this machine, so
 * it is a decision the user makes knowingly rather than a default they discover
 * afterwards (D-116). Shown once, remembered, changeable in Settings.
 *
 * The wording states plainly what is sent, to whom, and why — no persuasion,
 * and "Not now" is a real choice, presented as an equal option rather than a
 * grey afterthought.
 */

import { useState } from "react";
import { saveSettings } from "../api";

export function Consent({ onDecided }: { onDecided: (enabled: boolean) => void }) {
  const [saving, setSaving] = useState(false);

  const decide = async (enabled: boolean) => {
    setSaving(true);
    try {
      await saveSettings({ sourcing_consent: enabled });
    } finally {
      setSaving(false);
      onDecided(enabled);
    }
  };

  return (
    <div className="center-narrow">
      <div className="card">
        <header>
          <h1>Should Voxframe look for images online?</h1>
        </header>

        <p>
          Voxframe works entirely offline. It can also search free stock photo
          libraries to illustrate your scenes, which usually makes a noticeably
          better video — but that means sending some information off this
          machine, so it is your choice.
        </p>

        <h3 style={{ marginTop: 18, marginBottom: 8 }}>If you turn this on</h3>
        <ul className="list-plain">
          <li>
            <strong>What is sent:</strong> a few words from your transcript, as
            a search query — for example “a river through a forest”.
          </li>
          <li>
            <strong>Where:</strong> Openverse, and Pexels or Pixabay if you add
            a free key for them.
          </li>
          <li>
            <strong>What is not sent:</strong> your audio, your video, your full
            transcript, and anything identifying you.
          </li>
        </ul>

        <h3 style={{ marginTop: 18, marginBottom: 8 }}>If you leave it off</h3>
        <p className="muted">
          Nothing leaves this machine. Voxframe uses images you add to your own
          library, and any scene without a match shows a plain background.
          Captions, motion, cards and timing all work exactly the same.
        </p>

        <div className="actions">
          <button
            type="button"
            className="btn"
            disabled={saving}
            onClick={() => void decide(false)}
          >
            Not now
          </button>
          <span className="spacer" />
          <button
            type="button"
            className="btn btn-primary"
            disabled={saving}
            onClick={() => void decide(true)}
          >
            Search for images
          </button>
        </div>

        <p className="muted" style={{ marginTop: 14 }}>
          You can change this at any time in Settings.
        </p>
      </div>
    </div>
  );
}
