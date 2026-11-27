import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.core.tenant_context import apply_tenant_to_transaction
from app.domains.audit.controllers import record_audit_event
from app.domains.directory.models import DirectoryEmployee, DirectoryRun
from app.domains.directory.schemas import (
    DirectoryEmployeeSummary,
    DirectoryOverview,
    DirectoryRunSummary,
    DirectorySettings,
    VerifyEmployeesResult,
    VerifyOutcome,
)
from app.domains.directory.sync import _domain_allowed, inbox_folder, read_settings
from app.domains.identity.models import Membership, MembershipStatus, User
from app.domains.identity.schemas import SessionIdentity
from app.domains.tenants.models import Tenant
from app.domains.tenants.repositories import SQLAlchemyTenantRepository

tenant_repository = SQLAlchemyTenantRepository()
REVIEW_STATES = ("pending", "approved", "excluded")


async def get_overview(
    tenant_id: uuid.UUID, tenant_slug: str, settings: Settings, session: AsyncSession
) -> DirectoryOverview:
    folder = inbox_folder(settings, tenant_slug)
    waiting = (
        sorted(
            path.name
            for path in folder.iterdir()
            if path.is_file() and path.suffix.casefold() in (".xlsx", ".csv")
        )
        if folder.is_dir()
        else []
    )
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        counts = dict(
            (
                await session.execute(
                    select(DirectoryEmployee.status, func.count())
                    .where(DirectoryEmployee.tenant_id == tenant_id)
                    .group_by(DirectoryEmployee.status)
                )
            ).all()
        )
        with_account = await session.scalar(
            select(func.count()).where(
                DirectoryEmployee.tenant_id == tenant_id,
                DirectoryEmployee.status != "left",
                DirectoryEmployee.user_id.is_not(None),
            )
        )
        awaiting = await session.scalar(
            select(func.count()).where(
                DirectoryEmployee.tenant_id == tenant_id,
                DirectoryEmployee.status == "active",
                DirectoryEmployee.access_review == "pending",
            )
        )
        last_applied = await session.scalar(
            select(func.max(DirectoryRun.created_at)).where(
                DirectoryRun.tenant_id == tenant_id, DirectoryRun.status == "applied"
            )
        )
        return DirectoryOverview(
            settings=read_settings(tenant),
            inbox_folder=f"{folder.parent.name}/{folder.name}",
            inbox_waiting=waiting,
            employees_active=int(counts.get("active", 0)),
            employees_missing=int(counts.get("missing", 0)),
            employees_left=int(counts.get("left", 0)),
            employees_with_account=int(with_account or 0),
            employees_awaiting=int(awaiting or 0),
            last_applied_at=last_applied,
        )


async def update_settings(
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    command: DirectorySettings,
    session: AsyncSession,
) -> DirectorySettings:
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        tenant = await session.get(Tenant, tenant_id, with_for_update=True)
        assert tenant is not None
        before = read_settings(tenant)
        tenant.settings = {**(tenant.settings or {}), "hr_directory": command.model_dump()}
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            subject_user_id=actor_user_id,
            event_type="directory.settings_updated",
            entity_type="tenant",
            entity_id=tenant_id,
            entity_reference="HR directory",
            summary="Updated HR directory settings.",
            details={"before": before.model_dump(), "after": command.model_dump()},
        )
        return command


async def list_runs(tenant_id: uuid.UUID, session: AsyncSession) -> list[DirectoryRunSummary]:
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        runs = (
            await session.scalars(
                select(DirectoryRun)
                .where(DirectoryRun.tenant_id == tenant_id)
                .order_by(DirectoryRun.created_at.desc())
                .limit(30)
            )
        ).all()
        return [
            DirectoryRunSummary(
                id=run.id,
                file_name=run.file_name,
                source=run.source,
                status=run.status,
                created_at=run.created_at,
                summary=run.summary,
                report=run.report,
            )
            for run in runs
        ]


async def _approver_ids(session: AsyncSession, tenant_id: uuid.UUID) -> set[str]:
    rows = (
        await session.execute(
            select(DirectoryEmployee.manager_employee_id, DirectoryEmployee.hod_employee_id).where(
                DirectoryEmployee.tenant_id == tenant_id, DirectoryEmployee.status == "active"
            )
        )
    ).all()
    return {value for row in rows for value in row if value}


