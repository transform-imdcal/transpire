import os
import uuid
from datetime import UTC, date, datetime

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import session_factory
from app.core.tenant_context import apply_tenant_to_transaction
from app.domains.ideas.models import Idea, IdeaStatus, ProjectCharter
from app.domains.projects.controllers import (
    create_project_from_submitted_charter,
    open_project_for_approved_idea,
)
from app.main import app
from tests.test_idea_attachments_integration import PASSWORD, _invitation_token

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database",
)


async def _charter(tenant_id: uuid.UUID, idea_id: str, *, submitted: bool) -> None:
    async with session_factory() as session, session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        idea = await session.scalar(select(Idea).where(Idea.id == uuid.UUID(idea_id)))
        assert idea is not None
        idea.status = IdeaStatus.CHARTER_SUBMITTED if submitted else IdeaStatus.CHARTER_IN_PROGRESS
        charter = ProjectCharter(
            tenant_id=tenant_id,
            idea_id=idea.id,
            leader="Lata Lead",
            sponsor="Sam Sponsor",
            target_completion_date=date(2027, 3, 31),
            submitted_at=datetime.now(UTC) if submitted else None,
        )
        session.add(charter)
        await session.flush()
        if submitted:
            await create_project_from_submitted_charter(
                session,
                tenant_id=tenant_id,
                idea_id=idea.id,
                idea_reference=idea.reference,
                submitter_user_id=idea.submitter_user_id,
                charter=charter,
            )
        else:
            await open_project_for_approved_idea(
                session,
                tenant_id=tenant_id,
                idea_id=idea.id,
                idea_reference=idea.reference,
                charter=charter,
            )


@pytest.mark.asyncio
async def test_charters_register_lists_drafting_and_baselined_charters() -> None:
    settings = get_settings()
    suffix = uuid.uuid4().hex[:8]
    slug = f"charters-{suffix}"
    prefix = f"/api/v1/t/{slug}"
    owner_email = f"owner-{suffix}@example.com"
    transport = httpx.ASGITransport(app=app)

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url="http://localhost")

    async with client() as platform, client() as owner:
        dev_prefix = f"/api/v1/t/{settings.development_tenant_slug}"
        await platform.post(
            f"{dev_prefix}/auth/sign-in",
            json={
                "email": str(settings.development_admin_email),
                "password": settings.development_admin_password.get_secret_value(),
            },
        )
        provisioned = await platform.post(
            f"{dev_prefix}/platform/tenants",
            json={
                "slug": slug,
                "name": f"Charters {suffix}",
                "first_admin_email": owner_email,
                "first_admin_name": "Charter Owner",
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
        assert accepted.status_code == 200, accepted.text
        signed_in = await owner.post(
            f"{prefix}/auth/sign-in", json={"email": owner_email, "password": PASSWORD}
        )
        assert signed_in.status_code == 200
        h = {"X-CSRF-Token": owner.cookies.get(settings.csrf_cookie_name) or ""}

        assert (await owner.get(f"{prefix}/projects/charters")).json() == []

        drafting = await owner.post(f"{prefix}/ideas", json={"title": "Drafting one"}, headers=h)
        baselined = await owner.post(f"{prefix}/ideas", json={"title": "Baselined one"}, headers=h)
        await _charter(tenant_id, drafting.json()["id"], submitted=False)
        await _charter(tenant_id, baselined.json()["id"], submitted=True)

        listed = await owner.get(f"{prefix}/projects/charters")
        assert listed.status_code == 200, listed.text
        by_title = {entry["title"]: entry for entry in listed.json()}
        assert set(by_title) == {"Drafting one", "Baselined one"}
        draft = by_title["Drafting one"]
        assert draft["charter_status"] == "drafting"
        assert draft["project_status"] == "charter_in_progress"
        assert draft["my_role"] == "owner" and draft["can_edit"] is True
        assert draft["leader_name"] == "Lata Lead" and draft["sponsor_name"] == "Sam Sponsor"
        assert draft["owner_name"] == "Charter Owner"
        assert draft["target_completion_date"] == "2027-03-31"
        done = by_title["Baselined one"]
        assert done["charter_status"] == "baselined" and done["can_edit"] is False
        assert done["project_reference"].startswith("PRJ-")

        # The register is per workspace: another workspace sees none of these.
        foreign = await platform.get(f"{dev_prefix}/projects/charters")
        assert foreign.status_code == 200
        assert not {entry["title"] for entry in foreign.json()} & set(by_title)
