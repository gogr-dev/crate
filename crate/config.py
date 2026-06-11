"""Config load/save. Stored as TOML at ~/.crate/config.toml.

Uses the stdlib ``tomllib`` for reading (Python 3.11+) and a tiny hand-rolled
writer so we don't need a third-party TOML-writing dependency.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

CRATE_HOME = Path.home() / ".crate"
CONFIG_PATH = CRATE_HOME / "config.toml"
DB_PATH = CRATE_HOME / "crate.db"

DEFAULT_LIBRARY = Path.home() / "Music" / "crate"
VALID_FORMATS = ("aiff", "wav")


@dataclass
class Config:
    library: str = str(DEFAULT_LIBRARY)
    audio_format: str = "aiff"
    default_crate: str = ""

    @property
    def library_path(self) -> Path:
        return Path(self.library).expanduser()


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def load_config() -> Config:
    """Load config, falling back to defaults for any missing keys."""
    if not CONFIG_PATH.exists():
        return Config()
    try:
        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return Config()
    cfg = Config()
    if isinstance(data.get("library"), str):
        cfg.library = data["library"]
    if data.get("audio_format") in VALID_FORMATS:
        cfg.audio_format = data["audio_format"]
    if isinstance(data.get("default_crate"), str):
        cfg.default_crate = data["default_crate"]
    return cfg


def save_config(cfg: Config) -> None:
    CRATE_HOME.mkdir(parents=True, exist_ok=True)
    lines = [f"{key} = {_quote(str(val))}" for key, val in asdict(cfg).items()]
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
