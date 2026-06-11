"""CLI integration tests — every command, asserting exit codes AND output.

Network/ffmpeg-free: yt-dlp is mocked via conftest.mock_download and audio is
the generated fixtures. DB inspection uses the db_conn fixture (closed at
teardown) so no test leaks a connection.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from crate import cli, db
from crate.download import shutil as dl_shutil  # patched for the missing-ffmpeg test
from .conftest import mock_download

runner = CliRunner()


def full_output(result) -> str:
    """Combined stdout+stderr regardless of CliRunner's mix_stderr setting."""
    text = result.output or ""
    try:
        text += result.stderr or ""
    except (ValueError, AttributeError):
        pass
    return text


# ============================================================ config

def test_config_set_then_show(home, tmp_path):
    newlib = tmp_path / "newlib"
    r1 = runner.invoke(cli.app, ["config", "--library", str(newlib), "--format", "wav"])
    assert r1.exit_code == 0
    assert "Config saved" in r1.output

    r2 = runner.invoke(cli.app, ["config", "--show"])
    assert r2.exit_code == 0
    assert "wav" in r2.output  # the value we set round-trips to disk


def test_config_invalid_format(home):
    r = runner.invoke(cli.app, ["config", "--format", "ogg"])
    assert r.exit_code == 1
    assert "format must be one of" in full_output(r)


# ============================================================ doctor

def test_doctor_healthy(home, monkeypatch):
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    r = runner.invoke(cli.app, ["doctor"])
    assert r.exit_code == 0
    assert "ffmpeg found" in r.output
    assert "database OK" in r.output
    assert "no missing files" in r.output


def test_doctor_missing_ffmpeg(home, monkeypatch):
    monkeypatch.setattr(dl_shutil, "which", lambda name: None)
    r = runner.invoke(cli.app, ["doctor"])
    assert r.exit_code == 1
    assert "ffmpeg NOT found" in r.output


def test_doctor_prune_dead_file(home, monkeypatch, scan_dir, db_conn):
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    runner.invoke(cli.app, ["scan", str(scan_dir), "--no-analyze"])
    row = db.all_tracks(db_conn)[0]
    Path(row["path"]).unlink()  # make a dead DB entry

    r = runner.invoke(cli.app, ["doctor", "--prune"])
    assert r.exit_code == 0
    assert "Pruned" in r.output
    assert db.get_track(db_conn, row["id"]) is None


# ============================================================ scan + analyze

def test_scan_analyzes_and_populates(home, scan_dir, db_conn):
    r = runner.invoke(cli.app, ["scan", str(scan_dir)])
    assert r.exit_code == 0
    assert "Scan summary" in r.output

    rows = db.all_tracks(db_conn)
    assert len(rows) == 3
    assert {row["camelot"] for row in rows} == {"8A", "8B", "4A"}
    for row in rows:
        assert 118 <= row["bpm"] <= 132


def test_scan_skips_already_tracked(home, scan_dir, db_conn):
    runner.invoke(cli.app, ["scan", str(scan_dir), "--no-analyze"])
    r = runner.invoke(cli.app, ["scan", str(scan_dir), "--no-analyze"])
    assert r.exit_code == 0
    assert len(db.all_tracks(db_conn)) == 3  # no duplicates on second pass


def test_scan_no_analyze_keeps_qualifier_and_skips_bpm(home, scan_dir, db_conn):
    r = runner.invoke(cli.app, ["scan", str(scan_dir), "--no-analyze", "--crate", "Inbox"])
    assert r.exit_code == 0
    titles = {row["title"] for row in db.all_tracks(db_conn)}
    assert "A Minor (Original Mix)" in titles  # qualifier kept, junk stripped
    assert all(row["bpm"] is None for row in db.all_tracks(db_conn))  # analysis skipped
    assert db.tracks_in_crate(db_conn, "Inbox")