async def search_employees(
    tenant_id: uuid.UUID,
    query: str,
    session: AsyncSession,
    limit: int = 50,
    review: str | None = None,
) -> list[DirectoryEmployeeSummary]:
    """Directory rows. ``review`` limits to one verification state, current employees only."""
    manager = aliased(DirectoryEmployee)
    hod = aliased(DirectoryEmployee)
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        statement = (
            select(DirectoryEmployee, manager.display_name, hod.display_name)
            .outerjoin(
                manager,
                (manager.tenant_id == tenant_id)
                & (manager.employee_id == DirectoryEmployee.manager_employee_id),
            )
            .outerjoin(
                hod,
                (hod.tenant_id == tenant_id)
                & (hod.employee_id == DirectoryEmployee.hod_employee_id),
            )
            .where(DirectoryEmployee.tenant_id == tenant_id)
            .order_by(DirectoryEmployee.status, DirectoryEmployee.display_name)
            .limit(limit)
        )
        if review in REVIEW_STATES:
            # Only people in the latest HR file can be verified.
            statement = statement.where(
                DirectoryEmployee.access_review == review, DirectoryEmployee.status == "active"
            )
        term = query.strip()
        if term:
            pattern = f"%{term}%"
            statement = statement.where(
                DirectoryEmployee.display_name.ilike(pattern)
                | DirectoryEmployee.employee_id.ilike(pattern)
                | DirectoryEmployee.email.ilike(pattern)
                | DirectoryEmployee.department.ilike(pattern)
            )
        approvers = await _approver_ids(session, tenant_id)
        return [
            DirectoryEmployeeSummary(
                employee_id=employee.employee_id,
                display_name=employee.display_name,
                email=employee.email,
                location=employee.location,
                department=employee.department,
                sub_department=employee.sub_department,
                manager_employee_id=employee.manager_employee_id,
                manager_name=manager_name,
                hod_employee_id=employee.hod_employee_id,
                hod_name=hod_name,
                status=employee.status,
                has_account=employee.user_id is not None,
                access_review=employee.access_review,
                is_approver=employee.employee_id in approvers,
            )
            for employee, manager_name, hod_name in (await session.execute(statement)).all()
        ]


async def hr_profiles_by_user(
    session: AsyncSession, tenant_id: uuid.UUID
) -> dict[uuid.UUID, DirectoryEmployeeSummary]:
    """HR details for every member linked to a directory row (used by People)."""
    manager = aliased(DirectoryEmployee)
    hod = aliased(DirectoryEmployee)
    rows = (
        await session.execute(
            select(DirectoryEmployee, manager.display_name, hod.display_name)
            .outerjoin(
                manager,
                (manager.tenant_id == tenant_id)
                & (manager.employee_id == DirectoryEmployee.manager_employee_id),
            )
            .outerjoin(
                hod,
                (hod.tenant_id == tenant_id)
                & (hod.employee_id == DirectoryEmployee.hod_employee_id),
            )
            .where(
                DirectoryEmployee.tenant_id == tenant_id,
                DirectoryEmployee.user_id.is_not(None),
            )
            # A current record wins over a "left" one for the same account.
            .order_by((DirectoryEmployee.status == "left").desc())
        )
    ).all()
    approvers = await _approver_ids(session, tenant_id)
    profiles: dict[uuid.UUID, DirectoryEmployeeSummary] = {}
    for employee, manager_name, hod_name in rows:
        assert employee.user_id is not None
        profiles[employee.user_id] = DirectoryEmployeeSummary(
            employee_id=employee.employee_id,
            display_name=employee.display_name,
            email=employee.email,
            location=employee.location,
            department=employee.department,
            sub_department=employee.sub_department,
            manager_employee_id=employee.manager_employee_id,
            manager_name=manager_name,
            hod_employee_id=employee.hod_employee_id,
            hod_name=hod_name,
            status=employee.status,
            has_account=True,
            access_review=employee.access_review,
            is_approver=employee.employee_id in approvers,
        )
    return profiles


