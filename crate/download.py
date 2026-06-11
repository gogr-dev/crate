"""yt-dlp download wrapper plus video-title parsing helpers.

The parsing helpers (``parse_title``, ``clean_title``, ``sanitize_filename``,
``is_url``) are pure and unit tested. The network-touching ``download_audio``
is kept thin and is not exercised by the test suite.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Parenthesised qualifiers we want to KEEP in a title.
_KEEP = re.compile(
    r"\b(extended|original|radio|club|mix|remix|edit|version|vip|bootleg|"
    r"rework|instrumental|dub|flip|refix|re-?edit|remaster|acoustic|feat\.?|ft\.?)\b",
    re.IGNORECASE,
)
# Junk we want to STRIP.
_JUNK = re.compile(
    r"\b(official|video|audio|lyrics?|visuali[sz]er|hd|hq|4k|8k|"
    r"free\s+download|out\s+now|premiere|full\s+album|stream|m/?v|"
    r"music\s+video|color\s+coded)\b",
    re.IGNORECASE,
)

_ILLEGAL_FS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


class DownloadError(Exception):
    """Raised for any expected download/conversion failure."""


@dataclass
class DownloadResult:
    filepath: Path
    title: str
    artist: str
    source_url: str
    genre: str
    duration: float


def is_url(text: str) -> bool:
    return bool(re.match(r"https?://", text.strip(), re.IGNORECASE))


def _has_keep(text: str) -> bool:
    return bool(_KEEP.search(text))


def _has_junk(text: str) -> bool:
    return bool(_JUNK.search(text))


def clean_title(title: str) -> str:
    """Strip noise from a video title while preserving meaningful qualifiers."""
    title = title.strip()

    # Pipe segments: drop junk segments, keep meaningful ones as "(...)".
    if "|" in title:
        head, *tail = title.split("|")
        title = head.strip()
        for seg in tail:
            seg = seg.strip().strip("()[]").strip()
            if seg and _has_keep(seg) and not _has_junk(seg):
                title = f"{title} ({seg})"

    # Square-bracket groups are almost always junk → remove.
    title = re.sub(r"\[[^\]]*\]", " ", title)

    # Parenthesised groups: keep meaningful, drop junk, keep unknowns.
    def _paren(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        if _has_keep(inner):
            return f"({inner})"
        if _has_junk(inner):
            return " "
        return f"({inner})"

    title = re.sub(r"\(([^)]*)\)", _paren, title)

    title = re.sub(r"\s+", " ", title).strip()
    title = title.strip(" -–—|")
    return re.sub(r"\s+", " ", title).strip()


def clean_artist(artist: str) -> str:
    artist = re.sub(r"\[[^\]]*\]", " ", artist)
    artist = re.sub(r"\s+", " ", artist).strip()
    return artist.strip(" -–—|")


def parse_title(raw_title: str, uploader: str | None = None) -> tuple[str, str]:
    """Best-effort (artist, title) from a video title.

    Splits on the first ``" - "``. If there's no separator, the cleaned title
    is used as the title and ``uploader`` (if given) as the artist.
    """
    raw_title = (raw_title or "").strip()
    if " - " in raw_title:
        artist_part, title_part = raw_title.split(" - ", 1)
        artist = clean_artist(artist_part)
        title = clean_title(title_part)
        if not title:  # everything got stripped — fall back to the raw remainder
            title = clean_artist(title_part)
        return artist, title

    title = clean_title(raw_title)
    artist = clean_artist(uploader or "")
    return artist, title


def sanitize_filename(name: str) -> str:
    """Make a string safe to use as a filename component."""
    name = _ILLEGAL_FS.sub("", name)
    name = name.replace("/", "-").strip()
    name = re.sub(r"\s+", " ", name)
    name = name.strip(". ")
    return name or "untitled"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _extract_info(target: str, *, search: bool) -> dict:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError as YtDownloadError

    query = f"ytsearch1:{target}" if search else target
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
    except YtDownloadError as exc:
        raise DownloadError(str(exc)) from exc
    if info is None:
        raise DownloadError("no results found")
    if "entries" in info:  # search result
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise DownloadError("no results found")
        info = entries[0]
    return info


def probe(target: str, *, search: bool) -> dict:
    """Return metadata for a URL/query without downloading (for confirmation)."""
    return _extract_info(target, search=search)


def _convert(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(src), "-map_metadata", "-1", str(dest)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not dest.exists():
        tail = result.stderr.strip().splitlines()[-1:] or ["ffmpeg failed"]
        raise DownloadError(f"ffmpeg conversion failed: {tail[0]}")


def download_audio(
    target: str,
    library: Path,
    *,
    audio_format: str = "aiff",
    search: bool = False,
    artist_override: str | None = None,
    title_override: str | None = None,
    info: dict | None = None,
) -> DownloadResult:
    """Download bestaudio and convert to the target format under ``library``.

    ``info`` may be a pre-fetched metadata dict (from :func:`probe`) to avoid a
    second network round-trip.
    """
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError as YtDownloadError

    if not ffmpeg_available():
        raise DownloadError("ffmpeg not found on PATH")

    if info is None:
        info = _extract_info(target, search=search)

    webpage_url = info.get("webpage_url") or info.get("original_url") or target
    raw_title = info.get("title", "")
    uploader = info.get("uploader") or info.get("channel")
    artist, title = parse_title(raw_title, uploader)
    if artist_override:
        artist = artist_override
    if title_override:
        title = title_override
    if not artist:
        artist = "Unknown Artist"
    if not title:
        title = "Unknown Title"
    genre = info.get("genre") or ""

    library = Path(library).expanduser()
    library.mkdir(parents=True, exist_ok=True)
    tmp_tmpl = str(library / "_crate_tmp_%(id)s.%(ext)s")

    opts = {
        "format": "bestaudio/best",
        "outtmpl": tmp_tmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    try:
        with YoutubeDL(opts) as ydl:
            dl_info = ydl.extract_info(webpage_url, download=True)
            src = Path(ydl.prepare_filename(dl_info))
    except YtDownloadError as exc:
        raise DownloadError(str(exc)) from exc

    if not src.exists():
        # yt-dlp may have remuxed; find the tmp file by id stem.
        candidates = list(library.glob(f"_crate_tmp_{dl_info.get('id', '')}.*"))
        if not candidates:
            raise DownloadError("downloaded file not found")
        src = candidates[0]

    ext = "aiff" if audio_format == "aiff" else "wav"
    dest = library / f"{sanitize_filename(f'{artist} - {title}')}.{ext}"
    try:
        _convert(src, dest)
    finally:
        src.unlink(missing_ok=True)

    return DownloadResult(
        filepath=dest,
        title=title,
        artist=artist,
        source_url=webpage_url,
        genre=genre,
        duration=float(info.get("duration") or 0.0),
    )
