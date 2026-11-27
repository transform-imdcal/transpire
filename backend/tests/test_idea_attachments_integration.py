import io
import os
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import session_factory
from app.core.tenant_context import apply_tenant_to_transaction
from app.domains.communications.models import EmailDelivery
from app.domains.communications.security import decrypt_template_data
from app.domains.files.controllers import purge_abandoned_uploads
from app.domains.files.models import StoredFile
from app.main import app

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database",
)

PASSWORD = "Secure-attachments#2026"


def _pdf(pages: int) -> bytes:
    images = [Image.new("RGB", (595, 842), "white") for _ in range(pages)]
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), "green").save(buffer, "PNG")
    return buffer.getvalue()


async def _invitation_token(tenant_id: uuid.UUID, email: str) -> str:
    async with session_factory() as session, session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        delivery = await session.scalar(
            select(EmailDelivery)
            .where(EmailDelivery.tenant_id == tenant_id, EmailDelivery.recipient == email)
            .order_by(EmailDelivery.created_at.desc())
            .limit(1)
        )
    assert delivery is not None and delivery.encrypted_template_data is not None
    data = decrypt_template_data(delivery.encrypted_template_data, get_settings())
    return parse_qs(urlparse(str(data["action_url"])).query)["token"][0]


async def _upload(client, prefix, headers, idea_id, name, body, content_type):
    intent = await client.post(
        f"{prefix}/ideas/{idea_id}/attachments",
        json={"file_name": name, "size_bytes": len(body), "content_type": content_type},
        headers=headers,
    )
    if intent.status_code != 201:
        return intent
    upload = intent.json()["upload"]
    assert upload["target"] == "api"
    sent = await client.put(
        f"{prefix}{upload['url']}",
        content=body,
        headers={**headers, "Content-Type": content_type},
    )
    assert sent.status_code == 204, sent.text
    return await client.post(
        f"{prefix}/ideas/{idea_id}/attachments/{intent.json()['attachment']['id']}/complete",
        headers=headers,
    )


