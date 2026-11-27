import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import session_factory
from app.core.tenant_context import apply_tenant_to_transaction
from app.domains.communications.models import EmailDelivery
from app.domains.ideas.leaderboard import _period, financial_year_of
from app.domains.ideas.models import Idea, IdeaStatus
from app.main import app
from tests.test_idea_attachments_integration import PASSWORD, _invitation_token

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database",
)


async def _submit(tenant_id: uuid.UUID, idea_id: str, saving: int) -> None:
    async with session_factory() as session, session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        idea = await session.scalar(select(Idea).where(Idea.id == uuid.UUID(idea_id)))
        assert idea is not None
        idea.status = IdeaStatus.SUBMITTED
        idea.submitted_at = datetime.now(UTC)
        idea.estimated_annual_saving = Decimal(saving)


@pytest.mark.asyncio
async def test_capex_endorsements_and_leaderboard() -> None:
    settings = get_settings()
    suffix = uuid.uuid4().hex[:8]
    slug = f"endorse-{suffix}"
    prefix = f"/api/v1/t/{slug}"
    owner_email = f"owner-{suffix}@example.com"
    friend_email = f"friend-{suffix}@example.com"
    transport = httpx.ASGITransport(app=app)

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url="http://localhost")

    async with client() as platform, client() as owner, client() as friend:
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
                "name": f"Endorse {suffix}",
                "first_admin_email": owner_email,
                "first_admin_name": "Olivia Owner",
            },
            headers={"X-CSRF-Token": platform.cookies.get(settings.csrf_cookie_name) or ""},
        )
        assert provisioned.status_code == 201, provisioned.text
        tenant_id = uuid.UUID(provisioned.json()["id"])

        async def join(person: httpx.AsyncClient, email: str) -> dict[str, str]:
            accepted = await person.post(
                "/api/v1/auth/invitations/accept",
                json={"token": await _invitation_token(tenant_id, email), "new_password": PASSWORD},
            )
            assert accepted.status_code == 200, accepted.text
            signed = await person.post(
                f"{prefix}/auth/sign-in", json={"email": email, "password": PASSWORD}
            )
            assert signed.status_code == 200, signed.text
            return {"X-CSRF-Token": person.cookies.get(settings.csrf_cookie_name) or ""}

        oh = await join(owner, owner_email)
        invited = await owner.post(
            f"{prefix}/admin/invitations",
            json={
                "email": friend_email,
                "display_name": "Farah Friend",
                "role_key": "idea_submitter",
            },
            headers=oh,
        )
        assert invited.status_code in (200, 201, 202), invited.text
        fh = await join(friend, friend_email)

        # CAPEX sub-categories come from the workspace register, seeded from the URS.
        catalog = (await owner.get(f"{prefix}/ideas/submission-catalog")).json()
        capex = [item["name"] for item in catalog["capex_subtypes"]]
        assert "Capacity Addition" in capex and len(capex) == 16
        bad = await owner.post(
            f"{prefix}/ideas",
            json={
                "idea_type": "project",
                "project_category": "Capex Project",
                "project_subtype": "Replacement",
            },
            headers=oh,
        )
        assert bad.status_code == 409 and "CAPEX" in bad.json()["detail"], bad.text
        good = await owner.post(
            f"{prefix}/ideas",
            json={
                "title": "New filling line",
                "idea_type": "project",
                "project_category": "Capex Project",
                "project_subtype": "Capacity Addition",
            },
            headers=oh,
        )
        assert good.status_code in (200, 201), good.text
        idea_id = good.json()["id"]

        # Drafts can't collect endorsements.
        friend_id = next(
            row["user_id"]
            for row in (await owner.get(f"{prefix}/projects/team-candidates")).json()
            if row["email"] == friend_email
        )
        early = await owner.post(
            f"{prefix}/ideas/{idea_id}/endorsements", json={"user_ids": [friend_id]}, headers=oh
        )
        assert early.status_code == 409 and "Submit the idea first" in early.json()["detail"]

        await _submit(tenant_id, idea_id, saving=250000)
        asked = await owner.post(
            f"{prefix}/ideas/{idea_id}/endorsements",
            json={"user_ids": [friend_id], "message": "You run this line."},
            headers=oh,
        )
        assert asked.status_code == 201 and asked.json() == {"asked": 1}
        again = await owner.post(
            f"{prefix}/ideas/{idea_id}/endorsements", json={"user_ids": [friend_id]}, headers=oh
        )
        assert again.status_code == 409
        async with session_factory() as session, session.begin():
            await apply_tenant_to_transaction(session, tenant_id)
            mail = await session.scalar(
                select(EmailDelivery).where(
                    EmailDelivery.recipient == friend_email,
                    EmailDelivery.template_key == "endorsement_request",
                )
            )
        assert mail is not None

        inbox = (await friend.get(f"{prefix}/ideas/home-summary")).json()
        [request] = inbox["endorsement_requests"]
        assert request["idea_id"] == idea_id and request["message"] == "You run this line."

        # Only the person asked can answer.
        hijack = await owner.post(
            f"{prefix}/ideas/{idea_id}/endorsements/{request['id']}/respond",
            json={"decision": "endorse"},
            headers=oh,
        )
        assert hijack.status_code == 403
        answered = await friend.post(
            f"{prefix}/ideas/{idea_id}/endorsements/{request['id']}/respond",
            json={"decision": "endorse", "comment": "Saves us a shift a week."},
            headers=fh,
        )
        assert answered.status_code == 204, answered.text
        panel = (await owner.get(f"{prefix}/ideas/{idea_id}/endorsements")).json()
        assert panel["endorsed_count"] == 1 and panel["can_request"] is True
        assert panel["items"][0]["comment"] == "Saves us a shift a week."
        assert (await friend.get(f"{prefix}/ideas/home-summary")).json()[
            "endorsement_requests"
        ] == []

        # Leaderboard: owner ranks by the idea's estimate (no validated benefit yet).
        board = (await owner.get(f"{prefix}/ideas/leaderboard")).json()
        assert board["shows_savings"] is True and board["ranked_by"] == "savings"
        top = board["employees"][0]
        assert top["name"] == "Olivia Owner" and top["is_me"] is True
        assert top["submitted"] == 1 and Decimal(top["savings"]) == Decimal("2.50")
        assert board["sites"][0]["name"] == "Site not recorded"
        kaizen_only = (
            await owner.get(f"{prefix}/ideas/leaderboard", params={"idea_type": "kaizen"})
        ).json()
        assert kaizen_only["employees"] == []
        # Contributors follow the Idea Bank's financials policy (off by default).
        hidden = (await friend.get(f"{prefix}/ideas/leaderboard")).json()
        assert hidden["shows_savings"] is False and hidden["employees"][0]["savings"] is None


def test_financial_year_periods() -> None:
    assert financial_year_of(date(2027, 3, 31)) == 2026
    assert financial_year_of(date(2026, 4, 1)) == 2026
    start, end, label = _period(2026, "q4")
    assert (start.date().isoformat(), end.date().isoformat()) == ("2027-01-01", "2027-04-01")
    assert label == "FY 2026–27 · Q4 (Jan–Mar)"
    start, end, _ = _period(2026, "all")
    assert (start.date().isoformat(), end.date().isoformat()) == ("2026-04-01", "2027-04-01")
