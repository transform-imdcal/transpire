"""Endorsements: an idea's owner asks colleagues to back it.

Endorsements are a visible signal of support for reviewers. They never block, skip or
change the approval route. An idea can collect them once it is visible in the Idea Bank.
"""

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.tenant_context import apply_tenant_to_transaction
from app.core.urls import preferences_link, tenant_frontend_url
from app.domains.audit.controllers import record_audit_event
from app.domains.communications.controllers import enqueue_email
from app.domains.communications.schemas import EmailMessage
from app.domains.ideas.approval_notifications import IDEA_PREFERENCE, wants_email
from app.domains.ideas.controllers import BANK_STATUSES, can_manage_idea
from app.domains.ideas.models import Idea, IdeaApprovalStage, IdeaEndorsement
from app.domains.identity.models import Membership, MembershipStatus, User
from app.domains.identity.queries import contributing_membership
from app.domains.tenants.models import Tenant

MAX_ENDORSEMENT_REQUESTS = 20


class EndorsementNotFoundError(Exception):
    pass


class EndorsementAccessError(Exception):
    pass


class EndorsementRuleError(Exception):
    """A rule the person can act on; the message is shown to them."""


class EndorsementRequestCommand(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_ENDORSEMENT_REQUESTS)
    message: str = Field(default="", max_length=500)


class EndorsementResponseCommand(BaseModel):
    decision: Literal["endorse", "decline"]
    comment: str = Field(default="", max_length=600)


class EndorsementRecord(BaseModel):
    id: uuid.UUID
    endorser_user_id: uuid.UUID
    endorser_name: str
    status: Literal["requested", "endorsed", "declined"]
    message: str
    comment: str
    requested_at: datetime
    responded_at: datetime | None
    is_me: bool


class EndorsementPanel(BaseModel):
    endorsed_count: int
    can_request: bool
    # Why requesting isn't possible right now, for the owner; None otherwise.
    request_blocked_reason: str | None
    items: list[EndorsementRecord]


class EndorsementRequestItem(BaseModel):
    """An endorsement waiting on the viewer, for their Inbox."""

    id: uuid.UUID
    idea_id: uuid.UUID
    idea_reference: str
    idea_title: str
    owner_name: str
    message: str
    requested_at: datetime


async def _idea(session: AsyncSession, tenant_id: uuid.UUID, idea_id: uuid.UUID) -> Idea:
    idea = await session.scalar(select(Idea).where(Idea.tenant_id == tenant_id, Idea.id == idea_id))
    if idea is None:
        raise EndorsementNotFoundError
    return idea


async def _can_see(session: AsyncSession, idea: Idea, user_id: uuid.UUID, roles: list[str]) -> bool:
    if idea.status in BANK_STATUSES or idea.submitter_user_id == user_id:
        return True
    stages = list(
        (
            await session.scalars(
                select(IdeaApprovalStage).where(
                    IdeaApprovalStage.tenant_id == idea.tenant_id,
                    IdeaApprovalStage.idea_id == idea.id,
                )
            )
        ).all()
    )
    return can_manage_idea(idea, stages, user_id, roles)


def _blocked_reason(idea: Idea) -> str | None:
    if idea.status in BANK_STATUSES:
        return None
    if idea.status.value == "draft":
        return "Submit the idea first. Colleagues can endorse it once it's in the Idea Bank."
    if idea.status.value == "needs_correction":
        return "Resubmit your correction first, then ask colleagues to endorse it."
    return "This idea can no longer collect endorsements."


