/**
 * Small shared pieces.
 *
 * Every one of these carries an accessibility obligation that is easy to lose
 * if it is re-implemented per screen: notices are announced, the step rail is a
 * real list with current state, and nothing conveys meaning by colour alone.
 */

import type { ReactNode } from "react";

export type Tone = "info" | "warn" | "error" | "ok";

const MARKS: Record<Tone, string> = {
  info: "i",
  warn: "!",
  error: "!",
  ok: "✓",
};

/**
 * A message the user must be able to read and a screen reader must announce.
 *
 * The mark is not decoration: colour alone must never carry the difference
 * between a warning and a confirmation.
 */
export function Notice({
  tone = "info",
  children,
  live = false,
}: {
  tone?: Tone;
  children: ReactNode;
  live?: boolean;
}) {
  return (
    <div
      className={`notice notice-${tone}`}
      role={tone === "error" ? "alert" : "status"}
      aria-live={live || tone === "error" ? "polite" : undefined}
    >
      <span className="mark" aria-hidden="true">
        {MARKS[tone]}
      </span>
      <div>{children}</div>
    </div>
  );
}

export interface StepInfo {
  id: string;
  label: string;
}

/** The step rail. A real ordered list, with the current step marked. */
export function Steps({ steps, current }: { steps: StepInfo[]; current: string }) {
  const index = steps.findIndex((step) => step.id === current);

  return (
    <nav aria-label="Progress">
      <ol className="steps">
        {steps.map((step, position) => {
          const state =
            position < index ? "done" : position === index ? "current" : "todo";
          return (
            <li
              key={step.id}
              data-state={state}
              aria-current={state === "current" ? "step" : undefined}
            >
              <span className="mark" aria-hidden="true">
                {state === "done" ? "✓" : position + 1}
              </span>
              {step.label}
              {state === "done" && <span className="sr-only"> (completed)</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** A labelled form field. The label is always present, never a placeholder. */
export function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  htmlFor: string;
  children: ReactNode;
}) {
  const hintId = hint ? `${htmlFor}-hint` : undefined;

  return (
    <div className="field">
      <label className="label" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {hint && (
        <p className="hint" id={hintId}>
          {hint}
        </p>
      )}
    </div>
  );
}

/** A card-style radio. The input stays real, so keyboard and AT still work. */
export function Choice({
  name,
  value,
  checked,
  onChange,
  title,
  description,
  children,
}: {
  name: string;
  value: string;
  checked: boolean;
  onChange: (value: string) => void;
  title?: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <label className="choice">
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        onChange={() => onChange(value)}
      />
      {children ?? (
        <>
          <strong>{title}</strong>
          {description && <span>{description}</span>}
        </>
      )}
    </label>
  );
}

/** Seconds as "4:07", for durations a person reads. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "unknown";
  const whole = Math.round(seconds);
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Estimate render time from audio length.
 *
 * Uses the factors measured in Phase 6 rather than a guess: about 0.7x
 * realtime at 720p, scaled for height and for draft quality. Telling someone a
 * 40-minute lecture will take "a few minutes" when it takes half an hour is the
 * easiest way to lose their trust, so this is deliberately not optimistic.
 */
export function estimateRenderSeconds(
  audioSeconds: number,
  height: number,
  quality: string,
): number {
  const base = 0.75;
  const scale = (height / 720) ** 1.4;
  const qualityFactor = quality === "draft" ? 0.55 : quality === "high" ? 1.5 : 1;
  return audioSeconds * base * scale * qualityFactor;
}

export function formatEstimate(seconds: number): string {
  if (seconds < 90) return "under 2 minutes";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `about ${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = seconds / 3600;
  return `about ${hours.toFixed(1)} hours`;
}
