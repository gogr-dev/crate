"""Shared fixtures: generated audio + isolated config/DB per test.

The whole suite is network-free and ffmpeg-free: audio is synthesized with
numpy/soundfile and read back by librosa via libsndfile (plain-PCM AIFF needs no
ffmpeg), and yt-dlp is mocked at the cli.download_audio boundary.
"""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from crate import cli, db
from crate import config as cfgmod
from crate.config import Config, save_config

SR = 22050


def _make_audio(path, root_hz, triad_hz, bpm, dur=4.0) -> None:
    """Synthesize a short clip: an emphasized-root triad + a four-on-the-floor
    click at ``bpm`` so both key and tempo are detectable."""
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    tone = 0.6 * np.sin(2 * np.pi * root_hz * t)
    for f in triad_hz:
        tone += 0.4 * np.sin(2 * np.pi * f * t)
    tone /= 1.6
    click = np.zeros_like(t)
    period = 60.0 / bpm
    n = 0
    while n * period < dur:
        i = int(n * period * SR)
        click[i : i + 150] = 1.0
        n += 1
    y = (0.6 * tone + 0.4 * click).astype("float32")
    sf.write(str(path), y, SR)


# Three distinct, deterministic fixtures. Each triad independently fixes the
# key, so asserting on the detected Camelot tests real detection (not whatever
# the code happens to emit).
_SPECS = [
    {
        "name": "Alpha - A Minor (Original Mix).aiff",
        "root": 220.00, "triad": [261.63, 329.63], "bpm": 124,
        "camelot": "8A", "tonality": "Am", "key": "A minor",
    },
    {
        "name": "Bravo - C Major (Extended Mix).aiff",
        "root": 261.63, "triad": [329.63, 392.00], "bpm": 128,
        "camelot": "8B", "tonality": "C", "key": "C major",
    },
    {
        "name": "Charlie - F Minor (Remix).aiff",
        "root": 174.61, "triad": [207.65, 261.63], "bpm": 122,
        "camelot": "4A", "tonality": "Fm", "key": "F minor",
    },
]


@pytest.fixture(scope="session")
def audio_fixtures(tmp_path_factory):
    """Three generated AIFFs (generated once per session). Read-only — tests
    that tag/mutate must copy them first."""
    d = tmp_path_factory.mktemp("audio")
    out = []
    for spec in _SPECS:
        path = d / spec["name"]
        _make_audio(path, spec["root"], spec["triad"], spec["bpm"])
        out.append({**spec, "path": path})
    return out


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate config + DB under a per-test temp home; seed a config pointing
    the library at a temp folder so commands never touch the real ~/.crate."""
    crate_home = tmp_path / ".crate"
    crate_home.mkdir()
    lib = tmp_path / "library"
    lib.mkdir()
    cfg_path = crate_home / "config.toml"
    db_path = crate_home / "crate.db"

    monkeypatch.setattr(cfgmod, "CRATE_HOME", crate_home)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(cfgmod, "DB_PATH", db_path)
    monkeypatch.setattr(cli, "DB_PATH", db_path)

    save_config(Config(library=str(lib)))
    return SimpleNamespace(home=crate_home, lib=lib, db=db_path, config_path=cfg_path)


@pytest.fixture
def db_conn(home):
    """A connection to the isolated DB, closed at teardown (no ResourceWarning)."""
    conn = db.connect(home.db)
    yield conn
    conn.close()


@pytest.fixture
def scan_dir(tmp_path, audio_fixtures):
    """A folder containing fresh copies of all three fixtures (safe to tag)."""
    d = tmp_path / "incoming"
    d.mkdir()
    for fx in audio_fixtures:
        shutil.copyfile(fx["path"], d / fx["name"])
    return d


def mock_download(monkeypatch, audio_src, *, title, artist, source_url,
                  genre="", duration=200.0, matched_title=None):
    """Patch the yt-dlp boundary: probe() returns canned info and
    download_audio() copies a real fixture into the library."""
    from crate.download import DownloadResult, sanitize_filename

    def fake_probe(target, *, search):
        return {
            "title": matched_title or f"{artist} - {title}",
            "webpage_url": source_url,
            "duration": duration,
        }

    def fake_download(target, library, *, audio_format="aiff", search=False,
                      artist_override=None, title_override=None, info=None):
        from pathlib import Path

        a = artist_override or artist
        t = title_override or title
        dest = Path(library) / f"{sanitize_filename(f'{a} - {t}')}.aiff"
        shutil.copyfile(audio_src, dest)
        return DownloadResult(
            filepath=dest, title=t, artist=a, source_url=source_url,
            genre=genre, duration=duration,
        )

    monkeypatch.setattr(cli, "probe", fake_probe)
    monkeypatch.setattr(cli, "download_audio", fake_download)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
