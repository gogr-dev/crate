"""Metadata tagging via mutagen.

Primary format is AIFF (ID3 in an AIFF chunk). MP3/WAV also use ID3; FLAC uses
Vorbis comments; M4A uses MP4 atoms. We write the Camelot code into the comment
and the standard key (e.g. ``Am``) into the key field, per the spec.
"""

from __future__ import annotations

from pathlib import Path


class TaggingError(Exception):
    """Raised for expected tagging failures (unreadable/unsupported file)."""


def _apply_id3(tags, title, artist, bpm, camelot, tonality, genre) -> None:
    from mutagen.id3 import COMM, TBPM, TCON, TIT2, TKEY, TPE1

    if title:
        tags.setall("TIT2", [TIT2(encoding=3, text=title)])
    if artist:
        tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
    if bpm:
        tags.setall("TBPM", [TBPM(encoding=3, text=str(int(round(bpm))))])
    if tonality:
        tags.setall("TKEY", [TKEY(encoding=3, text=tonality)])
    if camelot:
        tags.setall("COMM", [COMM(encoding=3, lang="eng", desc="", text=camelot)])
    if genre:
        tags.setall("TCON", [TCON(encoding=3, text=genre)])


def write_tags(
    path: str | Path,
    *,
    title: str = "",
    artist: str = "",
    bpm: float | None = None,
    camelot: str = "",
    tonality: str = "",
    genre: str = "",
) -> None:
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix in (".aiff", ".aif", ".aifc"):
            from mutagen.aiff import AIFF

            audio = AIFF(str(path))
            if audio.tags is None:
                audio.add_tags()
            _apply_id3(audio.tags, title, artist, bpm, camelot, tonality, genre)
            audio.save()
        elif suffix == ".mp3":
            from mutagen.mp3 import MP3

            audio = MP3(str(path))
            if audio.tags is None:
                audio.add_tags()
            _apply_id3(audio.tags, title, artist, bpm, camelot, tonality, genre)
            audio.save()
        elif suffix == ".wav":
            from mutagen.wave import WAVE

            audio = WAVE(str(path))
            if audio.tags is None:
                audio.add_tags()
            _apply_id3(audio.tags, title, artist, bpm, camelot, tonality, genre)
            audio.save()
        elif suffix == ".flac":
            from mutagen.flac import FLAC

            audio = FLAC(str(path))
            if title:
                audio["title"] = title
            if artist:
                audio["artist"] = artist
            if bpm:
                audio["bpm"] = str(int(round(bpm)))
            if tonality:
                audio["initialkey"] = tonality
            if camelot:
                audio["comment"] = camelot
            if genre:
                audio["genre"] = genre
            audio.save()
        elif suffix in (".m4a", ".mp4", ".m4b"):
            from mutagen.mp4 import MP4

            audio = MP4(str(path))
            if title:
                audio["\xa9nam"] = [title]
            if artist:
                audio["\xa9ART"] = [artist]
            if bpm:
                audio["tmpo"] = [int(round(bpm))]
            if camelot:
                audio["\xa9cmt"] = [camelot]
            if tonality:
                audio["----:com.apple.iTunes:initialkey"] = [
                    tonality.encode("utf-8")
                ]
            if genre:
                audio["\xa9gen"] = [genre]
            audio.save()
        else:
            raise TaggingError(f"unsupported file type: {suffix}")
    except TaggingError:
        raise
    except Exception as exc:  # mutagen raises a variety of types
        raise TaggingError(f"failed to tag {path.name}: {exc}") from exc


def read_tags(path: str | Path) -> dict[str, str]:
    """Best-effort read of title/artist/genre from an existing file."""
    path = Path(path)
    out = {"title": "", "artist": "", "genre": ""}
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(path), easy=True)
        if audio is None or not audio.tags:
            return out
        for key, field in (("title", "title"), ("artist", "artist"), ("genre", "genre")):
            val = audio.tags.get(field)
            if val:
                out[key] = val[0] if isinstance(val, list) else str(val)
    except Exception:
        return out
    return out