@pytest.mark.asyncio
async def test_idea_attachment_lifecycle(tmp_path: Path) -> None:
    settings = get_settings()
    settings.storage_backend = "local"
    settings.storage_local_dir = str(tmp_path)
    suffix = uuid.uuid4().hex[:8]
    slug = f"files-{suffix}"
    prefix = f"/api/v1/t/{slug}"
    owner_email = f"owner-{suffix}@example.com"
    transport = httpx.ASGITransport(app=app)

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url="http://localhost")

    async with client() as platform:
        dev_prefix = f"/api/v1/t/{settings.development_tenant_slug}"
        await platform.post(
            f"{dev_prefix}/auth/sign-in",
            json={
                "email": str(settings.development_admin_email),
                "password": settings.development_admin_password.get_secret_value(),
            },
        )
        platform_h = {"X-CSRF-Token": platform.cookies.get(settings.csrf_cookie_name) or ""}
        provisioned = await platform.post(
            f"{dev_prefix}/platform/tenants",
            json={
                "slug": slug,
                "name": f"Files {suffix}",
                "first_admin_email": owner_email,
                "first_admin_name": "Files Owner",
            },
            headers=platform_h,
        )
        assert provisioned.status_code == 201, provisioned.text
        tenant_id = uuid.UUID(provisioned.json()["id"])

        async with client() as owner:
            accepted = await owner.post(
                "/api/v1/auth/invitations/accept",
                json={
                    "token": await _invitation_token(tenant_id, owner_email),
                    "new_password": PASSWORD,
                },
            )
            assert accepted.status_code == 200, accepted.text
            assert (
                await owner.post(
                    f"{prefix}/auth/sign-in", json={"email": owner_email, "password": PASSWORD}
                )
            ).status_code == 200
            h = {"X-CSRF-Token": owner.cookies.get(settings.csrf_cookie_name) or ""}

            draft = await owner.post(
                f"{prefix}/ideas", json={"title": "Faster changeover"}, headers=h
            )
            assert draft.status_code in (200, 201), draft.text
            idea_id = draft.json()["id"]

            listing = await owner.get(f"{prefix}/ideas/{idea_id}/attachments")
            assert listing.status_code == 200, listing.text
            assert listing.json()["can_edit"] is True
            assert listing.json()["policy"]["accepted_types"] == [
                "application/pdf",
                "image/png",
                "image/jpeg",
                "image/webp",
            ]

            # A real PDF: verified, first page rendered, pages counted.
            pdf = await _upload(
                owner, prefix, h, idea_id, "Line 4 baseline.pdf", _pdf(2), "application/pdf"
            )
            assert pdf.status_code == 200, pdf.text
            assert pdf.json()["status"] == "ready"
            assert pdf.json()["page_count"] == 2 and pdf.json()["has_preview"] is True
            pdf_id = pdf.json()["id"]

            preview = await owner.get(f"{prefix}/ideas/{idea_id}/attachments/{pdf_id}/preview")
            assert preview.status_code == 200 and preview.headers["content-type"] == "image/webp"
            content = await owner.get(f"{prefix}/ideas/{idea_id}/attachments/{pdf_id}/download")
            assert content.status_code == 200 and content.content.startswith(b"%PDF-")
            assert content.headers["content-disposition"].startswith("attachment;")

            image = await _upload(owner, prefix, h, idea_id, "photo.png", _png(), "image/png")
            assert image.status_code == 200 and image.json()["has_preview"] is True

            # Contents that lie about their type are refused and leave nothing behind.
            spoofed = await _upload(
                owner, prefix, h, idea_id, "fake.pdf", _png(), "application/pdf"
            )
            assert spoofed.status_code == 422, spoofed.text
            unsupported = await _upload(
                owner, prefix, h, idea_id, "notes.txt", b"hello", "text/plain"
            )
            assert unsupported.status_code == 422
            names = [
                item["file_name"]
                for item in (await owner.get(f"{prefix}/ideas/{idea_id}/attachments")).json()[
                    "items"
                ]
            ]
            assert names == ["Line 4 baseline.pdf", "photo.png"]

            # Announcing a small file and sending a bigger one is refused by the upload itself.
            intent = await owner.post(
                f"{prefix}/ideas/{idea_id}/attachments",
                json={"file_name": "a.pdf", "size_bytes": 10, "content_type": "application/pdf"},
                headers=h,
            )
            assert intent.status_code == 201
            oversized = await owner.put(
                f"{prefix}{intent.json()['upload']['url']}", content=_pdf(1), headers=h
            )
            assert oversized.status_code == 422
            await owner.delete(
                f"{prefix}/ideas/{idea_id}/attachments/{intent.json()['attachment']['id']}",
                headers=h,
            )

            # Workspace policy is capped by the platform and enforced on upload.
            too_high = await owner.put(
                f"{prefix}/admin/configuration/attachment-policy",
                json={"max_files": 999, "max_file_mb": 5, "allow_pdf": True, "allow_images": True},
                headers=h,
            )
            assert too_high.status_code == 422
            tightened = await owner.put(
                f"{prefix}/admin/configuration/attachment-policy",
                json={"max_files": 2, "max_file_mb": 5, "allow_pdf": True, "allow_images": False},
                headers=h,
            )
            assert tightened.status_code == 200, tightened.text
            assert tightened.json()["accepted_types"] == ["application/pdf"]
            third = await _upload(
                owner, prefix, h, idea_id, "third.pdf", _pdf(1), "application/pdf"
            )
            assert third.status_code == 422 and "at most 2" in third.json()["detail"]

            removed = await owner.delete(
                f"{prefix}/ideas/{idea_id}/attachments/{pdf_id}", headers=h
            )
            assert removed.status_code == 204
            gone = await owner.get(f"{prefix}/ideas/{idea_id}/attachments/{pdf_id}/content")
            assert gone.status_code == 404

        # Another workspace's admin cannot reach these files, even by guessing IDs.
        foreign = await platform.get(f"{dev_prefix}/ideas/{idea_id}/attachments")
        assert foreign.status_code == 404

    async with session_factory() as session:
        assert await purge_abandoned_uploads(tenant_id, settings, session) == 0
    async with session_factory() as session, session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        files = (
            await session.scalars(select(StoredFile).where(StoredFile.tenant_id == tenant_id))
        ).all()
    assert [stored.original_name for stored in files] == ["photo.png"]
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == [
        "original",
        "preview.webp",
    ]
