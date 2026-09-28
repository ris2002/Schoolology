"""Uploaded photos and PDFs become page images, in memory, within the limits."""

import io

import pymupdf
import pytest
from PIL import Image
from pillow_heif import from_pillow

from app import paper_images
from app.paper_images import PaperFileError, prepare_pages


def image_bytes(fmt: str, size=(3000, 1500)) -> bytes:
    out = io.BytesIO()
    image = Image.new("RGB", size, "white")
    if fmt == "HEIC":
        from_pillow(image).save(out, format="HEIF")
    else:
        image.save(out, fmt)
    return out.getvalue()


def pdf_bytes(pages: int) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Question {i + 1}: 3/4 ÷ 1/2")
    return doc.tobytes()


def test_photos_and_pdf_pages_become_scaled_jpegs_in_order():
    pages = prepare_pages([
        ("front.png", "image/png", image_bytes("PNG")),
        ("test.pdf", "application/pdf", pdf_bytes(2)),
        ("back.heic", "image/heic", image_bytes("HEIC")),
    ])
    assert [p.source for p in pages] == ["front.png", "test.pdf, page 1", "test.pdf, page 2", "back.heic"]
    for p in pages:
        with Image.open(io.BytesIO(p.jpeg)) as im:
            assert im.format == "JPEG" and max(im.size) <= paper_images.MAX_SIDE


def test_a_pdf_is_recognised_by_its_content_not_its_name():
    assert len(prepare_pages([("scan", "application/octet-stream", pdf_bytes(1))])) == 1


@pytest.mark.parametrize("files, message", [
    ([], "at least one"),
    ([("notes.txt", "text/plain", b"hello")], "not a photo or PDF"),
    ([("fake.jpg", "image/jpeg", b"not really a jpeg")], "could not be opened"),
    ([("empty.png", "image/png", b"")], "empty"),
    ([("long.pdf", "application/pdf", pdf_bytes(11))], "at most 10 pages"),
])
def test_bad_uploads_are_refused_with_a_readable_reason(files, message):
    with pytest.raises(PaperFileError, match=message):
        prepare_pages(files)


def test_total_size_limit(monkeypatch):
    monkeypatch.setattr(paper_images, "MAX_FILES_BYTES", 100)
    with pytest.raises(PaperFileError, match="larger than"):
        prepare_pages([("a.png", "image/png", image_bytes("PNG", (50, 50)))])
