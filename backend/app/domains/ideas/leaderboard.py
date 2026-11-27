"""Employee and site leaderboards, ranked by savings.

* Period: Indian financial year (April to March). Q1 = Apr–Jun … Q4 = Jan–Mar. An idea
  counts in the period it was submitted.
* Savings per idea: Finance-validated benefit when the project has any; otherwise the
  annual estimate (saving + cost avoidance) the idea was submitted with. Values in ₹ lakhs.
* Credit goes to the idea's owner, and to the idea's site.
* The Idea Bank's "Show financial estimates" policy applies: when it's off, people who
  aren't Workspace Admins see the ranking by implemented and approved ideas, without money.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.tenant_context import apply_tenant_to_transaction
from app.domains.configuration.models import ConfigurationItem
from app.domains.ideas.models import Idea, IdeaBankPolicy, IdeaStatus
from app.domains.identity.access import is_workspace_admin
from app.domains.identity.models import User
from app.domains.projects.controllers import annual_target_lakhs, benefit_totals
from app.domains.projects.models import Project, ProjectBenefitMonth, ProjectStatus

Quarter = Literal["all", "q1", "q2", "q3", "q4"]
IdeaType = Literal["all", "kaizen", "project"]

COUNTED_STATUSES = (
    IdeaStatus.SUBMITTED,
    IdeaStatus.NEEDS_CORRECTION,
    IdeaStatus.APPROVED,
    IdeaStatus.CHARTER_IN_PROGRESS,
    IdeaStatus.CHARTER_SUBMITTED,
)
APPROVED_STATUSES = (
    IdeaStatus.APPROVED,
    IdeaStatus.CHARTER_IN_PROGRESS,
    IdeaStatus.CHARTER_SUBMITTED,
)
TOP = 25


class LeaderboardRow(BaseModel):
    rank: int
    key: str
    name: str
    detail: str | None
    submitted: int
    approved: int
    implemented: int
    # ₹ lakhs; None when the viewer may not see financials.
    savings: Decimal | None
    validated: Decimal | None
    estimated: Decimal | None
    is_me: bool = False


class Leaderboard(BaseModel):
    financial_year: int  # 2026 means FY 2026–27 (April 2026 to March 2027)
    financial_years: list[int]
    quarter: Quarter
    idea_type: IdeaType
    period_label: str
    shows_savings: bool
    ranked_by: Literal["savings", "delivery"]
    employees: list[LeaderboardRow]
    sites: list[LeaderboardRow]
    totals: dict[str, Decimal | int | None]


@dataclass
class _Tally:
    name: str
    detail: str | None = None
    submitted: int = 0
    approved: int = 0
    implemented: int = 0
    validated: Decimal = Decimal(0)
    estimated: Decimal = Decimal(0)
    savings: Decimal = Decimal(0)
    ideas: set[uuid.UUID] = field(default_factory=set)


def financial_year_of(moment: date) -> int:
    return moment.year if moment.month >= 4 else moment.year - 1


def _period(financial_year: int, quarter: Quarter) -> tuple[datetime, datetime, str]:
    label_year = f"FY {financial_year}–{str(financial_year + 1)[-2:]}"
    if quarter == "all":
        start, end = date(financial_year, 4, 1), date(financial_year + 1, 4, 1)
        label = label_year
    else:
        index = int(quarter[1]) - 1
        month = 4 + index * 3
        year = financial_year + (1 if month > 12 else 0)
        month = month - 12 if month > 12 else month
        start = date(year, month, 1)
        end_month, end_year = month + 3, year
        if end_month > 12:
            end_month, end_year = end_month - 12, year + 1
        end = date(end_year, end_month, 1)
        names = {0: "Apr–Jun", 1: "Jul–Sep", 2: "Oct–Dec", 3: "Jan–Mar"}
        label = f"{label_year} · Q{index + 1} ({names[index]})"
    as_dt = lambda value: datetime(value.year, value.month, value.day, tzinfo=UTC)
    return as_dt(start), as_dt(end), label


async def get_leaderboard(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: list[str],
    session: AsyncSession,
    *,
    financial_year: int | None = None,
    quarter: Quarter = "all",
    idea_type: IdeaType = "all",
) -> Leaderboard:
    year = financial_year or financial_year_of(datetime.now(UTC).date())
    start, end, label = _period(year, quarter)
    site = aliased(ConfigurationItem)
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        policy = await session.scalar(
            select(IdeaBankPolicy).where(IdeaBankPolicy.tenant_id == tenant_id)
        )
        shows_savings = is_workspace_admin(roles) or bool(policy and policy.show_financials)

        first = await session.scalar(
            select(Idea.submitted_at)
            .where(Idea.tenant_id == tenant_id, Idea.submitted_at.is_not(None))
            .order_by(Idea.submitted_at)
            .limit(1)
        )
        current_year = financial_year_of(datetime.now(UTC).date())
        first_year = financial_year_of(first.date()) if first else current_year
        years = list(range(current_year, min(first_year, year) - 1, -1))

        statement = (
            select(
                Idea, User.display_name, User.email, site.id, site.name, Project.id, Project.status
            )
            .join(User, User.id == Idea.submitter_user_id)
            .outerjoin(site, site.id == Idea.site_id)
            .outerjoin(Project, Project.idea_id == Idea.id)
            .where(
                Idea.tenant_id == tenant_id,
                Idea.status.in_(COUNTED_STATUSES),
                Idea.submitted_at >= start,
                Idea.submitted_at < end,
            )
        )
        if idea_type != "all":
            statement = statement.where(Idea.idea_type == idea_type)
        rows = (await session.execute(statement)).all()

        project_ids = [row[5] for row in rows if row[5] is not None]
        months: dict[uuid.UUID, list[ProjectBenefitMonth]] = {}
        if project_ids:
            for month in (
                await session.scalars(
                    select(ProjectBenefitMonth).where(
                        ProjectBenefitMonth.tenant_id == tenant_id,
                        ProjectBenefitMonth.project_id.in_(project_ids),
                    )
                )
            ).all():
                months.setdefault(month.project_id, []).append(month)

        people: dict[str, _Tally] = {}
        sites: dict[str, _Tally] = {}
        totals = {"submitted": 0, "approved": 0, "implemented": 0, "savings": Decimal(0)}
        for idea, name, email, site_id, site_name, project_id, project_status in rows:
            estimated = annual_target_lakhs(idea)
            _, validated, _ = (
                benefit_totals(months.get(project_id, [])) if project_id else (0, 0, 0)
            )
            validated = Decimal(validated)
            savings = validated if validated > 0 else estimated
            approved = idea.status in APPROVED_STATUSES
            implemented = project_status == ProjectStatus.SUCCESS.value
            for bucket, key, label_name, detail in (
                (people, str(idea.submitter_user_id), name, email),
                (
                    sites,
                    str(site_id) if site_id else "unassigned",
                    site_name or "Site not recorded",
                    None,
                ),
            ):
                tally = bucket.setdefault(key, _Tally(name=label_name, detail=detail))
                tally.submitted += 1
                tally.approved += int(approved)
                tally.implemented += int(implemented)
                tally.validated += validated
                tally.estimated += estimated
                tally.savings += savings
            totals["submitted"] += 1
            totals["approved"] += int(approved)
            totals["implemented"] += int(implemented)
            totals["savings"] += savings

        def ranked(bucket: dict[str, _Tally], *, mark_me: bool) -> list[LeaderboardRow]:
            def order(item: tuple[str, _Tally]):
                tally = item[1]
                if shows_savings:
                    return (-tally.savings, -tally.implemented, -tally.approved, tally.name)
                return (-tally.implemented, -tally.approved, -tally.submitted, tally.name)

            result = []
            for position, (key, tally) in enumerate(
                sorted(bucket.items(), key=order)[:TOP], start=1
            ):
                result.append(
                    LeaderboardRow(
                        rank=position,
                        key=key,
                        name=tally.name,
                        detail=tally.detail,
                        submitted=tally.submitted,
                        approved=tally.approved,
                        implemented=tally.implemented,
                        savings=tally.savings.quantize(Decimal("0.01")) if shows_savings else None,
                        validated=tally.validated.quantize(Decimal("0.01"))
                        if shows_savings
                        else None,
                        estimated=tally.estimated.quantize(Decimal("0.01"))
                        if shows_savings
                        else None,
                        is_me=mark_me and key == str(user_id),
                    )
                )
            return result

        return Leaderboard(
            financial_year=year,
            financial_years=years,
            quarter=quarter,
            idea_type=idea_type,
            period_label=label,
            shows_savings=shows_savings,
            ranked_by="savings" if shows_savings else "delivery",
            employees=ranked(people, mark_me=True),
            sites=ranked(sites, mark_me=False),
            totals={
                **{key: value for key, value in totals.items() if key != "savings"},
                "savings": Decimal(totals["savings"]).quantize(Decimal("0.01"))
                if shows_savings
                else None,
            },
        )
