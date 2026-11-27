"""Storage backends and preview rendering; no database needed."""

import asyncio
import io
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.core.config import Settings
from app.core.storage import LocalStorage, S3Storage, scan_status_from_tag
from app.domains.files.controllers import _detect_type, _keys, _render_preview


def _pdf_bytes(pages: int = 2) -> bytes:
    images = [Image.new("RGB", (595, 842), color) for color in ("white", "navy", "teal")][:pages]
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1600, 900), "orange").save(buffer, "PNG")
    return buffer.getvalue()


def test_detects_real_types_from_contents() -> None:
    assert _detect_type(_pdf_bytes()[:16]) == "application/pdf"
    assert _detect_type(_png_bytes()[:16]) == "image/png"
    assert _detect_type(b"\xff\xd8\xff\xe0rest-of-jpeg") == "image/jpeg"
    assert _detect_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert _detect_type(b"<html><script>") is None


@pytest.mark.asyncio
async def test_renders_first_page_of_pdf_and_images(tmp_path: Path) -> None:
    pdf, png = tmp_path / "in.pdf", tmp_path / "in.png"
    pdf.write_bytes(_pdf_bytes(3))
    png.write_bytes(_png_bytes())

    assert await _render_preview("pdf", pdf, tmp_path / "pdf.webp") == 3
    with Image.open(tmp_path / "pdf.webp") as preview:
        assert preview.format == "WEBP" and max(preview.size) <= 640

    assert await _render_preview("image", png, tmp_path / "png.webp") == 1
    with Image.open(tmp_path / "png.webp") as preview:
        assert preview.size == (640, 360)


@pytest.mark.asyncio
async def test_broken_file_yields_no_preview(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7 this is not really a pdf")
    assert await _render_preview("pdf", broken, tmp_path / "out.webp") is None


@pytest.mark.asyncio
async def test_local_storage_refuses_oversize_and_path_escape(tmp_path: Path) -> None:
    storage = LocalStorage(str(tmp_path))

    async def chunks(data: bytes):
        yield data

    assert await storage.write_stream("tenants/a/file", chunks(b"12345"), 5) == 5
    assert await storage.size("tenants/a/file") == 5
    with pytest.raises(ValueError):
        await storage.write_stream("tenants/a/big", chunks(b"123456"), 5)
    assert await storage.size("tenants/a/big") is None
    with pytest.raises(ValueError):
        storage.path("../outside")


@pytest.fixture
def moto_endpoint():
    moto_server = pytest.importorskip("moto.server")
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.mark.asyncio
async def test_s3_signed_form_upload_round_trip(
    moto_endpoint: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "testing")
    settings = Settings(
        STORAGE_BACKEND="s3",
        S3_BUCKET="transpire-test-files",
        S3_REGION="ap-south-1",
        S3_ENDPOINT_URL=moto_endpoint,
    )
    storage = S3Storage(settings)
    await asyncio.to_thread(
        storage.client.create_bucket,
        Bucket="transpire-test-files",
        CreateBucketConfiguration={"LocationConstraint": "ap-south-1"},
    )
    key = "tenants/t1/ideas/i1/files/f1/original"
    body = _pdf_bytes()
    form = await storage.upload_form(
        key=key, content_type="application/pdf", max_bytes=1024 * 1024, upload_path="unused"
    )
    assert form.target == "s3" and form.fields["Content-Type"] == "application/pdf"
    async with httpx.AsyncClient() as client:
        posted = await client.post(
            form.url, data=form.fields, files={"file": ("x.pdf", body, "application/pdf")}
        )
    assert posted.status_code in (200, 204), posted.text

    assert await storage.size(key) == len(body)
    await storage.download_to(key, tmp_path / "copy.pdf")
    assert (tmp_path / "copy.pdf").read_bytes() == body
    url = await storage.download_url(key, "Plant layout é.pdf", "application/pdf", inline=True)
    assert "response-content-disposition=inline" in url and "X-Amz-Signature" in url
    # Malware scanning: no tag yet → pending; GuardDuty's verdict decides after that.
    assert await storage.scan_status(key) == "pending"
    await asyncio.to_thread(
        storage.client.put_object_tagging,
        Bucket="transpire-test-files",
        Key=key,
        Tagging={"TagSet": [{"Key": "GuardDutyMalwareScanStatus", "Value": "NO_THREATS_FOUND"}]},
    )
    assert await storage.scan_status(key) == "clean"

    await storage.delete([key])
    assert await storage.size(key) is None


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        (None, "pending"),
        ("NO_THREATS_FOUND", "clean"),
        ("THREATS_FOUND", "blocked"),
        ("UNSUPPORTED", "blocked"),
        ("ACCESS_DENIED", "blocked"),
        ("FAILED", "blocked"),
    ],
)
def test_scan_tag_fails_closed(tag: str | None, expected: str) -> None:
    assert scan_status_from_tag(tag) == expected


def test_keys_honour_optional_prefix() -> None:
    ids = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    plain, _ = _keys(Settings(S3_PREFIX=""), *ids)
    assert plain.startswith("tenants/")
    prefixed, preview = _keys(Settings(S3_PREFIX="/qa/"), *ids)
    assert prefixed == f"qa/{plain}" and preview.startswith("qa/tenants/")


def test_platform_cap_is_five_mb_by_default() -> None:
    assert Settings(_env_file=None).attachment_max_file_mb == 5


def test_production_refuses_local_storage() -> None:
    with pytest.raises(ValueError, match="STORAGE_BACKEND=local"):
        Settings(
            ENVIRONMENT="production",
            SESSION_COOKIE_SECURE=True,
            OUTBOX_ENCRYPTION_KEY="x" * 44,
            STORAGE_BACKEND="local",
        )


if sys.platform == "win32":  # pragma: no cover
    pytest.skip("preview subprocess limits are POSIX-only", allow_module_level=True)