def test_scan_genre_override(home, scan_dir, db_conn):
    runner.invoke(cli.app, ["scan", str(scan_dir), "--no-analyze", "--genre", "Tech House"])
    assert all(row["genre"] == "Tech House" for row in db.all_tracks(db_conn))


def test_analyze_single_and_all(home, scan_dir, db_conn):
    runner.invoke(cli.app, ["scan", str(scan_dir), "--no-analyze"])
    r1 = runner.invoke(cli.app, ["analyze", "1"])
    assert r1.exit_code == 0
    assert "BPM" in r1.output
    assert db.get_track(db_conn, 1)["camelot"]

    r2 = runner.invoke(cli.app, ["analyze", "all"])
    assert r2.exit_code == 0
    assert all(row["camelot"] for row in db.all_tracks(db_conn))


def test_analyze_corrupt_file_exits_nonzero(home, tmp_path):
    folder = tmp_path / "bad"
    folder.mkdir()
    (folder / "Garbage - Track.aiff").write_bytes(b"not real audio" * 50)
    runner.invoke(cli.app, ["scan", str(folder), "--no-analyze"])
    r = runner.invoke(cli.app, ["analyze", "1"])
    assert r.exit_code == 1
    assert "failed" in full_output(r).lower()


def test_analyze_unknown_id(home):
    r = runner.invoke(cli.app, ["analyze", "999"])
    assert r.exit_code == 1
    assert "no track with id" in full_output(r)


def test_analyze_by_path(home, scan_dir, db_conn):
    runner.invoke(cli.app, ["scan", str(scan_dir), "--no-analyze"])
    path = db.get_track(db_conn, 1)["path"]
    r = runner.invoke(cli.app, ["analyze", path])
    assert r.exit_code == 0
    assert db.get_track(db_conn, 1)["camelot"]


# ============================================================ add (mocked download)

def test_add_url(home, monkeypatch, audio_fixtures, db_conn):
    mock_download(
        monkeypatch, audio_fixtures[0]["path"],
        title="A Minor (Original Mix)", artist="Alpha", source_url="https://youtu.be/aaa",
    )
    r = runner.invoke(cli.app, ["add", "https://youtu.be/aaa"])
    assert r.exit_code == 0
    assert "Added" in r.output
    assert "8A" in r.output  # analysis ran and reported the key

    rows = db.all_tracks(db_conn)
    assert len(rows) == 1
    assert rows[0]["source_url"] == "https://youtu.be/aaa"
    assert rows[0]["camelot"] == "8A"


def test_add_search_with_confirmation(home, monkeypatch, audio_fixtures):
    mock_download(
        monkeypatch, audio_fixtures[1]["path"],
        title="C Major (Extended Mix)", artist="Bravo", source_url="https://youtu.be/bbb",
        matched_title="Bravo - C Major (Extended Mix)",
    )
    r = runner.invoke(cli.app, ["add", "bravo c major"], input="y\n")
    assert r.exit_code == 0
    assert "Matched:" in r.output
    assert "Added" in r.output


def test_add_search_declined(home, monkeypatch, audio_fixtures, db_conn):
    mock_download(
        monkeypatch, audio_fixtures[1]["path"],
        title="C Major", artist="Bravo", source_url="https://youtu.be/bbb",
    )
    r = runner.invoke(cli.app, ["add", "bravo c major"], input="n\n")
    assert r.exit_code == 0
    assert "Added" not in r.output
    assert db.all_tracks(db_conn) == []


def test_add_search_yes_flag(home, monkeypatch, audio_fixtures):
    mock_download(
        monkeypatch, audio_fixtures[2]["path"],
        title="F Minor (Remix)", artist="Charlie", source_url="https://youtu.be/ccc",
    )
    r = runner.invoke(cli.app, ["add", "charlie f minor", "--yes"])
    assert r.exit_code == 0
    assert "Added" in r.output


