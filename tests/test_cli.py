"""CLI-layer smoke tests (no network, ffmpeg, or librosa).

We drive the Typer app with its runner against a temp DB + library, using
``scan --no-analyze`` so nothing touches audio analysis.
"""

import pytest
from typer.testing import CliRunner

from crate import cli, db
from crate.config import Config

runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "crate.db"
    lib = tmp_path / "lib"
    lib.mkdir()
    cfg = Config(library=str(lib), audio_format="aiff")
    monkeypatch.setattr(cli, "DB_PATH", db_path)
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    return {"db": db_path, "lib": lib, "cfg": cfg}


def _make_file(lib, name="Artist - Song (Remix).aiff"):
    f = lib / name
    f.write_bytes(b"\x00" * 16)
    return f


def test_scan_no_analyze_adds_with_existing_metadata(env):
    _make_file(env["lib"])
    result = runner.invoke(cli.app, ["scan", str(env["lib"]), "--no-analyze", "--crate", "Test"])
    assert result.exit_code == 0, result.output

    conn = db.connect(env["db"])
    rows = db.all_tracks(conn)
    assert len(rows) == 1
    assert rows[0]["artist"] == "Artist"
    assert rows[0]["title"] == "Song (Remix)"  # qualifier preserved
    assert rows[0]["bpm"] is None  # analysis skipped
    assert db.tracks_in_crate(conn, "Test") == [rows[0]["id"]]


def test_scan_genre_override(env):
    _make_file(env["lib"])
    result = runner.invoke(
        cli.app, ["scan", str(env["lib"]), "--no-analyze", "--genre", "Tech House"]
    )
    assert result.exit_code == 0, result.output
    conn = db.connect(env["db"])
    assert db.all_tracks(conn)[0]["genre"] == "Tech House"


def test_list_and_filters(env):
    _make_file(env["lib"])
    runner.invoke(cli.app, ["scan", str(env["lib"]), "--no-analyze"])
    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 0
    assert "Artist" in result.output

    # No tracks match an unused crate filter.
    empty = runner.invoke(cli.app, ["list", "--crate", "Nope"])
    assert "No tracks match." in empty.output


def test_rm_removes_track_keeps_file(env):
    f = _make_file(env["lib"])
    runner.invoke(cli.app, ["scan", str(env["lib"]), "--no-analyze"])
    result = runner.invoke(cli.app, ["rm", "1", "--yes"])
    assert result.exit_code == 0, result.output

    conn = db.connect(env["db"])
    assert db.all_tracks(conn) == []
    assert f.exists()  # file kept by default


def test_rm_delete_file(env):
    f = _make_file(env["lib"])
    runner.invoke(cli.app, ["scan", str(env["lib"]), "--no-analyze"])
    result = runner.invoke(cli.app, ["rm", "1", "--yes", "--delete-file"])
    assert result.exit_code == 0, result.output
    assert not f.exists()


def test_rm_missing_id_errors(env):
    result = runner.invoke(cli.app, ["rm", "999", "--yes"])
    assert result.exit_code == 1


def test_crate_management(env):
    _make_file(env["lib"])
    runner.invoke(cli.app, ["scan", str(env["lib"]), "--no-analyze"])
    runner.invoke(cli.app, ["crate", "assign", "1", "Peak Time"])
    result = runner.invoke(cli.app, ["crates"])
    assert result.exit_code == 0
    assert "Peak Time" in result.output


def test_export_writes_valid_xml(env, tmp_path):
    _make_file(env["lib"])
    runner.invoke(cli.app, ["scan", str(env["lib"]), "--no-analyze", "--crate", "Set"])
    out = tmp_path / "rb.xml"
    result = runner.invoke(cli.app, ["export", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    text = out.read_text()
    assert "<DJ_PLAYLISTS" in text
    assert 'Name="Set"' in text


def test_config_show(env):
    result = runner.invoke(cli.app, ["config", "--show"])
    assert result.exit_code == 0
    # (Rich wraps the long library path to terminal width, so assert on the
    # stable short fields rather than the full path.)
    assert "library" in result.output
    assert "aiff" in result.output
