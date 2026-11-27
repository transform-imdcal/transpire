"""The Charters register: every project charter in the workspace, drafting or baselined."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.tenant_context import apply_tenant_to_transaction
from app.domains.configuration.models import ConfigurationItem
from app.domains.ideas.models import Idea, ProjectCharter
from app.domains.identity.models import User
from app.domains.projects.models import Project, ProjectTeamMember
from app.domains.projects.schemas import CharterListEntry

ACTIVE_TEAM_STATUSES = ("invited", "accepted")


async def list_charters(
    tenant_id: uuid.UUID, user_id: uuid.UUID, session: AsyncSession
) -> list[CharterListEntry]:
    site = aliased(ConfigurationItem)
    department = aliased(ConfigurationItem)
    owner = aliased(User)
    leader = aliased(User)
    sponsor = aliased(User)
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        team_sizes = (
            select(
                ProjectTeamMember.project_id.label("project_id"),
                func.count(ProjectTeamMember.id).label("team_size"),
            )
            .where(
                ProjectTeamMember.tenant_id == tenant_id,
                ProjectTeamMember.status.in_(ACTIVE_TEAM_STATUSES),
            )
            .group_by(ProjectTeamMember.project_id)
            .subquery()
        )
        my_teams = set(
            (
                await session.scalars(
                    select(ProjectTeamMember.project_id).where(
                        ProjectTeamMember.tenant_id == tenant_id,
                        ProjectTeamMember.user_id == user_id,
                        ProjectTeamMember.status.in_(ACTIVE_TEAM_STATUSES),
                    )
                )
            ).all()
        )
        rows = (
            await session.execute(
                select(
                    Project,
                    Idea,
                    ProjectCharter,
                    owner.display_name,
                    leader.display_name,
                    sponsor.display_name,
                    site.name,
                    department.name,
                    team_sizes.c.team_size,
                )
                .join(Idea, Idea.id == Project.idea_id)
                .join(ProjectCharter, ProjectCharter.idea_id == Idea.id)
                .join(owner, owner.id == Idea.submitter_user_id)
                .outerjoin(leader, leader.id == ProjectCharter.leader_user_id)
                .outerjoin(sponsor, sponsor.id == ProjectCharter.sponsor_user_id)
                # The charter's own location wins; fall back to where the idea was raised.
                .outerjoin(site, site.id == func.coalesce(ProjectCharter.site_id, Idea.site_id))
                .outerjoin(
                    department,
                    department.id
                    == func.coalesce(ProjectCharter.department_id, Idea.department_id),
                )
                .outerjoin(team_sizes, team_sizes.c.project_id == Project.id)
                .where(Project.tenant_id == tenant_id)
                .order_by(ProjectCharter.updated_at.desc())
            )
        ).all()
        entries: list[CharterListEntry] = []
        for (
            project,
            idea,
            charter,
            owner_name,
            leader_name,
            sponsor_name,
            site_name,
            department_name,
            team_size,
        ) in rows:
            is_owner = idea.submitter_user_id == user_id
            role = (
                "owner"
                if is_owner
                else "lead"
                if charter.leader_user_id == user_id
                else "sponsor"
                if charter.sponsor_user_id == user_id
                else "team"
                if project.id in my_teams
                else None
            )
            entries.append(
                CharterListEntry(
                    project_id=project.id,
                    project_reference=project.reference,
                    idea_reference=idea.reference,
                    title=idea.title,
                    idea_type=idea.idea_type,
                    project_subtype=idea.project_subtype,
                    charter_status="baselined" if charter.submitted_at else "drafting",
                    project_status=project.status,
                    leader_name=leader_name or charter.leader or project.lead_name or None,
                    sponsor_name=sponsor_name or charter.sponsor or None,
                    owner_name=owner_name,
                    site=site_name,
                    department=department_name,
                    target_completion_date=charter.target_completion_date
                    or project.target_completion_date,
                    team_size=int(team_size or 0),
                    submitted_at=charter.submitted_at,
                    updated_at=charter.updated_at,
                    my_role=role,
                    # Same rule as the charter page: only the owner, and only until submitted.
                    can_edit=is_owner and charter.submitted_at is None,
                )
            )
        return entries