def test_add_with_crate_and_genre(home, monkeypatch, audio_fixtures, db_conn):
    mock_download(
        monkeypatch, audio_fixtures[0]["path"],
        title="A Minor (Original Mix)", artist="Alpha", source_url="https://youtu.be/aaa",
    )
    r = runner.invoke(
        cli.app, ["add", "https://youtu.be/aaa", "--crate", "Peak Time", "--genre", "Techno"]
    )
    assert r.exit_code == 0
    track = db.all_tracks(db_conn)[0]
    assert track["genre"] == "Techno"
    assert db.tracks_in_crate(db_conn, "Peak Time") == [track["id"]]


def test_add_dedupe_by_source_url(home, monkeypatch, audio_fixtures, db_conn):
    mock_download(
        monkeypatch, audio_fixtures[0]["path"],
        title="A Minor (Original Mix)", artist="Alpha", source_url="https://youtu.be/aaa",
    )
    assert runner.invoke(cli.app, ["add", "https://youtu.be/aaa"]).exit_code == 0

    r2 = runner.invoke(cli.app, ["add", "https://youtu.be/aaa"])
    assert r2.exit_code == 1
    assert "already in library" in full_output(r2)
    assert len(db.all_tracks(db_conn)) == 1

    r3 = runner.invoke(cli.app, ["add", "https://youtu.be/aaa", "--force"])
    assert r3.exit_code == 0
    assert len(db.all_tracks(db_conn)) == 1  # re-download, still one row


def test_add_invalid_url(home, monkeypatch):
    from crate.download import DownloadError

    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "probe", lambda target, *, search: (_ for _ in ()).throw(
        DownloadError("Unsupported URL")))
    r = runner.invoke(cli.app, ["add", "https://example.com/not-a-video"])
    assert r.exit_code == 1
    assert "could not resolve" in full_output(r)
    assert "Traceback" not in full_output(r)


def test_add_missing_ffmpeg(home, monkeypatch):
    monkeypatch.setattr(dl_shutil, "which", lambda name: None)
    r = runner.invoke(cli.app, ["add", "https://youtu.be/aaa"])
    assert r.exit_code == 1
    assert "ffmpeg not found" in full_output(r)


# ============================================================ list + filters

@pytest.fixture
def populated(home, scan_dir):
    runner.invoke(cli.app, ["scan", str(scan_dir)])
    return home


def test_list_all(populated):
    r = runner.invoke(cli.app, ["list"])
    assert r.exit_code == 0
    for name in ("Alpha", "Bravo", "Charlie"):
        assert name in r.output


def test_list_key_filter(populated):
    r = runner.invoke(cli.app, ["list", "--key", "8A"])
    assert r.exit_code == 0
    assert "Alpha" in r.output
    assert "Bravo" not in r.output


def test_list_compatible_filter(populated):
    # 8A ~ {8A, 8B, 7A, 9A} → Alpha(8A) + Bravo(8B); not Charlie(4A).
    r = runner.invoke(cli.app, ["list", "--compatible", "8A"])
    assert r.exit_code == 0
    assert "Alpha" in r.output
    assert "Bravo" in r.output
    assert "Charlie" not in r.output


def test_list_bpm_range(populated):
    # Alpha/Charlie ~123 in range; Bravo ~129 out.
    r = runner.invoke(cli.app, ["list", "--bpm", "120-126"])
    assert r.exit_code == 0
    assert "Charlie" in r.output
    assert "Bravo" not in r.output


def test_list_sort_bpm_orders_rows(populated):
    r = runner.invoke(cli.app, ["list", "--sort", "bpm"])
    assert r.exit_code == 0
    # Bravo (~129) sorts after the ~123 tracks; check its Camelot appears later.
    assert r.output.index("8A") < r.output.index("8B")


def test_list_bad_bpm_range(populated):
    r = runner.invoke(cli.app, ["list", "--bpm", "fast"])
    assert r.exit_code == 1
    assert "invalid --bpm range" in full_output(r)


def test_list_bad_compatible(populated):
    r = runner.invoke(cli.app, ["list", "--compatible", "99Z"])
    assert r.exit_code == 1