async def verify_employees(
    *,
    tenant_id: uuid.UUID,
    employee_ids: list[str],
    role_key: str,
    actor: SessionIdentity,
    settings: Settings,
    session: AsyncSession,
) -> VerifyEmployeesResult:
    """An administrator confirms HR employees should get access: invite them now."""
    from app.domains.tenants.controllers import _queue_invitation

    now = datetime.now(UTC)
    invited: list[VerifyOutcome] = []
    linked: list[VerifyOutcome] = []
    skipped: list[VerifyOutcome] = []
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        options = read_settings(tenant)
        role = await tenant_repository.get_role(session, tenant_id, role_key)
        records = (
            await session.scalars(
                select(DirectoryEmployee)
                .where(
                    DirectoryEmployee.tenant_id == tenant_id,
                    DirectoryEmployee.employee_id.in_(set(employee_ids)),
                )
                .order_by(DirectoryEmployee.display_name)
                .with_for_update()
            )
        ).all()
        found = {record.employee_id for record in records}
        skipped.extend(
            VerifyOutcome(employee_id=missing, name=missing, reason="Not in the HR directory.")
            for missing in sorted(set(employee_ids) - found)
        )
        for record in records:
            outcome = VerifyOutcome(employee_id=record.employee_id, name=record.display_name)
            if record.status != "active":
                outcome.reason = (
                    "Left per HR."
                    if record.status == "left"
                    else "Missing from the latest HR file."
                )
                skipped.append(outcome)
                continue
            if not record.email or not _domain_allowed(record.email, options):
                outcome.reason = "No allowed company email in the HR file."
                skipped.append(outcome)
                continue
            if role is None:
                outcome.reason = "The selected role is unavailable."
                skipped.append(outcome)
                continue
            existing = (
                await session.execute(
                    select(Membership, User)
                    .join(User, User.id == Membership.user_id)
                    .where(
                        Membership.tenant_id == tenant_id,
                        func.lower(User.email) == record.email.casefold(),
                    )
                )
            ).one_or_none()
            if existing is not None and existing[0].status != MembershipStatus.INACTIVE:
                membership, user = existing
                record.user_id = user.id
                membership.employee_number = record.employee_id
                record.access_review = "approved"
                record.reviewed_at = now
                record.reviewed_by_user_id = actor.user.id
                outcome.reason = (
                    "Already invited."
                    if membership.status == MembershipStatus.INVITED
                    else "Already has access."
                )
                linked.append(outcome)
                continue
            if existing is not None and existing[0].provisioned_by != "hr_directory":
                outcome.reason = "Account was deactivated by hand. Reactivate it in People."
                skipped.append(outcome)
                continue
            await _queue_invitation(
                session,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                email=record.email,
                display_name=record.display_name,
                inviter=actor,
                role=role,
                settings=settings,
                provisioned_by="hr_directory",
            )
            membership, user = (
                await session.execute(
                    select(Membership, User)
                    .join(User, User.id == Membership.user_id)
                    .where(
                        Membership.tenant_id == tenant_id,
                        func.lower(User.email) == record.email.casefold(),
                    )
                )
            ).one()
            membership.employee_number = record.employee_id
            record.user_id = user.id
            record.access_review = "approved"
            record.reviewed_at = now
            record.reviewed_by_user_id = actor.user.id
            invited.append(outcome)
        if invited or linked:
            record_audit_event(
                session,
                tenant_id=tenant_id,
                actor_user_id=actor.user.id,
                subject_user_id=None,
                event_type="directory.employees_verified",
                entity_type="tenant",
                entity_id=tenant_id,
                entity_reference="HR directory",
                summary=(
                    f"Verified {len(invited) + len(linked)} HR employee(s); "
                    f"{len(invited)} invited as {role.name if role else role_key}."
                ),
                details={
                    "role_key": role_key,
                    "invited": [item.employee_id for item in invited],
                    "linked": [item.employee_id for item in linked],
                    "skipped": [item.employee_id for item in skipped],
                },
            )
        return VerifyEmployeesResult(invited=invited, linked=linked, skipped=skipped)


async def exclude_employees(
    *,
    tenant_id: uuid.UUID,
    employee_ids: list[str],
    actor: SessionIdentity,
    session: AsyncSession,
) -> int:
    """Mark HR employees as "do not invite". Only those still awaiting verification."""
    now = datetime.now(UTC)
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        records = (
            await session.scalars(
                select(DirectoryEmployee)
                .where(
                    DirectoryEmployee.tenant_id == tenant_id,
                    DirectoryEmployee.employee_id.in_(set(employee_ids)),
                    DirectoryEmployee.access_review == "pending",
                )
                .with_for_update()
            )
        ).all()
        for record in records:
            record.access_review = "excluded"
            record.reviewed_at = now
            record.reviewed_by_user_id = actor.user.id
        if records:
            record_audit_event(
                session,
                tenant_id=tenant_id,
                actor_user_id=actor.user.id,
                subject_user_id=None,
                event_type="directory.employees_excluded",
                entity_type="tenant",
                entity_id=tenant_id,
                entity_reference="HR directory",
                summary=f"Marked {len(records)} HR employee(s) as not to be invited.",
                details={"employee_ids": [record.employee_id for record in records]},
            )
        return len(records)
