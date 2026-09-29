"""Find the visually important region of an image.

Ken Burns zooming into the geometric centre often lands on sky, water or
out-of-focus background. Aiming the move at the subject is most of the
difference between motion that looks composed and motion that looks automatic.

Method
------
A spectral-residual saliency map (Hou & Zhang 2007), computed with numpy:

1. FFT the greyscale image.
2. Subtract the smoothed log-amplitude from itself. What remains is the part
   of the spectrum that is *not* predictable from the rest of the image — which
   is, loosely, what stands out.
3. Inverse FFT, square, blur.

Chosen over the alternatives because it needs no model download, no extra
dependency and no licence to audit, and degrades gracefully: on an image with
no clear subject it returns something near the centre, which is the sensible
default anyway. Measured at roughly 40 ms per photograph on CPU (D-047).

It is not a person or object detector. For a portrait it will usually find the
face; for a landscape it finds the horizon or a peak. Both are better aim
points than the geometric centre.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import structlog

__all__ = ["SaliencyResult", "find_subject_center"]

log = structlog.get_logger(__name__)

#: Working resolution for the saliency computation. Detail beyond this does not
#: change where the subject is, and the FFT cost grows quickly.
_WORK_SIZE = 256

#: Keep the aim point away from the frame edge, or the crop runs into the
#: boundary and the move stalls against it.
_EDGE_MARGIN = 0.22


class SaliencyResult:
    """Where the subject is, and how confident that estimate is.

    Attributes:
        center: ``(x, y)`` in ``[0, 1]``, clamped away from the edges.
        confidence: 0-1. Low values mean the saliency map was flat, so the
            centre is a fallback rather than a finding.
    """

    __slots__ = ("center", "confidence")

    def __init__(self, center: tuple[float, float], confidence: float) -> None:
        self.center = center
        self.confidence = confidence

    @property
    def is_confident(self) -> bool:
        """Whether the estimate is worth acting on.

        Below this, aiming at the reported point is no better than aiming at
        the centre, and may be worse if the map picked up noise.
        """
        return self.confidence >= 0.15

    def __repr__(self) -> str:
        return (
            f"SaliencyResult(center=({self.center[0]:.3f}, {self.center[1]:.3f}), "
            f"confidence={self.confidence:.3f})"
        )


def _spectral_residual(grey: np.ndarray) -> np.ndarray:
    """Compute a spectral-residual saliency map."""
    spectrum = np.fft.fft2(grey)
    log_amplitude = np.log(np.abs(spectrum) + 1e-8)
    phase = np.angle(spectrum)

    # A 3x3 box blur of the log amplitude approximates "what this spectrum
    # would look like if nothing stood out".
    kernel = np.ones((3, 3), dtype=np.float32) / 9.0
    smoothed = _convolve2d(log_amplitude, kernel)
    residual = log_amplitude - smoothed

    reconstructed = np.fft.ifft2(np.exp(residual + 1j * phase))
    saliency = np.abs(reconstructed) ** 2

    # Blur again: the raw map is speckled, and the aim point should follow the
    # broad region of interest rather than the single brightest pixel.
    blur = np.ones((9, 9), dtype=np.float32) / 81.0
    return _convolve2d(saliency, blur)


def _convolve2d(image: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Convolve with edge padding, using numpy only.

    OpenCV would be faster, but this keeps saliency usable when only the core
    dependencies are installed.
    """
    pad_y, pad_x = kernel.shape[0] // 2, kernel.shape[1] // 2
    padded = np.pad(image, ((pad_y, pad_y), (pad_x, pad_x)), mode="edge")

    result = np.zeros_like(image, dtype=np.float64)
    for dy in range(kernel.shape[0]):
        for dx in range(kernel.shape[1]):
            weight = kernel[dy, dx]
            if weight:
                result += weight * padded[
                    dy : dy + image.shape[0], dx : dx + image.shape[1]
                ]
    return result


def find_subject_center(image_path: Path) -> SaliencyResult:
    """Estimate where the subject of an image sits.

    Args:
        image_path: The image to analyse.

    Returns:
        The aim point and a confidence. On any failure — unreadable file,
        missing Pillow — returns the centre with zero confidence rather than
        raising: a bad aim point should degrade the motion, not fail the
        render.
    """
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            grey_image = image.convert("L").resize(
                (_WORK_SIZE, _WORK_SIZE), Image.Resampling.BILINEAR
            )
            grey = np.asarray(grey_image, dtype=np.float64) / 255.0
    except Exception as exc:
        log.debug("saliency.unreadable", path=str(image_path), error=str(exc))
        return SaliencyResult((0.5, 0.5), 0.0)

    saliency = _spectral_residual(grey)

    total = float(saliency.sum())
    if total <= 0:
        return SaliencyResult((0.5, 0.5), 0.0)

    # Centre of mass of the saliency map, weighted by intensity.
    ys, xs = np.mgrid[0:_WORK_SIZE, 0:_WORK_SIZE]
    center_x = float((saliency * xs).sum() / total) / _WORK_SIZE
    center_y = float((saliency * ys).sum() / total) / _WORK_SIZE

    # Confidence: how concentrated the map is. A flat map spreads its mass
    # evenly and its centre of mass lands near the middle regardless, so the
    # result would be meaningless.
    peak = float(saliency.max())
    mean = float(saliency.mean())
    confidence = 0.0 if peak <= 0 else min(1.0, (peak - mean) / peak)

    clamped = (
        max(_EDGE_MARGIN, min(1.0 - _EDGE_MARGIN, center_x)),
        max(_EDGE_MARGIN, min(1.0 - _EDGE_MARGIN, center_y)),
    )

    return SaliencyResult(clamped, confidence)