async def get_endorsements(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: list[str],
    idea_id: uuid.UUID,
    session: AsyncSession,
) -> EndorsementPanel:
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        idea = await _idea(session, tenant_id, idea_id)
        if not await _can_see(session, idea, user_id, roles):
            raise EndorsementAccessError
        is_owner = idea.submitter_user_id == user_id
        rows = (
            await session.execute(
                select(IdeaEndorsement, User.display_name)
                .join(User, User.id == IdeaEndorsement.endorser_user_id)
                .where(IdeaEndorsement.tenant_id == tenant_id, IdeaEndorsement.idea_id == idea_id)
                .order_by(
                    IdeaEndorsement.responded_at.desc().nulls_last(),
                    IdeaEndorsement.created_at.desc(),
                )
            )
        ).all()
        items = [
            EndorsementRecord(
                id=endorsement.id,
                endorser_user_id=endorsement.endorser_user_id,
                endorser_name=name,
                status=endorsement.status,
                message=endorsement.message
                if (is_owner or endorsement.endorser_user_id == user_id)
                else "",
                comment=endorsement.comment,
                requested_at=endorsement.created_at,
                responded_at=endorsement.responded_at,
                is_me=endorsement.endorser_user_id == user_id,
            )
            for endorsement, name in rows
            # Everyone sees who endorsed. Pending and declined requests stay between the
            # owner and the person asked.
            if endorsement.status == "endorsed"
            or is_owner
            or endorsement.endorser_user_id == user_id
        ]
        return EndorsementPanel(
            endorsed_count=sum(1 for endorsement, _ in rows if endorsement.status == "endorsed"),
            can_request=is_owner and idea.status in BANK_STATUSES,
            request_blocked_reason=_blocked_reason(idea) if is_owner else None,
            items=items,
        )


async def request_endorsements(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    idea_id: uuid.UUID,
    command: EndorsementRequestCommand,
    settings: Settings,
    session: AsyncSession,
) -> int:
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        idea = await _idea(session, tenant_id, idea_id)
        if idea.submitter_user_id != user_id:
            raise EndorsementAccessError
        reason = _blocked_reason(idea)
        if reason:
            raise EndorsementRuleError(reason)
        wanted = {target for target in command.user_ids if target != user_id}
        if not wanted:
            raise EndorsementRuleError("Choose at least one colleague other than yourself.")
        existing = set(
            (
                await session.scalars(
                    select(IdeaEndorsement.endorser_user_id).where(
                        IdeaEndorsement.tenant_id == tenant_id, IdeaEndorsement.idea_id == idea_id
                    )
                )
            ).all()
        )
        wanted -= existing
        if not wanted:
            raise EndorsementRuleError("You've already asked everyone you chose.")
        if len(existing) + len(wanted) > MAX_ENDORSEMENT_REQUESTS:
            raise EndorsementRuleError(
                f"An idea can ask at most {MAX_ENDORSEMENT_REQUESTS} colleagues to endorse it."
            )
        # Only active colleagues who can contribute (not Viewers) can endorse.
        people = list(
            (
                await session.scalars(
                    select(User)
                    .join(Membership, Membership.user_id == User.id)
                    .where(
                        Membership.tenant_id == tenant_id,
                        Membership.status == MembershipStatus.ACTIVE,
                        contributing_membership(Membership.id, tenant_id),
                        User.id.in_(wanted),
                    )
                )
            ).all()
        )
        if len(people) != len(wanted):
            raise EndorsementRuleError("Some of the people you chose can't endorse ideas here.")
        tenant = await session.get(Tenant, tenant_id)
        owner = await session.get(User, user_id)
        assert tenant is not None and owner is not None
        message = command.message.strip()
        for person in people:
            session.add(
                IdeaEndorsement(
                    tenant_id=tenant_id,
                    idea_id=idea_id,
                    endorser_user_id=person.id,
                    requested_by_user_id=user_id,
                    status="requested",
                    message=message,
                )
            )
            if wants_email(person, IDEA_PREFERENCE):
                enqueue_email(
                    session,
                    EmailMessage(
                        tenant_id=tenant_id,
                        recipient=person.email,
                        template_key="endorsement_request",
                        subject=f"Will you endorse {idea.reference}? {idea.title}",
                        template_data={
                            "recipient_name": person.display_name,
                            "tenant_name": tenant.name,
                            "owner_name": owner.display_name,
                            "idea_reference": idea.reference,
                            "idea_title": idea.title,
                            "problem_statement": idea.problem_statement or "Not provided",
                            "owner_message": message or "No message added.",
                            "action_url": tenant_frontend_url(
                                settings, tenant.slug, f"/ideas/{idea.id}/summary"
                            ),
                            "action_label": "Read and respond",
                            **preferences_link(settings, tenant.slug),
                        },
                    ),
                    settings,
                )
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_user_id=user_id,
            subject_user_id=user_id,
            event_type="idea.endorsement_requested",
            entity_type="idea",
            entity_id=idea.id,
            entity_reference=idea.reference,
            summary=f"Asked {len(people)} colleague(s) to endorse {idea.reference}.",
            details={"endorser_user_ids": sorted(str(person.id) for person in people)},
        )
        return len(people)


