"""Golden-file test for rekordbox.py.

The generated XML must match a committed snapshot. Determinism is engineered in:
inputs use fixed absolute paths (under /music, which is not a symlink, so
Path.resolve() is a stable no-op across machines) and a fixed DateAdded, so no
runtime/host state leaks. Attribute and playlist ordering are insertion order
(stable in CPython 3.8+); newline differences are normalized by comparing text
read with universal newlines.

If you intentionally change the XML format, regenerate the snapshot:
    python -m tests.test_rekordbox_golden
and review the diff before committing.
"""

from __future__ import annotations

from pathlib import Path

from crate.rekordbox import generate_xml

GOLDEN = Path(__file__).parent / "golden" / "collection.xml"

TRACKS = [
    {
        "id": 1,
        "path": "/music/Alpha - A Minor (Original Mix).aiff",
        "title": "A Minor (Original Mix)",
        "artist": "Alpha",
        "genre": "Techno",
        "bpm": 124.0,
        "duration": 210.0,
        "size": 1000,
        "date_added": "2026-01-01",
        "tonality": "Am",
        "first_beat": 0.5,
    },
    {
        "id": 2,
        "path": "/music/Bravo - Don't Stop.aiff",  # apostrophe → %27 in Location
        "title": "Don't Stop",
        "artist": "Bravo",
        "genre": "House",
        "bpm": 128.0,
        "duration": 200.0,
        "size": 2048,
        "date_added": "2026-01-01",
        "tonality": "C",
        "first_beat": 0.25,
    },
]
PLAYLISTS = {"Set 1": [1, 2], "Set 2": [2]}


def render() -> str:
    return generate_xml(TRACKS, PLAYLISTS)


def test_matches_golden():
    assert GOLDEN.exists(), "golden missing — run this module as a script to create it"
    assert render() == GOLDEN.read_text(encoding="utf-8")


if __name__ == "__main__":  # regenerate the snapshot
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(render(), encoding="utf-8")
    print(f"wrote {GOLDEN}")
