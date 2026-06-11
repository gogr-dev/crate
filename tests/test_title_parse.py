"""Video title → artist/title parsing, including junk stripping."""

import pytest

from crate.download import clean_title, is_url, parse_title, sanitize_filename


@pytest.mark.parametrize(
    "raw, artist, title",
    [
        ("Daft Punk - Harder Better Faster", "Daft Punk", "Harder Better Faster"),
        (
            "Artist - Track Name (Official Video)",
            "Artist",
            "Track Name",
        ),
        (
            "Artist - Track Name [Official Music Video]",
            "Artist",
            "Track Name",
        ),
        (
            "Someone - Cool Tune (Extended Mix)",
            "Someone",
            "Cool Tune (Extended Mix)",
        ),
        (
            "Someone - Cool Tune (Original Mix) [HD]",
            "Someone",
            "Cool Tune (Original Mix)",
        ),
        (
            "DJ X - Banger (Remix) (Official Audio)",
            "DJ X",
            "Banger (Remix)",
        ),
        (
            "Producer - Night Drive | Extended Mix",
            "Producer",
            "Night Drive (Extended Mix)",
        ),
        (
            "Producer - Night Drive | Free Download",
            "Producer",
            "Night Drive",
        ),
    ],
)
def test_parse_title_cases(raw, artist, title):
    assert parse_title(raw) == (artist, title)


def test_parse_title_no_separator_uses_uploader():
    assert parse_title("Just A Title (Official Video)", uploader="ChannelName") == (
        "ChannelName",
        "Just A Title",
    )


def test_clean_title_preserves_qualifiers():
    assert clean_title("Track (Original Mix)") == "Track (Original Mix)"
    assert clean_title("Track (VIP)") == "Track (VIP)"
    assert clean_title("Track (Remix)") == "Track (Remix)"


def test_clean_title_strips_junk():
    assert clean_title("Track (Official Video)") == "Track"
    assert clean_title("Track [4K]") == "Track"
    assert clean_title("Track [Free Download]") == "Track"


def test_clean_title_keeps_feat():
    assert clean_title("Track (feat. Someone)") == "Track (feat. Someone)"


def test_is_url():
    assert is_url("https://youtube.com/watch?v=abc")
    assert is_url("http://soundcloud.com/x/y")
    assert not is_url("some search query")
    assert not is_url("artist - title")


def test_sanitize_filename():
    assert sanitize_filename("AC/DC - Thunder") == "ACDC - Thunder"
    assert sanitize_filename('A: "B" <C>') == "A B C"
    assert sanitize_filename("   ") == "untitled"
    assert "/" not in sanitize_filename("a/b/c")
