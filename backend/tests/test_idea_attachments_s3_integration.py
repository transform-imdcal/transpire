"""The S3 path end to end against a local mock S3: signed POST upload, the 5 MB limit,
the switchable GuardDuty gate, and files removed by the bucket's retention rule."""

import asyncio
import os
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.core.storage import get_storage
from app.main import app
from tests.test_idea_attachments_integration import PASSWORD, _invitation_token, _pdf

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database",
)


@pytest.fixture
def s3_settings(monkeypatch: pytest.MonkeyPatch):
    moto_server = pytest.importorskip("moto.server")
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "testing")
    settings = get_settings()
    original = {
        name: getattr(settings, name)
        for name in (
            "storage_backend",
            "storage_bucket",
            "storage_endpoint_url",
            "storage_prefix",
            "storage_require_malware_scan",
        )
    }
    settings.storage_backend = "s3"
    settings.storage_bucket = f"transpire-test-{uuid.uuid4().hex[:8]}"
    settings.storage_endpoint_url = f"http://{host}:{port}"
    settings.storage_prefix = ""
    settings.storage_require_malware_scan = True
    storage = get_storage(settings)
    storage.client.create_bucket(
        Bucket=settings.storage_bucket,
        CreateBucketConfiguration={"LocationConstraint": settings.storage_region},
    )
    yield settings, storage
    for name, value in original.items():
        setattr(settings, name, value)
    server.stop()


def _tag(storage, key: str, value: str) -> None:
    storage.client.put_object_tagging(
        Bucket=storage.bucket,
        Key=key,
        Tagging={"TagSet": [{"Key": "GuardDutyMalwareScanStatus", "Value": value}]},
    )


@pytest.mark.asyncio
async def test_s3_upload_scan_gate_and_retention(s3_settings) -> None:
    settings, storage = s3_settings
    suffix = uuid.uuid4().hex[:8]
    slug = f"s3files-{suffix}"
    prefix = f"/api/v1/t/{slug}"
    owner_email = f"owner-{suffix}@example.com"
    transport = httpx.ASGITransport(app=app)

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url="http://localhost")

    async with client() as platform, client() as owner, httpx.AsyncClient() as browser:
        dev = f"/api/v1/t/{settings.development_tenant_slug}"
        await platform.post(
            f"{dev}/auth/sign-in",
            json={
                "email": str(settings.development_admin_email),
                "password": settings.development_admin_password.get_secret_value(),
            },
        )
        provisioned = await platform.post(
            f"{dev}/platform/tenants",
            json={
                "slug": slug,
                "name": f"S3 {suffix}",
                "first_admin_email": owner_email,
                "first_admin_name": "S3 Owner",
            },
            headers={"X-CSRF-Token": platform.cookies.get(settings.csrf_cookie_name) or ""},
        )
        assert provisioned.status_code == 201, provisioned.text
        tenant_id = uuid.UUID(provisioned.json()["id"])
        accepted = await owner.post(
            "/api/v1/auth/invitations/accept",
            json={
                "token": await _invitation_token(tenant_id, owner_email),
                "new_password": PASSWORD,
            },
        )
        assert accepted.status_code == 200
        await owner.post(
            f"{prefix}/auth/sign-in", json={"email": owner_email, "password": PASSWORD}
        )
        h = {"X-CSRF-Token": owner.cookies.get(settings.csrf_cookie_name) or ""}
        idea_id = (
            await owner.post(f"{prefix}/ideas", json={"title": "S3 path"}, headers=h)
        ).json()["id"]

        # Over the 5 MB platform cap: refused before any upload form is issued.
        too_big = await owner.post(
            f"{prefix}/ideas/{idea_id}/attachments",
            json={
                "file_name": "big.pdf",
                "size_bytes": 6 * 1024 * 1024,
                "content_type": "application/pdf",
            },
            headers=h,
        )
        assert too_big.status_code == 422 and "5 MB" in too_big.json()["detail"]

        body = _pdf(2)
        intent = await owner.post(
            f"{prefix}/ideas/{idea_id}/attachments",
            json={
                "file_name": "Study.pdf",
                "size_bytes": len(body),
                "content_type": "application/pdf",
            },
            headers=h,
        )
        assert intent.status_code == 201, intent.text
        upload = intent.json()["upload"]
        assert upload["target"] == "s3"
        conditions = upload["fields"]["policy"]
        assert conditions  # signed policy carries the content-length-range condition
        posted = await browser.post(
            upload["url"],
            data=upload["fields"],
            files={"file": ("Study.pdf", body, "application/pdf")},
        )
        assert posted.status_code in (200, 204), posted.text
        attachment_id = intent.json()["attachment"]["id"]
        done = await owner.post(
            f"{prefix}/ideas/{idea_id}/attachments/{attachment_id}/complete", headers=h
        )
        assert done.status_code == 200, done.text
        assert done.json()["page_count"] == 2 and done.json()["has_preview"] is True

        keys = sorted(
            entry["Key"]
            for entry in storage.client.list_objects_v2(Bucket=storage.bucket)["Contents"]
        )
        assert len(keys) == 2 and all(key.startswith(f"tenants/{tenant_id}/") for key in keys)
        original = next(key for key in keys if key.endswith("/original"))

        content = f"{prefix}/ideas/{idea_id}/attachments/{attachment_id}/content"
        preview = f"{prefix}/ideas/{idea_id}/attachments/{attachment_id}/preview"
        listing = f"{prefix}/ideas/{idea_id}/attachments"

        # Not scanned yet: listed as pending, nothing can be opened, preview included.
        assert (await owner.get(listing)).json()["items"][0]["scan_status"] == "pending"
        assert (await owner.get(content)).status_code == 409
        assert (await owner.get(preview)).status_code == 409

        _tag(storage, original, "NO_THREATS_FOUND")
        assert (await owner.get(listing)).json()["items"][0]["scan_status"] == "clean"
        opened = await owner.get(content)
        assert opened.status_code == 307 and "X-Amz-Signature" in opened.headers["location"]
        fetched = await browser.get(opened.headers["location"])
        assert fetched.status_code == 200 and fetched.content == body

        _tag(storage, original, "THREATS_FOUND")
        assert (await owner.get(content)).status_code == 403
        assert (await owner.get(listing)).json()["items"][0]["scan_status"] == "blocked"

        # Scanning switched off by setting only: no code change, files open again.
        settings.storage_require_malware_scan = False
        assert (await owner.get(content)).status_code == 307
        assert (await owner.get(listing)).json()["items"][0]["scan_status"] == "not_required"

        # The bucket's 3-year retention removed the object: a clear 410, not a broken link.
        await asyncio.to_thread(storage.client.delete_object, Bucket=storage.bucket, Key=original)
        gone = await owner.get(content)
        assert gone.status_code == 410 and "three years" in gone.json()["detail"]