async def respond_to_endorsement(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    idea_id: uuid.UUID,
    endorsement_id: uuid.UUID,
    command: EndorsementResponseCommand,
    session: AsyncSession,
) -> None:
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        idea = await _idea(session, tenant_id, idea_id)
        endorsement = await session.scalar(
            select(IdeaEndorsement)
            .where(
                IdeaEndorsement.tenant_id == tenant_id,
                IdeaEndorsement.idea_id == idea_id,
                IdeaEndorsement.id == endorsement_id,
            )
            .with_for_update()
        )
        if endorsement is None:
            raise EndorsementNotFoundError
        if endorsement.endorser_user_id != user_id:
            raise EndorsementAccessError
        if idea.status not in BANK_STATUSES:
            raise EndorsementRuleError("This idea isn't collecting endorsements right now.")
        # People can change their mind; the latest answer stands.
        endorsement.status = "endorsed" if command.decision == "endorse" else "declined"
        endorsement.comment = command.comment.strip()
        endorsement.responded_at = datetime.now(UTC)
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_user_id=user_id,
            subject_user_id=idea.submitter_user_id,
            event_type=f"idea.endorsement_{endorsement.status}",
            entity_type="idea",
            entity_id=idea.id,
            entity_reference=idea.reference,
            summary=f"{'Endorsed' if endorsement.status == 'endorsed' else 'Declined to endorse'} "
            f"{idea.reference}.",
            details={"endorsement_id": str(endorsement.id)},
        )


async def withdraw_endorsement_request(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    idea_id: uuid.UUID,
    endorsement_id: uuid.UUID,
    session: AsyncSession,
) -> None:
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        idea = await _idea(session, tenant_id, idea_id)
        endorsement = await session.scalar(
            select(IdeaEndorsement).where(
                IdeaEndorsement.tenant_id == tenant_id,
                IdeaEndorsement.idea_id == idea_id,
                IdeaEndorsement.id == endorsement_id,
            )
        )
        if endorsement is None:
            raise EndorsementNotFoundError
        if idea.submitter_user_id != user_id:
            raise EndorsementAccessError
        if endorsement.status != "requested":
            raise EndorsementRuleError("Only requests that haven't been answered can be withdrawn.")
        await session.delete(endorsement)


async def pending_endorsement_requests(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> list[EndorsementRequestItem]:
    """For the Inbox: requests waiting on this person, on ideas still collecting them."""
    owner = User.__table__.alias("owner")
    rows = (
        await session.execute(
            select(IdeaEndorsement, Idea.reference, Idea.title, owner.c.display_name)
            .join(Idea, Idea.id == IdeaEndorsement.idea_id)
            .join(owner, owner.c.id == Idea.submitter_user_id)
            .where(
                IdeaEndorsement.tenant_id == tenant_id,
                IdeaEndorsement.endorser_user_id == user_id,
                IdeaEndorsement.status == "requested",
                Idea.status.in_(BANK_STATUSES),
            )
            .order_by(IdeaEndorsement.created_at)
        )
    ).all()
    return [
        EndorsementRequestItem(
            id=endorsement.id,
            idea_id=endorsement.idea_id,
            idea_reference=reference,
            idea_title=title,
            owner_name=owner_name,
            message=endorsement.message,
            requested_at=endorsement.created_at,
        )
        for endorsement, reference, title, owner_name in rows
    ]


async def endorsement_counts(
    session: AsyncSession, tenant_id: uuid.UUID, idea_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not idea_ids:
        return {}
    return dict(
        (
            await session.execute(
                select(IdeaEndorsement.idea_id, func.count())
                .where(
                    IdeaEndorsement.tenant_id == tenant_id,
                    IdeaEndorsement.idea_id.in_(idea_ids),
                    IdeaEndorsement.status == "endorsed",
                )
                .group_by(IdeaEndorsement.idea_id)
            )
        ).all()
    )
