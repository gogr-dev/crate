"""crate CLI — Typer app wiring the whole pipeline together."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn
from rich.table import Table

from . import db
from .analyze import analyze_file, compatible_camelot
from .config import DB_PATH, VALID_FORMATS, Config, load_config, save_config
from .download import (
    DownloadError,
    download_audio,
    ffmpeg_available,
    is_url,
    parse_title,
    probe,
)
from .rekordbox import generate_xml
from .tagging import TaggingError, write_tags

app = typer.Typer(
    help="DJ library automation: download, analyze, tag, export Rekordbox XML.",
    no_args_is_help=True,
    add_completion=False,
)
crate_app = typer.Typer(help="Manage crates (playlists).", no_args_is_help=True)
app.add_typer(crate_app, name="crate")

console = Console()
err_console = Console(stderr=True)

AUDIO_EXTS = {".aiff", ".aif", ".wav", ".flac", ".mp3", ".m4a", ".mp4"}


def die(message: str) -> "typer.Exit":
    err_console.print(f"[bold red]error:[/] {message}")
    raise typer.Exit(1)


def get_conn() -> sqlite3.Connection:
    return db.connect(DB_PATH)


def _analyze_and_tag(
    conn: sqlite3.Connection,
    track_id: int,
    path: Path,
    *,
    title: str,
    artist: str,
    genre: str,
) -> object:
    result = analyze_file(str(path))
    try:
        write_tags(
            path,
            title=title,
            artist=artist,
            bpm=result.bpm,
            camelot=result.camelot,
            tonality=result.tonality,
            genre=genre,
        )
    except TaggingError as exc:
        err_console.print(f"[yellow]warning:[/] {exc}")
    db.update_analysis(
        conn,
        track_id,
        bpm=result.bpm,
        key_name=result.key_name,
        tonality=result.tonality,
        camelot=result.camelot,
        duration=result.duration,
    )
    return result


# ---------------------------------------------------------------- add

@app.command()
def add(
    target: str = typer.Argument(..., help="URL or search query."),
    crate: Optional[str] = typer.Option(None, "--crate", help="Assign to this crate."),
    artist: Optional[str] = typer.Option(None, "--artist", help="Override artist."),
    title: Optional[str] = typer.Option(None, "--title", help="Override title."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip search confirmation."),
    force: bool = typer.Option(False, "--force", help="Re-download even if known."),
) -> None:
    """Download a track, analyze, tag, and add it to the library."""
    if not ffmpeg_available():
        die("ffmpeg not found on PATH — install it (e.g. `brew install ffmpeg`).")

    cfg = load_config()
    conn = get_conn()
    search = not is_url(target)

    try:
        info = probe(target, search=search)
    except DownloadError as exc:
        die(f"could not resolve {target!r}: {exc}")

    matched = info.get("title", "(unknown)")
    webpage_url = info.get("webpage_url") or info.get("original_url") or target

    if search:
        console.print(f"Matched: [cyan]{matched}[/] — {webpage_url}")
        if not yes and not typer.confirm("Download this?", default=True):
            raise typer.Exit(0)

    existing = db.get_by_url(conn, webpage_url)
    if existing and not force:
        die(
            f"already in library as #{existing['id']} "
            f"({existing['artist']} - {existing['title']}). Use --force to re-download."
        )

    target_crate = crate or cfg.default_crate or None

    try:
        with console.status("[cyan]Downloading…[/]"):
            result = download_audio(
                webpage_url,
                cfg.library_path,
                audio_format=cfg.audio_format,
                search=False,
                artist_override=artist,
                title_override=title,
                info=info,
            )
    except DownloadError as exc:
        die(f"download failed: {exc}")

    size = result.filepath.stat().st_size if result.filepath.exists() else 0
    try:
        track_id = db.add_track(
            conn,
            path=str(result.filepath),
            title=result.title,
            artist=result.artist,
            genre=result.genre,
            source_url=result.source_url,
            duration=result.duration,
            size=size,
        )
    except sqlite3.IntegrityError:
        row = db.get_by_path(conn, str(result.filepath))
        track_id = int(row["id"]) if row else 0

    console.print(f"Added [green]#{track_id}[/] {result.artist} - {result.title}")

    try:
        with console.status("[cyan]Analyzing BPM + key…[/]"):
            analysis = _analyze_and_tag(
                conn, track_id, result.filepath,
                title=result.title, artist=result.artist, genre=result.genre,
            )
        note = " [yellow](octave-corrected)[/]" if analysis.bpm_corrected else ""
        console.print(
            f"  BPM [bold]{analysis.bpm:g}[/]{note}  •  "
            f"Key [bold]{analysis.key_name}[/] ([magenta]{analysis.camelot}[/])"
        )
    except Exception as exc:  # noqa: BLE001 — analysis is best-effort
        err_console.print(f"[yellow]warning:[/] analysis failed: {exc}")

    if target_crate:
        db.assign_track(conn, track_id, target_crate)
        console.print(f"  → crate [blue]{target_crate}[/]")


# ---------------------------------------------------------------- scan

@app.command()
def scan(
    folder: Path = typer.Argument(..., help="Folder to scan for audio files."),
    crate: Optional[str] = typer.Option(None, "--crate", help="Assign found tracks."),
) -> None:
    """Analyze, tag, and add untracked audio files from a folder."""
    folder = folder.expanduser()
    if not folder.is_dir():
        die(f"not a directory: {folder}")

    conn = get_conn()
    files = sorted(
        p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )
    if not files:
        console.print("No audio files found.")
        raise typer.Exit(0)

    added = skipped = failed = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Scanning", total=len(files))
        for path in files:
            progress.update(task, description=f"Scanning [dim]{path.name}[/]")
            if db.get_by_path(conn, str(path)):
                skipped += 1
                progress.advance(task)
                continue
            try:
                from .tagging import read_tags

                tags = read_tags(path)
                t_artist, t_title = tags.get("artist", ""), tags.get("title", "")
                if not (t_artist and t_title):
                    p_artist, p_title = parse_title(path.stem)
                    t_artist = t_artist or p_artist
                    t_title = t_title or p_title or path.stem
                track_id = db.add_track(
                    conn,
                    path=str(path),
                    title=t_title,
                    artist=t_artist,
                    genre=tags.get("genre", ""),
                    size=path.stat().st_size,
                )
                _analyze_and_tag(
                    conn, track_id, path,
                    title=t_title, artist=t_artist, genre=tags.get("genre", ""),
                )
                if crate:
                    db.assign_track(conn, track_id, crate)
                added += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                err_console.print(f"[yellow]skip[/] {path.name}: {exc}")
            progress.advance(task)

    table = Table(title="Scan summary")
    table.add_column("Added", justify="right", style="green")
    table.add_column("Skipped", justify="right", style="yellow")
    table.add_column("Failed", justify="right", style="red")
    table.add_row(str(added), str(skipped), str(failed))
    console.print(table)


# ---------------------------------------------------------------- analyze

@app.command()
def analyze(
    target: str = typer.Argument(..., help="A file path, a track id, or 'all'."),
) -> None:
    """Re-run BPM/key analysis and update tags + DB."""
    conn = get_conn()
    rows: list[sqlite3.Row]
    if target == "all":
        rows = db.all_tracks(conn)
    elif target.isdigit():
        row = db.get_track(conn, int(target))
        if not row:
            die(f"no track with id {target}")
        rows = [row]
    else:
        path = Path(target).expanduser()
        row = db.get_by_path(conn, str(path))
        if not row:
            die(f"file not tracked: {path}")
        rows = [row]

    for row in rows:
        path = Path(row["path"])
        if not path.exists():
            err_console.print(f"[yellow]missing[/] {path}")
            continue
        try:
            result = _analyze_and_tag(
                conn, int(row["id"]), path,
                title=row["title"], artist=row["artist"], genre=row["genre"],
            )
            note = " (corrected)" if result.bpm_corrected else ""
            console.print(
                f"#{row['id']} {row['artist']} - {row['title']}: "
                f"[bold]{result.bpm:g}[/]{note} BPM, "
                f"[magenta]{result.camelot}[/] ({result.key_name})"
            )
        except Exception as exc:  # noqa: BLE001
            err_console.print(f"[red]failed[/] #{row['id']}: {exc}")


# ---------------------------------------------------------------- list

@app.command(name="list")
def list_tracks(
    crate: Optional[str] = typer.Option(None, "--crate", help="Only this crate."),
    key: Optional[str] = typer.Option(None, "--key", help="Exact Camelot key (e.g. 8A)."),
    compatible: Optional[str] = typer.Option(
        None, "--compatible", help="Harmonically compatible with this Camelot key."
    ),
    bpm: Optional[str] = typer.Option(None, "--bpm", help="BPM range, e.g. 120-126."),
    sort: str = typer.Option("added", "--sort", help="bpm | key | added."),
) -> None:
    """List the library (optionally filtered) as a table."""
    conn = get_conn()

    camelot_in: Optional[list[str]] = None
    if compatible:
        try:
            camelot_in = compatible_camelot(compatible)
        except ValueError as exc:
            die(str(exc))
    elif key:
        camelot_in = [key.strip().upper()]

    bpm_range = None
    if bpm:
        try:
            lo, hi = bpm.split("-")
            bpm_range = (float(lo), float(hi))
        except ValueError:
            die(f"invalid --bpm range {bpm!r}; expected e.g. 120-126")

    rows = db.filter_tracks(
        conn, crate=crate, camelot_in=camelot_in, bpm_range=bpm_range, sort=sort
    )
    if not rows:
        console.print("No tracks match.")
        raise typer.Exit(0)

    table = Table(show_lines=False)
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Title")
    table.add_column("Artist", style="cyan")
    table.add_column("BPM", justify="right")
    table.add_column("Key", style="magenta")
    table.add_column("Crates", style="blue")
    table.add_column("Added", style="dim")
    for row in rows:
        crates = ", ".join(db.crates_for_track(conn, int(row["id"])))
        bpm_val = f"{row['bpm']:g}" if row["bpm"] is not None else "—"
        table.add_row(
            str(row["id"]), row["title"] or "—", row["artist"] or "—",
            bpm_val, row["camelot"] or "—", crates or "—", row["date_added"],
        )
    console.print(table)


# ---------------------------------------------------------------- crates

@app.command()
def crates() -> None:
    """List all crates and their track counts."""
    conn = get_conn()
    rows = db.list_crates(conn)
    if not rows:
        console.print("No crates yet. Create one with `crate crate add <name>`.")
        raise typer.Exit(0)
    table = Table(title="Crates")
    table.add_column("Name", style="blue")
    table.add_column("Tracks", justify="right")
    for row in rows:
        table.add_row(row["name"], str(row["count"]))
    console.print(table)


@crate_app.command("add")
def crate_add(name: str = typer.Argument(..., help="Crate name.")) -> None:
    """Create a crate."""
    conn = get_conn()
    db.create_crate(conn, name)
    console.print(f"Crate [blue]{name}[/] ready.")


@crate_app.command("assign")
def crate_assign(
    track_id: int = typer.Argument(..., help="Track id."),
    name: str = typer.Argument(..., help="Crate name."),
) -> None:
    """Assign a track to a crate (creating the crate if needed)."""
    conn = get_conn()
    if not db.get_track(conn, track_id):
        die(f"no track with id {track_id}")
    db.assign_track(conn, track_id, name)
    console.print(f"Track #{track_id} → crate [blue]{name}[/].")


@crate_app.command("rm")
def crate_rm(name: str = typer.Argument(..., help="Crate name.")) -> None:
    """Remove a crate (tracks themselves are kept)."""
    conn = get_conn()
    if db.remove_crate(conn, name):
        console.print(f"Removed crate [blue]{name}[/].")
    else:
        die(f"no crate named {name!r}")


# ---------------------------------------------------------------- export

@app.command()
def export(
    out: Optional[Path] = typer.Option(None, "--out", help="Output XML path."),
) -> None:
    """Generate Rekordbox XML for the whole collection + crates."""
    cfg = load_config()
    conn = get_conn()
    tracks = db.all_tracks(conn)

    playlists: dict[str, list[int]] = {}
    for crate in db.list_crates(conn):
        playlists[crate["name"]] = db.tracks_in_crate(conn, crate["name"])

    xml = generate_xml(tracks, playlists)
    out_path = (out or (cfg.library_path / "rekordbox.xml")).expanduser()
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(xml, encoding="utf-8")
    except OSError as exc:
        die(f"could not write {out_path}: {exc}")

    console.print(f"Wrote [green]{out_path}[/] ({len(tracks)} tracks, {len(playlists)} crates).")
    console.print()
    console.print("[bold]Import into Rekordbox:[/]")
    console.print("  1. Preferences → Advanced → Database → rekordbox xml")
    console.print(f"  2. Set 'Imported Library' to: [cyan]{out_path}[/]")
    console.print("  3. In the tree view, open the [cyan]rekordbox xml[/] node and import.")


# ---------------------------------------------------------------- config

@app.command()
def config(
    library: Optional[Path] = typer.Option(None, "--library", help="Set library folder."),
    audio_format: Optional[str] = typer.Option(
        None, "--format", help="aiff | wav."
    ),
    default_crate: Optional[str] = typer.Option(
        None, "--default-crate", help="Default crate for `add`."
    ),
    show: bool = typer.Option(False, "--show", help="Print current config."),
) -> None:
    """View or change configuration (~/.crate/config.toml)."""
    cfg = load_config()
    changed = False
    if library is not None:
        cfg.library = str(library.expanduser())
        changed = True
    if audio_format is not None:
        if audio_format not in VALID_FORMATS:
            die(f"format must be one of {VALID_FORMATS}")
        cfg.audio_format = audio_format
        changed = True
    if default_crate is not None:
        cfg.default_crate = default_crate
        changed = True
    if changed:
        save_config(cfg)
        console.print("[green]Config saved.[/]")

    if show or not changed:
        table = Table(title="crate config")
        table.add_column("Setting", style="cyan")
        table.add_column("Value")
        table.add_row("library", cfg.library)
        table.add_row("audio_format", cfg.audio_format)
        table.add_row("default_crate", cfg.default_crate or "—")
        console.print(table)


# ---------------------------------------------------------------- doctor

@app.command()
def doctor(
    prune: bool = typer.Option(False, "--prune", help="Remove DB entries for missing files."),
) -> None:
    """Sanity-check the environment and library."""
    cfg = load_config()
    ok = True

    if ffmpeg_available():
        console.print("[green]✓[/] ffmpeg found")
    else:
        console.print("[red]✗[/] ffmpeg NOT found on PATH")
        ok = False

    lib = cfg.library_path
    try:
        lib.mkdir(parents=True, exist_ok=True)
        probe_file = lib / ".crate_write_test"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
        console.print(f"[green]✓[/] library writable: {lib}")
    except OSError as exc:
        console.print(f"[red]✗[/] library not writable ({lib}): {exc}")
        ok = False

    try:
        conn = get_conn()
        tracks = db.all_tracks(conn)
        console.print(f"[green]✓[/] database OK ({len(tracks)} tracks)")
    except sqlite3.DatabaseError as exc:
        die(f"database error: {exc}")

    missing = [r for r in tracks if not Path(r["path"]).exists()]
    if missing:
        console.print(f"[yellow]![/] {len(missing)} track(s) point to missing files:")
        for r in missing:
            console.print(f"    #{r['id']} {r['path']}")
        if prune:
            for r in missing:
                db.delete_track(conn, int(r["id"]))
            console.print(f"[green]Pruned {len(missing)} dead entries.[/]")
        else:
            console.print("    Run [cyan]crate doctor --prune[/] to remove them.")
    else:
        console.print("[green]✓[/] no missing files")

    raise typer.Exit(0 if ok else 1)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