def test_list_crate_filter(populated):
    runner.invoke(cli.app, ["crate", "assign", "1", "MySet"])
    r = runner.invoke(cli.app, ["list", "--crate", "MySet"])
    assert r.exit_code == 0
    assert "Alpha" in r.output
    assert "Charlie" not in r.output


def test_list_renders_multiple_crates(populated, monkeypatch):
    # A track in two crates should show both names in the Crates column.
    monkeypatch.setenv("COLUMNS", "200")  # avoid rich truncating the wide row
    runner.invoke(cli.app, ["crate", "assign", "1", "Warmup"])
    runner.invoke(cli.app, ["crate", "assign", "1", "PeakTime"])
    r = runner.invoke(cli.app, ["list"])
    assert r.exit_code == 0
    assert "Warmup" in r.output
    assert "PeakTime" in r.output


def test_list_empty(home):
    r = runner.invoke(cli.app, ["list"])
    assert r.exit_code == 0
    assert "No tracks match" in r.output


# ============================================================ crates

def test_crate_lifecycle(populated, db_conn):
    assert runner.invoke(cli.app, ["crate", "add", "Warmup"]).exit_code == 0
    assert runner.invoke(cli.app, ["crate", "assign", "1", "Warmup"]).exit_code == 0

    listing = runner.invoke(cli.app, ["crates"])
    assert listing.exit_code == 0
    assert "Warmup" in listing.output

    rm = runner.invoke(cli.app, ["crate", "rm", "Warmup"])
    assert rm.exit_code == 0
    assert db.get_track(db_conn, 1) is not None  # track survives crate removal


def test_crate_assign_unknown_track(home):
    r = runner.invoke(cli.app, ["crate", "assign", "42", "Whatever"])
    assert r.exit_code == 1
    assert "no track with id" in full_output(r)


def test_crate_rm_unknown(home):
    r = runner.invoke(cli.app, ["crate", "rm", "Ghost"])
    assert r.exit_code == 1
    assert "no crate named" in full_output(r)


def test_crates_empty(home):
    r = runner.invoke(cli.app, ["crates"])
    assert r.exit_code == 0
    assert "No crates yet" in r.output


# ============================================================ rm

def test_rm_keeps_file(populated, db_conn):
    path = Path(db.get_track(db_conn, 1)["path"])
    r = runner.invoke(cli.app, ["rm", "1", "--yes"])
    assert r.exit_code == 0
    assert db.get_track(db_conn, 1) is None
    assert path.exists()  # file kept by default


def test_rm_delete_file(populated, db_conn):
    path = Path(db.get_track(db_conn, 1)["path"])
    r = runner.invoke(cli.app, ["rm", "1", "--yes", "--delete-file"])
    assert r.exit_code == 0
    assert not path.exists()


def test_rm_unknown(home):
    r = runner.invoke(cli.app, ["rm", "5", "--yes"])
    assert r.exit_code == 1
    assert "no track with id" in full_output(r)


# ============================================================ export

def test_export_writes_xml(populated, tmp_path):
    out = tmp_path / "rb.xml"
    runner.invoke(cli.app, ["crate", "assign", "1", "Set A"])
    r = runner.invoke(cli.app, ["export", "--out", str(out)])
    assert r.exit_code == 0
    assert out.exists()
    text = out.read_text()
    assert "<DJ_PLAYLISTS" in text
    assert 'Name="Set A"' in text
    assert "Import into Rekordbox" in r.output


def test_export_empty_library_errors(home, tmp_path):
    out = tmp_path / "rb.xml"
    r = runner.invoke(cli.app, ["export", "--out", str(out)])
    assert r.exit_code == 1
    assert "library is empty" in full_output(r)
    assert not out.exists()  # nothing written


# ============================================================ error paths

def test_scan_nonexistent_folder(home):
    r = runner.invoke(cli.app, ["scan", "/no/such/folder/anywhere"])
    assert r.exit_code == 1
    assert "not a directory" in full_output(r)
    assert "Traceback" not in full_output(r)
