"""BPM and musical-key analysis via librosa, plus the Camelot wheel.

The Camelot mapping and the pure helpers (octave correction, key-from-chroma,
compatible-key logic) are kept import-light so they can be unit tested without
loading audio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

# Pitch classes as returned by librosa's chroma features (index 0 == C).
PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Schmuckler key profiles.
MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)

# Full 24-entry Camelot wheel. Key = (pitch_class_index, mode).
# Value = (camelot_code, rekordbox_tonality).
# minor -> "A" ring, major -> "B" ring.
CAMELOT_WHEEL: dict[tuple[int, str], tuple[str, str]] = {
    # ----- minor keys ("A" ring) -----
    (0, "minor"): ("5A", "Cm"),    # C minor
    (1, "minor"): ("12A", "C#m"),  # C# minor
    (2, "minor"): ("7A", "Dm"),    # D minor
    (3, "minor"): ("2A", "D#m"),   # D# / Eb minor
    (4, "minor"): ("9A", "Em"),    # E minor
    (5, "minor"): ("4A", "Fm"),    # F minor
    (6, "minor"): ("11A", "F#m"),  # F# minor
    (7, "minor"): ("6A", "Gm"),    # G minor
    (8, "minor"): ("1A", "G#m"),   # G# / Ab minor
    (9, "minor"): ("8A", "Am"),    # A minor
    (10, "minor"): ("3A", "A#m"),  # A# / Bb minor
    (11, "minor"): ("10A", "Bm"),  # B minor
    # ----- major keys ("B" ring) -----
    (0, "major"): ("8B", "C"),     # C major
    (1, "major"): ("3B", "C#"),    # C# / Db major
    (2, "major"): ("10B", "D"),    # D major
    (3, "major"): ("5B", "D#"),    # D# / Eb major
    (4, "major"): ("12B", "E"),    # E major
    (5, "major"): ("7B", "F"),     # F major
    (6, "major"): ("2B", "F#"),    # F# / Gb major
    (7, "major"): ("9B", "G"),     # G major
    (8, "major"): ("4B", "G#"),    # G# / Ab major
    (9, "major"): ("11B", "A"),    # A major
    (10, "major"): ("6B", "A#"),   # A# / Bb major
    (11, "major"): ("1B", "B"),    # B major
}


@dataclass
class AnalysisResult:
    bpm: float
    bpm_corrected: bool
    pitch_class: int
    mode: str
    camelot: str
    tonality: str  # rekordbox-style, e.g. "Am" or "C"
    key_name: str  # human, e.g. "A minor"
    duration: float


def camelot_from_key(pitch_class: int, mode: str) -> str:
    """Camelot code (e.g. ``8A``) for a pitch class (0=C) and mode."""
    return CAMELOT_WHEEL[(pitch_class % 12, mode)][0]


def tonality_from_key(pitch_class: int, mode: str) -> str:
    """Rekordbox/standard key string (e.g. ``Am``, ``C``)."""
    return CAMELOT_WHEEL[(pitch_class % 12, mode)][1]


def key_name(pitch_class: int, mode: str) -> str:
    return f"{PITCH_NAMES[pitch_class % 12]} {mode}"


def compatible_camelot(camelot: str) -> list[str]:
    """Harmonically compatible keys per the Camelot wheel.

    Returns the key itself, its relative major/minor (same number, other
    letter), and ±1 on the same letter (energy boost / mix neighbours),
    wrapping 12 -> 1 and 1 -> 12.
    """
    m = re.fullmatch(r"(\d{1,2})([AB])", camelot.strip().upper())
    if not m:
        raise ValueError(f"Invalid Camelot code: {camelot!r}")
    number = int(m.group(1))
    letter = m.group(2)
    if not 1 <= number <= 12:
        raise ValueError(f"Camelot number out of range: {camelot!r}")
    other = "B" if letter == "A" else "A"
    up = number % 12 + 1
    down = (number - 2) % 12 + 1
    return [
        f"{number}{letter}",
        f"{number}{other}",
        f"{down}{letter}",
        f"{up}{letter}",
    ]


def detect_key_from_chroma(chroma_mean: np.ndarray) -> tuple[int, str, float]:
    """Correlate a 12-bin mean chroma vector against all 24 key profiles.

    Returns (pitch_class, mode, correlation_score) of the best match.
    """
    chroma = np.asarray(chroma_mean, dtype=float)
    if chroma.shape != (12,):
        raise ValueError("chroma_mean must be a 12-element vector")

    best: tuple[float, int, str] = (-2.0, 0, "major")
    for tonic in range(12):
        for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            # Rotate profile so index 0 aligns with this tonic.
            shifted = np.roll(profile, tonic)
            score = _pearson(chroma, shifted)
            if score > best[0]:
                best = (score, tonic, mode)
    return best[1], best[2], best[0]


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = np.sqrt((a * a).sum() * (b * b).sum())
    if denom == 0:
        return 0.0
    return float((a * b).sum() / denom)


def correct_bpm(raw_bpm: float) -> tuple[float, bool]:
    """Octave-error correction for tempo. Returns (bpm, was_corrected)."""
    bpm = float(raw_bpm)
    corrected = False
    # Loop in case of double octave errors (e.g. 45 -> 90 -> 180/2).
    while bpm < 90 and bpm > 0:
        bpm *= 2
        corrected = True
    while bpm > 160:
        bpm /= 2
        corrected = True
    return round(bpm, 2), corrected


def analyze_file(path: str, max_duration: float = 120.0) -> AnalysisResult:
    """Analyze BPM + key for an audio file. Loads only the first ~120s."""
    import librosa  # imported lazily — heavy and not needed for pure helpers

    y, sr = librosa.load(path, mono=True, duration=max_duration)
    if y.size == 0:
        raise ValueError("audio file is empty or unreadable")
    # True track length from the header (not the truncated load above).
    try:
        duration = float(librosa.get_duration(path=path))
    except Exception:
        duration = float(librosa.get_duration(y=y, sr=sr))

    # Harmonic component gives a cleaner chromagram for key detection.
    y_harmonic, _ = librosa.effects.hpss(y)
    chroma = librosa.feature.chroma_cqt(y=y_harmonic, sr=sr)
    chroma_mean = chroma.mean(axis=1)
    pitch_class, mode, _ = detect_key_from_chroma(chroma_mean)

    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    raw_bpm = float(np.atleast_1d(tempo)[0])
    bpm, corrected = correct_bpm(raw_bpm)

    return AnalysisResult(
        bpm=bpm,
        bpm_corrected=corrected,
        pitch_class=pitch_class,
        mode=mode,
        camelot=camelot_from_key(pitch_class, mode),
        tonality=tonality_from_key(pitch_class, mode),
        key_name=key_name(pitch_class, mode),
        duration=duration,
    )
