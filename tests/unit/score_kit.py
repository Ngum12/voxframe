"""A tiny synthetic sample folder and speech, for testing the score without the real pack.

Every instrument in ``instruments.toml`` gets a few short recordings named
the way the real libraries name theirs, so the whole engine -- reading,
composing, rendering, finishing -- runs in seconds.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

RATE = 44100


def _tone(midi: int, seconds: float, decay: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    f = 440.0 * 2 ** ((midi - 69) / 12)
    wave = np.sin(2 * np.pi * f * t) * np.exp(-t * decay) * 0.3
    return np.stack([wave, wave * 0.9], axis=1).astype(np.float32)


def _hit(seconds: float, seed: int) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    noise = np.random.default_rng(seed).standard_normal(len(t)) * np.exp(-t * 6) * 0.3
    body = np.sin(2 * np.pi * 110 * t) * np.exp(-t * 3) * 0.3
    wave = noise + body
    return np.stack([wave, wave], axis=1).astype(np.float32)


def make_samples(root: Path) -> Path:
    """Write the synthetic sample folder under ``root``; return it."""
    import soundfile as sf

    def write(folder: str, name: str, audio: np.ndarray) -> None:
        path = root / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), audio, RATE, subtype="PCM_16")

    # Pitched sets name notes with middle C as C3 (octave 12 in instruments.toml),
    # except the harp.
    for note, midi in (("C2", 48), ("G2", 55), ("C3", 60), ("G3", 67), ("C4", 72), ("C1", 36)):
        for layer in (2, 3):
            write("piano", f"GPiano_sus_{note}_v{layer}_rr1_Player.wav", _tone(midi, 4.0, 0.8))
        write("violins", f"VlnEns_susVib_{note}_v1.wav", _tone(midi, 5.0, 0.05))
        write("violas", f"ViolaEns_susvib_{note}_v1_1.wav", _tone(midi, 5.0, 0.05))
        write("cellos", f"susvib_{note}_v1_1.wav", _tone(midi, 5.0, 0.05))
        write("contrabass", f"BKCtbss_SusVib_{note}_v1_rr1.wav", _tone(midi, 5.0, 0.05))
        for layer in (1, 2):
            for take in (1, 2):
                write(
                    "violins_spic", f"VlnEns_Spic_{note}_v{layer}_rr{take}.wav", _tone(midi, 0.6, 6)
                )
                write(
                    "violas_spic", f"Violas_spic_{note}_v{layer}_rr{take}.wav", _tone(midi, 0.6, 6)
                )
                write("cellos_spic", f"spic_{note}_v{layer}_RR{take}.wav", _tone(midi, 0.6, 6))
    for note, midi in (("C3", 48), ("C4", 60), ("C5", 72)):
        write("harp", f"KSHarp_{note}_mf1.wav", _tone(midi, 2.5, 1.5))
    for drum in range(1, 4):
        for layer in (2, 3, 4):
            write(
                "timpani_hits",
                f"Timpani{drum}_Hit_v{layer}_rr1_Sum.wav",
                _hit(2.0, drum * 10 + layer),
            )
        write("timpani_rolls", f"Timpani{drum}_Roll_v3_rr1_Sum.wav", _hit(4.0, drum))
    for word in ("pp1", "mf1", "f", "ff"):
        write("bass_drum", f"bassdrum_hit_{word}.wav", _hit(1.5, len(word)))
    write("bass_drum", "bassdrum_cresc_med.wav", _hit(3.0, 9))
    for name in ("cresc_2s", "cresc_4s", "hit_mp1"):
        write("cymbal", f"susCymb1_{name}.wav", _hit(4.5, len(name)))
    for word in ("p", "mf", "f"):
        write("gong", f"gong_{word}.wav", _hit(5.0, len(word) + 20))
    return root


def make_speech(path: Path, seconds: float = 40.0) -> list[tuple[float, float]]:
    """A voice stem of noise bursts as words, at 48 kHz; return the word timings.

    Speech in phrases of about 4 seconds, with two long pauses (at about 14
    and 27 seconds) and a short line after the second, as real speech has.
    """
    import soundfile as sf

    rate = 48000
    rng = np.random.default_rng(3)
    voice = np.zeros(int(seconds * rate), dtype=np.float32)
    words: list[tuple[float, float]] = []
    t = 1.0
    while t < seconds - 4.0:
        if 13.0 < t < 14.0 or 26.0 < t < 27.0:
            t += 2.2  # a long pause
        start, end = t, t + 0.3
        first = int(start * rate)
        voice[first : first + int(0.3 * rate)] = rng.standard_normal(int(0.3 * rate)) * 0.1
        words.append((round(start, 3), round(end, 3)))
        t = end + (0.12 if len(words) % 9 else 0.7)
    sf.write(str(path), voice, rate, subtype="FLOAT")
    return words
