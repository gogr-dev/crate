"""DB insert / dedupe / crate assignment."""

import sqlite3

import pytest

from crate import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


def _add(conn, tmp_path, name="a", **kw):
    f = tmp_path / f"{name}.aiff"
    f.write_bytes(b"\x00")
    return db.add_track(conn, path=str(f), **kw)


def test_insert_and_get(conn, tmp_path):
    tid = _add(conn, tmp_path, name="track1", title="T", artist="A", bpm=128.0)
    row = db.get_track(conn, tid)
    assert row["title"] == "T"
    assert row["artist"] == "A"
    assert row["bpm"] == 128.0
    assert row["date_added"]  # auto-stamped


def test_dedupe_by_path(conn, tmp_path):
    f = tmp_path / "dupe.aiff"
    f.write_bytes(b"\x00")
    db.add_track(conn, path=str(f))
    with pytest.raises(sqlite3.IntegrityError):
        db.add_track(conn, path=str(f))


def test_get_by_path_and_url(conn, tmp_path):
    tid = _add(conn, tmp_path, name="byurl", source_url="https://x/y")
    assert db.get_by_url(conn, "https://x/y")["id"] == tid
    assert db.get_by_url(conn, "https://nope") is None


def test_update_analysis(conn, tmp_path):
    tid = _add(conn, tmp_path, name="an")
    db.update_analysis(conn, tid, bpm=126.0, key_name="A minor", tonality="Am", camelot="8A")
    row = db.get_track(conn, tid)
    assert row["bpm"] == 126.0
    assert row["camelot"] == "8A"


def test_crate_assignment_and_multi(conn, tmp_path):
    tid = _add(conn, tmp_path, name="c1")
    db.assign_track(conn, tid, "House")
    db.assign_track(conn, tid, "Peak Time")
    # Idempotent re-assign.
    db.assign_track(conn, tid, "House")
    assert db.crates_for_track(conn, tid) == ["House", "Peak Time"]
    assert db.tracks_in_crate(conn, "House") == [tid]


def test_crate_counts_and_remove(conn, tmp_path):
    t1 = _add(conn, tmp_path, name="x1")
    t2 = _add(conn, tmp_path, name="x2")
    db.assign_track(conn, t1, "Set")
    db.assign_track(conn, t2, "Set")
    counts = {r["name"]: r["count"] for r in db.list_crates(conn)}
    assert counts["Set"] == 2

    assert db.remove_crate(conn, "Set") is True
    assert db.remove_crate(conn, "Set") is False
    # Tracks survive; assignment gone.
    assert db.get_track(conn, t1) is not None
    assert db.crates_for_track(conn, t1) == []


def test_filter_by_bpm_and_camelot(conn, tmp_path):
    a = _add(conn, tmp_path, name="f1", bpm=124.0, camelot="8A")
    b = _add(conn, tmp_path, name="f2", bpm=128.0, camelot="9A")
    _add(conn, tmp_path, name="f3", bpm=140.0, camelot="8A")

    by_bpm = db.filter_tracks(conn, bpm_range=(120.0, 130.0))
    assert {r["id"] for r in by_bpm} == {a, b}

    by_key = db.filter_tracks(conn, camelot_in=["8A"])
    assert a in {r["id"] for r in by_key}
    assert b not in {r["id"] for r in by_key}


def test_filter_by_crate(conn, tmp_path):
    a = _add(conn, tmp_path, name="g1")
    _add(conn, tmp_path, name="g2")
    db.assign_track(conn, a, "Only")
    rows = db.filter_tracks(conn, crate="Only")
    assert [r["id"] for r in rows] == [a]
