"""The score's synthesised sounds: pads, sub bass, risers, impacts, rooms (D-176).

Nothing here is a recording: these widen the palette with no download. Each
sound takes its own seed, so it is the same every time it is made.
"""

from __future__ import annotations

import random

import numpy as np

from voxframe.music.score.instruments import RATE, envelope

__all__ = ["impact", "pad", "riser", "room", "sub_bass"]


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def pad(
    notes: tuple[int, ...], length: float, gain: float, brightness: float, seed: int
) -> np.ndarray:
    """Detuned additive voices, slowly breathing, softly filtered."""
    from scipy import signal

    rng = random.Random(seed)
    attack, release = 2.5, 3.0
    # Made at a quarter of the rate, then upsampled: the pad is filtered below
    # 3 kHz, so nothing it keeps is lost, and it costs a quarter as much.
    low_rate, factor = RATE // 4, 4
    n = int((length + release) * RATE)
    m = -(-n // factor)
    t = np.arange(m, dtype=np.float64) / low_rate
    out = np.zeros((m, 2))
    for channel, detunes in enumerate(((-7.0, 3.0), (-3.0, 7.0))):
        for midi in notes:
            for cents in detunes:
                f = _hz(midi + cents / 100)
                lfo = 1 + 0.15 * np.sin(
                    2 * np.pi * rng.uniform(0.05, 0.12) * t + rng.uniform(0, 6.28)
                )
                for h in range(1, 9):
                    if f * h > 5000:
                        break
                    out[:, channel] += (
                        lfo * np.sin(2 * np.pi * f * h * t + rng.uniform(0, 6.28)) / h**1.8
                    )
    sos = signal.butter(2, 600 + 2200 * brightness, "low", fs=low_rate, output="sos")
    filtered = signal.sosfilt(sos, out, axis=0)
    shaped = signal.resample_poly(filtered, factor, 1, axis=0)[:n].astype(np.float32)
    shaped /= max(1e-6, float(np.max(np.abs(shaped))))
    envelope(shaped, attack, length, release)
    result: np.ndarray = shaped * np.float32(gain)
    return result


def sub_bass(midi: int, length: float, gain: float) -> np.ndarray:
    """A sine with a little second harmonic, gently saturated."""
    n = int((length + 0.4) * RATE)
    t = np.arange(n) / RATE
    f = _hz(midi)
    tone = np.sin(2 * np.pi * f * t) + 0.18 * np.sin(4 * np.pi * f * t)
    tone = np.tanh(1.4 * tone) / np.tanh(1.4)
    stereo = np.repeat(tone.astype(np.float32)[:, None], 2, axis=1)
    envelope(stereo, 0.06, length, 0.4)
    result: np.ndarray = stereo * np.float32(gain)
    return result


def riser(length: float, gain: float, seed: int) -> np.ndarray:
    """Noise whose band climbs, swelling, then cut just before the next line."""
    from scipy import signal

    n = max(int(length * RATE), RATE // 4)
    generator = np.random.default_rng(seed)
    channels = []
    for _ in range(2):
        f, _, z = signal.stft(generator.standard_normal(n), fs=RATE, nperseg=2048)
        centre = np.geomspace(300, 5000, z.shape[1])
        mask = np.exp(-0.5 * (np.log2(np.maximum(f[:, None], 1) / centre[None, :]) / 0.35) ** 2)
        _, shaped = signal.istft(z * mask, fs=RATE, nperseg=2048)
        channels.append(np.pad(shaped, (0, max(0, n - len(shaped))))[:n])
    noise = np.stack(channels, axis=1)
    noise /= max(1e-6, float(np.max(np.abs(noise))))
    swell = np.linspace(0, 1, n) ** 2.5
    cut = int(0.03 * RATE)
    swell[-cut:] *= np.linspace(1, 0, cut)
    result: np.ndarray = (noise * swell[:, None] * gain).astype(np.float32)
    return result


def impact(gain: float, seed: int) -> np.ndarray:
    """A soft low hit: a falling sine, and a breath of filtered noise."""
    from scipy import signal

    n = int(3.0 * RATE)
    t = np.arange(n) / RATE
    freq = 38 + 34 * np.exp(-t * 3.0)
    boom = np.sin(2 * np.pi * np.cumsum(freq) / RATE) * np.exp(-t * 1.6)
    noise = np.random.default_rng(seed).standard_normal(n)
    noise = signal.sosfilt(signal.butter(2, 400, "low", fs=RATE, output="sos"), noise) * np.exp(
        -t * 9
    )
    hit = boom + 0.6 * noise / max(1e-6, float(np.max(np.abs(noise))))
    attack = int(0.004 * RATE)
    hit[:attack] *= np.linspace(0, 1, attack)
    hit /= max(1e-6, float(np.max(np.abs(hit))))
    result: np.ndarray = (np.repeat(hit[:, None], 2, axis=1) * gain).astype(np.float32)
    return result


def room(seconds: float, seed: int, damping: float = 0.5) -> np.ndarray:
    """A synthetic hall: early reflections, then a stereo tail whose highs die first."""
    from scipy import signal

    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    generator = np.random.default_rng(seed)
    noise = generator.standard_normal((n, 2))
    low = signal.sosfilt(signal.butter(2, 2500, "low", fs=RATE, output="sos"), noise, axis=0)
    high = noise - low
    rt = seconds * 0.7
    tail = low * np.exp(-6.9 * t / rt)[:, None] + high * np.exp(-6.9 * t / (rt * damping))[:, None]
    tail[: int(0.02 * RATE)] = 0
    for delay, level in ((0.011, 0.5), (0.019, 0.4), (0.027, 0.35), (0.041, 0.3), (0.058, 0.25)):
        i = int(delay * RATE)
        tail[i, 0] += level * generator.choice([-1, 1])
        tail[i + int(0.003 * RATE), 1] += level * generator.choice([-1, 1])
    result: np.ndarray = (tail / np.sqrt(np.sum(tail**2) / 2)).astype(np.float32)
    return result
