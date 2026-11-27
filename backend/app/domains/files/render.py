"""Render the first page of an uploaded file to a small WebP preview.

Runs in its own short-lived process (``python -m app.domains.files.render``) so a
malformed or hostile file can only crash or exhaust this child, never the API.
Prints ``{"page_count": n}`` on success.
"""

import json
import sys

PREVIEW_EDGE = 640  # 2x the 320px badge


def _pdf(source: str, target: str) -> int:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(source)
    try:
        pages = len(document)
        if pages < 1:
            raise ValueError("PDF has no pages")
        page = document[0]
        width, height = page.get_size()
        scale = min(PREVIEW_EDGE / max(width, height, 1), 4.0)
        image = page.render(scale=scale).to_pil()
        image.convert("RGB").save(target, "WEBP", quality=80)
        return pages
    finally:
        document.close()


def _image(source: str, target: str) -> int:
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = 60_000_000  # refuse decompression bombs
    with Image.open(source) as probe:
        probe.verify()
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE))
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        image.save(target, "WEBP", quality=80)
    return 1


def main() -> None:
    kind, source, target = sys.argv[1:4]
    pages = _pdf(source, target) if kind == "pdf" else _image(source, target)
    print(json.dumps({"page_count": pages}))


if __name__ == "__main__":
    main()
