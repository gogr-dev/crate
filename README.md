# crate

A single-user DJ library automation tool. Turn "I heard a track I want to play"
into "it's in my Rekordbox library — downloaded, analyzed, tagged, and sorted
into a crate" with one command.

```
crate add "https://youtube.com/watch?v=..." --crate "Peak Time"
```

The pipeline: **download** (yt-dlp → ffmpeg → AIFF) → **analyze** (librosa BPM +
musical key → Camelot) → **tag** (mutagen ID3) → **track** (SQLite manifest at
`~/.crate/crate.db`) → **export** (Rekordbox-importable XML).

## Requirements

- Python 3.11+
- `ffmpeg` on your `PATH` (`brew install ffmpeg` on macOS)

## Install

```bash
pipx install .            # recommended — isolated CLI install
# or, for development:
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

This installs the `crate` command.

## First-use sequence

```bash
crate doctor                                   # verify ffmpeg + library + DB
crate config --library ~/Music/crate           # (optional) set library folder
crate add "fisher losing it"                    # search YouTube, confirm, grab top hit
crate add "https://youtube.com/watch?v=..." --crate "Peak Time"
crate list --sort bpm                           # see everything you've got
crate list --compatible 8A                      # harmonically mixable with 8A
crate export                                     # write rekordbox.xml
```

## Commands

### `crate add <url-or-query>`
Download a track, convert to AIFF, analyze, tag, and add it to the library.

```bash
crate add "https://youtube.com/watch?v=dQw4w9WgXcQ"
crate add "artist name track name"              # ytsearch1, asks to confirm
crate add "artist - track" --yes                # skip the confirmation
crate add "<url>" --artist "Fisher" --title "Losing It (Extended Mix)"
crate add "<url>" --genre "Tech House"          # set genre (YouTube rarely supplies one)
crate add "<url>" --crate "Tech House"          # also file it in a crate
crate add "<url>" --force                        # re-download a known URL
```

- URLs download directly; anything else is treated as a search query
  (`ytsearch1:`) and you'll be asked to confirm the match (skip with `--yes`).
- Artist/title are parsed from the video title (split on `" - "`), stripping junk
  like `[Official Video]` / `(Official Audio)` while **preserving** meaningful
  qualifiers like `(Extended Mix)`, `(Original Mix)`, `(Remix)`.
  `--artist` / `--title` override parsing.
- Files are saved as `Artist - Title.aiff` in the library folder.
- A source URL already in the DB is skipped unless you pass `--force`.

### `crate scan <folder> [--crate NAME] [--genre G] [--no-analyze]`
Walk a folder for audio files (`aiff, wav, flac, mp3, m4a`), then analyze, tag,
and add any not already tracked (deduped by absolute path). Shows a progress bar
and a summary table (added / skipped / failed).

Use `--no-analyze` to trust the files' existing tags and skip BPM/key detection —
important when pointing it at a library already analyzed by Mixed In Key or
Rekordbox, so crate doesn't overwrite better data. `--genre` sets the genre on
everything found.

### `crate rm <track-id> [--delete-file]`
Remove a track from the library and its crate assignments (asks to confirm; skip
with `--yes`). The audio file is kept on disk unless you pass `--delete-file`.

### `crate analyze <file | track-id | all>`
Re-run BPM/key analysis (e.g. after improving the analysis) and update tags + DB.

```bash
crate analyze all
crate analyze 12
crate analyze "~/Music/crate/Fisher - Losing It.aiff"
```

### `crate list`
Print the library (or a filtered slice) as a table: ID, Title, Artist, BPM,
Key (Camelot), Crates, Added.

```bash
crate list --crate "Peak Time"
crate list --key 8A                 # exact Camelot key
crate list --compatible 8A          # 8A + relative (8B) + neighbours (7A, 9A)
crate list --bpm 120-126
crate list --sort bpm               # bpm | key | added
```

### Crates
```bash
crate crates                        # list crates + counts
crate crate add "Peak Time"
crate crate assign 12 "Peak Time"   # assign track #12 (a track can be in many)
crate crate rm "Peak Time"          # remove the crate (tracks are kept)
```

### `crate export [--out PATH]`
Generate Rekordbox XML for the whole collection plus every crate as a playlist.
Default output: `~/Music/crate/rekordbox.xml`.

**Import into Rekordbox:**
1. Preferences → Advanced → Database → rekordbox xml
2. Set "Imported Library" to your `rekordbox.xml`
3. In the tree view, open the **rekordbox xml** node and import.

> **Tip:** Rekordbox uses the BPM, key, and beatgrid from this XML on import. To
> stop it from re-analyzing and overwriting them, turn off auto-analysis for
> imported tracks (Preferences → Analysis) and don't manually re-run "Analyze
> Track". The beatgrid is anchored to the first detected beat (`Inizio`), so it
> should land on the downbeat rather than at 0:00.

### `crate config`
```bash
crate config --show
crate config --library ~/Music/crate
crate config --format aiff          # aiff (default) | wav
crate config --default-crate "Inbox"
```
Stored at `~/.crate/config.toml`.

### `crate doctor [--prune]`
Checks ffmpeg, that the library folder is writable, the DB is intact, and counts
tracks whose files are missing on disk. `--prune` removes those dead entries.

## How analysis works

- **Key:** chromagram (`chroma_cqt`) on the harmonic component (`hpss`), averaged
  over time, correlated against Krumhansl-Schmuckler profiles for all 24
  major/minor keys; best correlation wins, then mapped to Camelot.
- **BPM:** `librosa.beat.beat_track`, with octave-error correction — tempos under
  90 are doubled and over 160 are halved (noted in the output when applied).
  librosa is decent but not Mixed-In-Key accurate on four-on-the-floor; treat the
  BPM as a strong starting point and sanity-check on import.
- **Beatgrid anchor:** the first detected beat is exported as the Rekordbox
  `Inizio`, so the grid lands on the downbeat instead of at 0:00.
- Only the first ~120 s of audio is loaded, which is plenty for key/BPM and keeps
  it fast.

## Notes & decisions

- **One user, simple by design.** Plain SQL (no ORM), no plugin system, no config
  beyond a small TOML file.
- **AIFF is the default format** (DJ-friendly, lossless, tags via ID3). `wav` is
  available via `crate config --format wav`.
- **Camelot** is written into the comment field; the standard key (e.g. `Am`)
  into the key field (ID3 `TKEY`), so both Rekordbox and Camelot-aware workflows
  see the right thing.
- **AIFF tagging uses ID3** (the format embeds an ID3 chunk) — handled via
  `mutagen.aiff`.
- The **`Location`** attribute in the exported XML is a `file://localhost/` URL
  with the absolute path percent-encoded (spaces, apostrophes, etc.), which is
  the part Rekordbox is pickiest about.
- yt-dlp can't write AIFF directly, so `crate` downloads bestaudio and converts
  with ffmpeg.

## Development

```bash
pip install -e ".[dev]"
pytest                                       # no network or ffmpeg required
pytest --cov=crate --cov-report=term-missing # with coverage
```

The suite is network-free and ffmpeg-free: audio is synthesized with
numpy/soundfile and yt-dlp is mocked at the `download_audio` boundary. Tests
cover the Camelot wheel + compatible-key logic, title parsing/junk stripping,
Rekordbox XML (structure, `Location` encoding, and a byte-for-byte golden
snapshot under `tests/golden/`), DB insert/dedupe/crate assignment, every CLI
command end-to-end, and a full scan→crates→export pipeline that verifies
analysis output reaches the DB, the on-disk tags, and the exported XML.

If you intentionally change the XML format, regenerate the golden snapshot and
review the diff:

```bash
python -m tests.test_rekordbox_golden        # rewrites tests/golden/collection.xml
```
