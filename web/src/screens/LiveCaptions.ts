/**
 * The studio's live captions (D-196).
 *
 * The player shows the video without its captions, and the captions are drawn
 * over it here by libass, the same library FFmpeg burns them in with, built
 * for the browser (JASSUB). They are drawn from the very document the next
 * render will burn in, fetched from the plan as it stands, so a change to the
 * captions shows the moment it is saved, exactly as the video will have it.
 */

import JASSUB from "jassub";
import { useEffect, useRef } from "react";

import interBold from "../fonts/Inter-Bold.ttf?url";
import interRegular from "../fonts/Inter-Regular.ttf?url";

/** Whether this browser can draw them: a worker, WebAssembly, an offscreen canvas. */
export function liveCaptionsSupported(): boolean {
  return (
    typeof Worker !== "undefined" &&
    typeof WebAssembly === "object" &&
    typeof OffscreenCanvas !== "undefined" &&
    "transferControlToOffscreen" in HTMLCanvasElement.prototype &&
    "requestVideoFrameCallback" in HTMLVideoElement.prototype
  );
}

/**
 * Draw ``captions`` over ``video``, and redraw them whenever they change.
 *
 * ``onFailed`` is called if the renderer cannot start, so the studio can show
 * the captioned video instead: a preview must never be missing its captions.
 */
export function useLiveCaptions(
  video: HTMLVideoElement | null,
  captions: string | null,
  onFailed: (reason: string) => void,
): void {
  const instance = useRef<JASSUB | null>(null);
  const failed = useRef(onFailed);
  failed.current = onFailed;
  const latest = useRef(captions);
  latest.current = captions;
  const hasCaptions = captions !== null;

  useEffect(() => {
    if (!video || !hasCaptions || latest.current === null) return;
    let created: JASSUB;
    try {
      created = new JASSUB({
        video,
        subContent: latest.current,
        // The fonts the video is rendered with, and nothing looked up: what
        // is drawn here is what FFmpeg draws.
        fonts: [interRegular, interBold],
        defaultFont: "inter",
        queryFonts: false,
      });
    } catch (caught) {
      failed.current(caught instanceof Error ? caught.message : "The captions could not be drawn.");
      return;
    }
    instance.current = created;
    const frame = video.parentElement;
    created.ready
      .then(() => frame?.setAttribute("data-captions", "live"))
      .catch((caught: unknown) =>
        failed.current(caught instanceof Error ? caught.message : "The captions could not be drawn."),
      );
    return () => {
      frame?.removeAttribute("data-captions");
      instance.current = null;
      void created.destroy().catch(() => undefined);
    };
  }, [video, hasCaptions]);

  useEffect(() => {
    const current = instance.current;
    if (!current || captions === null) return;
    void current.ready
      .then(async () => {
        await current.renderer.setTrack(captions);
        // Paused, the frame on screen is redrawn with the new captions.
        await current.resize(true);
      })
      .catch(() => undefined);
  }, [captions]);
}
