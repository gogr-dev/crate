"""Rekordbox XML structure + Location URL encoding."""

import xml.etree.ElementTree as ET

from crate.rekordbox import generate_xml, location_url


def test_location_url_encodes_spaces_and_apostrophe(tmp_path):
    f = tmp_path / "Daft Punk - Don't Stop (Extended Mix).aiff"
    f.write_bytes(b"\x00")
    url = location_url(f)
    assert url.startswith("file://localhost/")
    assert "%20" in url          # spaces encoded
    assert "%27" in url          # apostrophe encoded
    assert " " not in url
    assert "'" not in url
    # Path separators preserved (not encoded to %2F).
    assert "%2F" not in url.upper()


def test_location_url_round_trips_path(tmp_path):
    from urllib.parse import unquote

    f = tmp_path / "x y.aiff"
    f.write_bytes(b"\x00")
    url = location_url(f)
    decoded = unquote(url.replace("file://localhost", ""))
    assert decoded == str(f.resolve())


def _fake_track(tmp_path):
    f = tmp_path / "Artist - Song's Name.aiff"
    f.write_bytes(b"\x00" * 1024)
    return {
        "id": 7,
        "path": str(f),
        "title": "Song's Name",
        "artist": "Artist",
        "genre": "House",
        "bpm": 124.0,
        "duration": 210.4,
        "size": 1024,
        "date_added": "2026-06-10",
        "tonality": "Am",
    }


def test_generate_xml_structure(tmp_path):
    track = _fake_track(tmp_path)
    xml = generate_xml([track], {"My Crate": [7]})

    assert xml.startswith("<?xml")
    root = ET.fromstring(xml)
    assert root.tag == "DJ_PLAYLISTS"
    assert root.attrib["Version"] == "1.0.0"

    product = root.find("PRODUCT")
    assert product.attrib["Company"] == "AlphaTheta"

    collection = root.find("COLLECTION")
    assert collection.attrib["Entries"] == "1"
    t = collection.find("TRACK")
    assert t.attrib["TrackID"] == "7"
    assert t.attrib["Name"] == "Song's Name"
    assert t.attrib["AverageBpm"] == "124.00"
    assert t.attrib["TotalTime"] == "210"
    assert t.attrib["Kind"] == "AIFF File"
    assert t.attrib["Tonality"] == "Am"
    assert t.attrib["Location"].startswith("file://localhost/")

    tempo = t.find("TEMPO")
    assert tempo.attrib["Bpm"] == "124.00"
    assert tempo.attrib["Metro"] == "4/4"
    assert tempo.attrib["Inizio"] == "0.0"

    # Playlists node tree.
    root_node = root.find("PLAYLISTS/NODE")
    assert root_node.attrib["Type"] == "0"
    assert root_node.attrib["Name"] == "ROOT"
    pl = root_node.find("NODE")
    assert pl.attrib["Type"] == "1"
    assert pl.attrib["Name"] == "My Crate"
    ref = pl.find("TRACK")
    assert ref.attrib["Key"] == "7"


def test_generate_xml_empty_collection():
    xml = generate_xml([], {})
    root = ET.fromstring(xml)
    assert root.find("COLLECTION").attrib["Entries"] == "0"
