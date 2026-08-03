"""
Job Start packet photo processing — pure tests (no Drive, no network).
Runs under pytest OR directly: `python tests/test_jobstart_photos.py`.
"""
from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subsystems.jobstart import photos as ph  # noqa: E402


def _png_bytes(w=1000, h=800, color=(90, 140, 60)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- is_image
def test_is_image_by_mime_and_extension():
    assert ph.is_image({"mimeType": "image/jpeg", "name": "x"})
    assert ph.is_image({"mimeType": "", "name": "Front door.HEIC"})
    assert ph.is_image({"name": "site.PNG"})
    assert not ph.is_image({"mimeType": "application/pdf", "name": "scope.pdf"})
    assert not ph.is_image({"mimeType": "", "name": "Takeoff Totals.xlsx"})


# ---------------------------------------------------------------- caption
def test_caption_strips_extension_and_camera_token():
    assert ph.caption_from_name("IMG_4417 - Lock box front door.HEIC") == "Lock box front door"
    assert ph.caption_from_name("Scaffold north wall.jpg") == "Scaffold north wall"
    assert ph.caption_from_name("site_measure_photo.png") == "site measure photo"
    assert ph.caption_from_name("") == ""


# ---------------------------------------------------------------- select
def test_select_filters_sorts_and_caps():
    files = [
        {"id": "3", "name": "c.jpg", "mimeType": "image/jpeg"},
        {"id": "1", "name": "a.png", "mimeType": "image/png"},
        {"id": "9", "name": "notes.pdf", "mimeType": "application/pdf"},
        {"id": "2", "name": "B.jpeg", "mimeType": "image/jpeg"},
    ]
    got = ph.select_photos(files, limit=10)
    assert [f["id"] for f in got] == ["1", "2", "3"], "images only, sorted by name"

    capped = ph.select_photos(files, limit=2)
    assert [f["id"] for f in capped] == ["1", "2"]


def test_select_only_filter_is_the_sub_seam():
    files = [
        {"id": "1", "name": "a.jpg", "mimeType": "image/jpeg"},
        {"id": "2", "name": "b.jpg", "mimeType": "image/jpeg"},
        {"id": "3", "name": "c.jpg", "mimeType": "image/jpeg"},
    ]
    got = ph.select_photos(files, only=["2", "3"])
    assert {f["id"] for f in got} == {"2", "3"}


# ---------------------------------------------------------------- to_data_uri
def test_to_data_uri_downscales_and_encodes_jpeg():
    uri = ph.to_data_uri(_png_bytes(2000, 1500), max_px=800, quality=70)
    assert uri and uri.startswith("data:image/jpeg;base64,")
    raw = base64.b64decode(uri.split(",", 1)[1])
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as im:
        assert max(im.size) <= 800, "longest edge downscaled to the cap"
        assert im.format == "JPEG"


def test_to_data_uri_bad_bytes_returns_none():
    assert ph.to_data_uri(b"not an image at all") is None
    assert ph.to_data_uri(b"") is None


# ---------------------------------------------------------------- build_entries
def test_build_entries_skips_undecodable_and_keeps_order():
    items = [
        {"name": "IMG_1 - North wall.png", "bytes": _png_bytes(300, 200)},
        {"name": "broken.jpg", "bytes": b"garbage"},
        {"name": "Lock box.png", "bytes": _png_bytes(300, 200)},
    ]
    entries = ph.build_entries(items)
    assert len(entries) == 2, "the undecodable one is dropped"
    assert entries[0]["caption"] == "North wall"
    assert entries[1]["caption"] == "Lock box"
    assert all(e["data_uri"].startswith("data:image/jpeg;base64,") for e in entries)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
