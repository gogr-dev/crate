"""Full end-to-end pipeline: scan generated audio → crates → export → verify.

Confirms real analysis output (BPM/key) reaches the DB, the on-disk tags, and
the exported Rekordbox XML, and that playlist nodes reference the right
TrackIDs. The fixtures' keys are fixed by construction, so the Camelot
assertions catch a real key-detection regression.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

from typer.testing import CliRunner

from crate import cli, db

runner = CliRunner()

EXPECTED = {
    "8A": {"tonality": "Am", "bpm_target": 124, "artist": "Alpha"},
    "8B": {"tonality": "C", "bpm_target": 128, "artist": "Bravo"},
    "4A": {"tonality": "Fm", "bpm_target": 122, "artist": "Charlie"},
}


def _read_comment(path: str) -> str:
    from mutagen.aiff import AIFF

    return str(AIFF(path).tags.getall("COMM")[0].text[0])


def test_full_pipeline(home, scan_dir, tmp_path, db_conn):
    # 1. Scan → analyze + tag + insert all three.
    r = runner.invoke(cli.app, ["scan", str(scan_dir)])
    assert r.exit_code == 0, r.output

    rows = {row["camelot"]: row for row in db.all_tracks(db_conn)}
    assert set(rows) == {"8A", "8B", "4A"}

    # 2. Per-track correctness: DB + on-disk Camelot-in-comment tag.
    for camelot, exp in EXPECTED.items():
        row = rows[camelot]
        assert row["tonality"] == exp["tonality"]
        assert abs(row["bpm"] - exp["bpm_target"]) <= 4
        assert row["artist"] == exp["artist"]
        assert _read_comment(row["path"]) == camelot  # tag written to the file itself

    # 3. Assign to two crates.
    id_8a, id_8b, id_4a = rows["8A"]["id"], rows["8B"]["id"], rows["4A"]["id"]
    runner.invoke(cli.app, ["crate", "assign", str(id_8a), "Set 1"])
    runner.invoke(cli.app, ["crate", "assign", str(id_8b), "Set 1"])
    runner.invoke(cli.app, ["crate", "assign", str(id_4a), "Set 2"])

    # 4. Export and parse.
    out = tmp_path / "rb.xml"
    assert runner.invoke(cli.app, ["export", "--out", str(out)]).exit_code == 0
    root = ET.fromstring(out.read_text())

    collection = root.find("COLLECTION")
    assert collection.attrib["Entries"] == "3"
    by_id = {t.attrib["TrackID"]: t for t in collection.findall("TRACK")}
    for camelot, exp in EXPECTED.items():
        t = by_id[str(rows[camelot]["id"])]
        assert t.attrib["Tonality"] == exp["tonality"]
        assert abs(float(t.attrib["AverageBpm"]) - exp["bpm_target"]) <= 4
        assert 3 <= int(t.attrib["TotalTime"]) <= 5  # ~4s fixtures
        loc = t.attrib["Location"]
        assert loc.startswith("file://localhost/")
        assert " " not in loc and "%20" in loc
        assert unquote(loc.replace("file://localhost", "")) == str(Path(rows[camelot]["path"]).resolve())
        assert float(t.find("TEMPO").attrib["Inizio"]) > 0.0  # real first-beat anchor

    # 5. Playlist nodes reference the right TrackIDs.
    nodes = {n.attrib["Name"]: n for n in root.findall("PLAYLISTS/NODE/NODE")}
    assert set(nodes) == {"Set 1", "Set 2"}
    assert {ref.attrib["Key"] for ref in nodes["Set 1"].findall("TRACK")} == {str(id_8a), str(id_8b)}
    assert {ref.attrib["Key"] for ref in nodes["Set 2"].findall("TRACK")} == {str(id_4a)}
