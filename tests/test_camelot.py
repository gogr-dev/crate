"""Full Camelot wheel mapping + compatible-key logic."""

import numpy as np
import pytest

from crate.analyze import (
    CAMELOT_WHEEL,
    camelot_from_key,
    compatible_camelot,
    correct_bpm,
    detect_key_from_chroma,
    tonality_from_key,
)

# Spec-anchored spot checks.
SPOT_CHECKS = {
    (9, "minor"): ("8A", "Am"),   # A minor
    (0, "major"): ("8B", "C"),    # C major
    (4, "minor"): ("9A", "Em"),   # E minor
    (7, "major"): ("9B", "G"),    # G major
}


@pytest.mark.parametrize("kv, expected", SPOT_CHECKS.items())
def test_spec_anchor_keys(kv, expected):
    pc, mode = kv
    assert camelot_from_key(pc, mode) == expected[0]
    assert tonality_from_key(pc, mode) == expected[1]


def test_wheel_is_complete_and_unique():
    assert len(CAMELOT_WHEEL) == 24
    codes = [v[0] for v in CAMELOT_WHEEL.values()]
    assert len(set(codes)) == 24  # every code distinct
    # All numbers 1..12 appear for both A and B rings.
    expected = {f"{n}{ring}" for n in range(1, 13) for ring in "AB"}
    assert set(codes) == expected


def test_minor_is_A_major_is_B():
    for (pc, mode), (code, _) in CAMELOT_WHEEL.items():
        assert code.endswith("A" if mode == "minor" else "B")


def test_relative_keys_share_number():
    # A minor (8A) and C major (8B) are relative → same number.
    assert camelot_from_key(9, "minor")[:-1] == camelot_from_key(0, "major")[:-1]


def test_compatible_camelot_8A():
    assert set(compatible_camelot("8A")) == {"8A", "8B", "7A", "9A"}


def test_compatible_camelot_wraps():
    assert set(compatible_camelot("12B")) == {"12B", "12A", "11B", "1B"}
    assert set(compatible_camelot("1A")) == {"1A", "1B", "12A", "2A"}


def test_compatible_camelot_invalid():
    with pytest.raises(ValueError):
        compatible_camelot("13A")
    with pytest.raises(ValueError):
        compatible_camelot("nope")


def test_correct_bpm():
    assert correct_bpm(124.0) == (124.0, False)
    assert correct_bpm(64.0) == (128.0, True)   # doubled
    assert correct_bpm(180.0) == (90.0, True)   # halved
    assert correct_bpm(45.0) == (90.0, True)    # doubled once into range


def test_detect_key_from_chroma_picks_profile_peak():
    # A chroma that is essentially the C-major profile should detect C major.
    from crate.analyze import MAJOR_PROFILE

    pc, mode, score = detect_key_from_chroma(np.array(MAJOR_PROFILE))
    assert (pc, mode) == (0, "major")
    assert score > 0.9


def test_detect_key_rejects_bad_shape():
    with pytest.raises(ValueError):
        detect_key_from_chroma(np.zeros(11))
