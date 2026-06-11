"""Rekordbox XML generation.

The tricky part is the ``Location`` attribute: Rekordbox wants a
``file://localhost/`` URL with the absolute path percent-encoded (path
separators preserved). Spaces, apostrophes, etc. must be encoded.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

_KIND = {
    ".aiff": "AIFF File",
    ".aif": "AIFF File",
    ".wav": "WAV File",
    ".mp3": "MP3 File",
    ".flac": "FLAC File",
    ".m4a": "M4A File",
    ".mp4": "M4A File",
}


def location_url(path: str | Path) -> str:
    """Build the ``file://localhost/...`` URL Rekordbox expects."""
    abs_path = str(Path(path).expanduser().resolve())
    # quote with safe="/" so separators survive but spaces/apostrophes encode.
    return "file://localhost" + quote(abs_path, safe="/")


def kind_for(path: str | Path) -> str:
    return _KIND.get(Path(path).suffix.lower(), "Unknown")


def _get(track: Mapping[str, Any], key: str, default: Any = "") -> Any:
    try:
        val = track[key]
    except (KeyError, IndexError):
        return default
    return default if val is None else val


def _track_element(track: Mapping[str, Any]) -> ET.Element:
    path = _get(track, "path")
    bpm = float(_get(track, "bpm", 0) or 0)
    duration = int(round(float(_get(track, "duration", 0) or 0)))
    size = int(_get(track, "size", 0) or 0)

    el = ET.Element(
        "TRACK",
        {
            "TrackID": str(_get(track, "id")),
            "Name": str(_get(track, "title")),
            "Artist": str(_get(track, "artist")),
            "Genre": str(_get(track, "genre")),
            "Kind": kind_for(path),
            "Size": str(size),
            "TotalTime": str(duration),
            "AverageBpm": f"{bpm:.2f}",
            "DateAdded": str(_get(track, "date_added")),
            "Tonality": str(_get(track, "tonality")),
            "Location": location_url(path),
        },
    )
    # Beatgrid anchor.
    ET.SubElement(
        el,
        "TEMPO",
        {"Inizio": "0.0", "Bpm": f"{bpm:.2f}", "Metro": "4/4", "Battito": "1"},
    )
    return el


def generate_xml(
    tracks: Sequence[Mapping[str, Any]],
    playlists: Mapping[str, Sequence[int]] | None = None,
) -> str:
    """Return a complete Rekordbox XML document as a string.

    ``tracks`` is a sequence of mappings (sqlite rows or dicts).
    ``playlists`` maps a crate name to a list of TrackIDs.
    """
    playlists = playlists or {}

    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(
        root,
        "PRODUCT",
        {"Name": "rekordbox", "Version": "6.0.0", "Company": "AlphaTheta"},
    )

    collection = ET.SubElement(root, "COLLECTION", {"Entries": str(len(tracks))})
    for track in tracks:
        collection.append(_track_element(track))

    playlists_el = ET.SubElement(root, "PLAYLISTS")
    root_node = ET.SubElement(
        playlists_el,
        "NODE",
        {"Type": "0", "Name": "ROOT", "Count": str(len(playlists))},
    )
    for name, track_ids in playlists.items():
        node = ET.SubElement(
            root_node,
            "NODE",
            {
                "Name": name,
                "Type": "1",
                "KeyType": "0",
                "Entries": str(len(track_ids)),
            },
        )
        for tid in track_ids:
            ET.SubElement(node, "TRACK", {"Key": str(tid)})

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"
