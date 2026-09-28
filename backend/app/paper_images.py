"""Turn an uploaded photo(s) or PDF into page images for the vision model. All in memory.

Pages are converted to JPEG and scaled so the long side is at most MAX_SIDE pixels:
enough for a model to read printed and handwritten marks, small enough to keep
vision calls cheap. Nothing here writes to disk.
"""

import io
from dataclasses import dataclass

import pymupdf
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

register_heif_opener()  # lets Pillow open iPhone HEIC photos

MAX_FILES_BYTES = 15 * 1024 * 1024
MAX_PAGES = 10
MAX_SIDE = 2000
PDF_DPI = 150
IMAGE_TYPES = {"image/jpeg", "image/png", "image/heic", "image/heif", "image/webp"}
PDF_TYPE = "application/pdf"


class PaperFileError(ValueError):
    """The upload cannot be used. The message is shown to the parent."""


@dataclass
class Page:
    jpeg: bytes
    source: str  # file name and page, for the parent's reference


def _kind(name: str, content_type: str, data: bytes) -> str:
    if data[:5] == b"%PDF-" or content_type == PDF_TYPE or name.lower().endswith(".pdf"):
        return "pdf"
    if content_type in IMAGE_TYPES or name.lower().endswith((".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp")):
        return "image"
    raise PaperFileError(f"{name} is not a photo or PDF.")


def _to_jpeg(image: Image.Image) -> bytes:
    image = ImageOps.exif_transpose(image)  # phone photos carry their rotation in EXIF
    image = image.convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()


def prepare_pages(files: list[tuple[str, str, bytes]]) -> list[Page]:
    """files: (file name, content type, bytes). Returns pages in upload order."""
    if not files:
        raise PaperFileError("Add at least one photo or PDF.")
    if sum(len(data) for _, _, data in files) > MAX_FILES_BYTES:
        raise PaperFileError(f"The files are larger than {MAX_FILES_BYTES // (1024 * 1024)} MB in total.")

    pages: list[Page] = []
    for name, content_type, data in files:
        if not data:
            raise PaperFileError(f"{name} is empty.")
        if _kind(name, content_type, data) == "pdf":
            try:
                doc = pymupdf.open(stream=data, filetype="pdf")
            except (pymupdf.FileDataError, RuntimeError) as e:
                raise PaperFileError(f"{name} could not be opened as a PDF.") from e
            with doc:
                if doc.needs_pass:
                    raise PaperFileError(f"{name} is password-protected.")
                if len(pages) + doc.page_count > MAX_PAGES:
                    raise PaperFileError(f"A paper can have at most {MAX_PAGES} pages.")
                for i, page in enumerate(doc, start=1):
                    pix = page.get_pixmap(dpi=PDF_DPI)
                    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    pages.append(Page(_to_jpeg(image), f"{name}, page {i}"))
        else:
            if len(pages) + 1 > MAX_PAGES:
                raise PaperFileError(f"A paper can have at most {MAX_PAGES} pages.")
            try:
                with Image.open(io.BytesIO(data)) as image:
                    pages.append(Page(_to_jpeg(image), name))
            except (UnidentifiedImageError, OSError) as e:
                raise PaperFileError(f"{name} could not be opened as a photo.") from e
    return pages
