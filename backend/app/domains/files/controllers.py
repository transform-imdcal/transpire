"""Idea attachments: upload intent → direct upload → verify + preview → ready."""

import asyncio
import hashlib
import json
import os
import resource
import sys
import tempfile
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.storage import (
    Storage,
    StorageNotConfiguredError,
    StoredObjectMissingError,
    get_storage,
)
from app.core.tenant_context import apply_tenant_to_transaction
from app.domains.audit.controllers import record_audit_event
from app.domains.files.models import AttachmentPolicy, IdeaAttachment, StoredFile, StoredFileStatus
from app.domains.files.schemas import (
    AttachmentList,
    AttachmentPolicyCommand,
    AttachmentPolicyRecord,
    AttachmentRecord,
    UploadInstructions,
    UploadIntentCommand,
    UploadIntentRecord,
)
from app.domains.ideas.controllers import can_manage_idea
from app.domains.ideas.models import Idea, IdeaApprovalStage, IdeaStatus
from app.domains.identity.models import User

PURPOSE_IDEA_ATTACHMENT = "idea_attachment"
PDF_TYPES = ("application/pdf",)
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")
EDITABLE_STATUSES = (IdeaStatus.DRAFT, IdeaStatus.NEEDS_CORRECTION)
RENDER_TIMEOUT_SECONDS = 30
ABANDONED_AFTER = timedelta(hours=24)


class AttachmentNotFoundError(Exception):
    pass


class AttachmentAccessError(Exception):
    pass


class AttachmentRejectedError(Exception):
    """The request breaks a rule the user can fix (type, size, count, state)."""


class AttachmentScanPendingError(Exception):
    """Malware scanning is required and hasn't finished for this file yet."""


class AttachmentBlockedError(Exception):
    """The malware scan found a threat, or couldn't scan the file. It stays closed."""


class AttachmentExpiredError(Exception):
    """The record exists but the object is gone (e.g. removed by the bucket's retention rule)."""


@dataclass(frozen=True)
class ServedFile:
    storage: Storage
    key: str
    file_name: str
    content_type: str
    inline: bool


def _scan_required(settings: Settings, storage: Storage) -> bool:
    return settings.storage_require_malware_scan and storage.backend == "s3"


# ---------------------------------------------------------------- policy


def _accepted_types(policy: AttachmentPolicy | None) -> list[str]:
    allow_pdf = policy.allow_pdf if policy else True
    allow_images = policy.allow_images if policy else True
    return [*(PDF_TYPES if allow_pdf else ()), *(IMAGE_TYPES if allow_images else ())]


def _storage_ready(settings: Settings) -> bool:
    try:
        get_storage(settings)
    except StorageNotConfiguredError:
        return False
    return True


def _policy_record(policy: AttachmentPolicy | None, settings: Settings) -> AttachmentPolicyRecord:
    max_files = policy.max_files if policy else settings.attachment_max_files
    max_file_mb = policy.max_file_mb if policy else settings.attachment_max_file_mb
    return AttachmentPolicyRecord(
        max_files=min(max_files, settings.attachment_max_files),
        max_file_mb=min(max_file_mb, settings.attachment_max_file_mb),
        allow_pdf=policy.allow_pdf if policy else True,
        allow_images=policy.allow_images if policy else True,
        platform_max_files=settings.attachment_max_files,
        platform_max_file_mb=settings.attachment_max_file_mb,
        accepted_types=_accepted_types(policy),
        storage_ready=_storage_ready(settings),
    )


async def _policy(session: AsyncSession, tenant_id: uuid.UUID, lock: bool = False):
    statement = select(AttachmentPolicy).where(AttachmentPolicy.tenant_id == tenant_id)
    if lock:
        statement = statement.with_for_update()
    return await session.scalar(statement)


async def get_attachment_policy(
    tenant_id: uuid.UUID, settings: Settings, session: AsyncSession
) -> AttachmentPolicyRecord:
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        return _policy_record(await _policy(session, tenant_id), settings)


async def update_attachment_policy(
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    command: AttachmentPolicyCommand,
    settings: Settings,
    session: AsyncSession,
) -> AttachmentPolicyRecord:
    if command.max_files > settings.attachment_max_files:
        raise AttachmentRejectedError(
            f"The platform allows at most {settings.attachment_max_files} files per idea."
        )
    if command.max_file_mb > settings.attachment_max_file_mb:
        raise AttachmentRejectedError(
            f"The platform allows files up to {settings.attachment_max_file_mb} MB."
        )
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        policy = await _policy(session, tenant_id, lock=True)
        if policy is None:
            policy = AttachmentPolicy(tenant_id=tenant_id)
            session.add(policy)
        before = {
            "max_files": policy.max_files,
            "max_file_mb": policy.max_file_mb,
            "allow_pdf": policy.allow_pdf,
            "allow_images": policy.allow_images,
        }
        policy.max_files = command.max_files
        policy.max_file_mb = command.max_file_mb
        policy.allow_pdf = command.allow_pdf
        policy.allow_images = command.allow_images
        await session.flush()
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            subject_user_id=None,
            event_type="configuration.attachment_policy_updated",
            entity_type="attachment_policy",
            entity_id=policy.id,
            entity_reference=None,
            summary="Updated the idea attachment policy.",
            details={"before": before, "after": command.model_dump()},
        )
        return _policy_record(policy, settings)


# ---------------------------------------------------------------- access


@dataclass(frozen=True)
class _IdeaAccess:
    idea: Idea
    can_view: bool
    can_edit: bool


async def _idea_access(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    idea_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: list[str],
    lock: bool = False,
) -> _IdeaAccess:
    statement = select(Idea).where(Idea.tenant_id == tenant_id, Idea.id == idea_id)
    if lock:
        statement = statement.with_for_update()
    idea = await session.scalar(statement)
    if idea is None:
        raise AttachmentNotFoundError
    stages = list(
        (
            await session.scalars(
                select(IdeaApprovalStage).where(
                    IdeaApprovalStage.tenant_id == tenant_id, IdeaApprovalStage.idea_id == idea_id
                )
            )
        ).all()
    )
    can_edit = idea.submitter_user_id == user_id and idea.status in EDITABLE_STATUSES
    # Drafts stay private to their owner, even from admins (same rule as the idea itself).
    can_view = can_edit or (
        idea.status != IdeaStatus.DRAFT and can_manage_idea(idea, stages, user_id, roles)
    )
    if not can_view:
        raise AttachmentAccessError
    return _IdeaAccess(idea=idea, can_view=can_view, can_edit=can_edit)


def _can_remove(access: _IdeaAccess, attachment: IdeaAttachment) -> bool:
    """Reviewers must keep seeing the evidence they decided on: once an idea has been
    submitted, only files added after its latest submission can be removed."""

    if not access.can_edit:
        return False
    submitted_at = access.idea.submitted_at
    return (
        access.idea.status == IdeaStatus.DRAFT
        or submitted_at is None
        or (attachment.created_at > submitted_at)
    )


def _record(
    attachment: IdeaAttachment, stored: StoredFile, uploader: str | None, access: _IdeaAccess
) -> AttachmentRecord:
    return AttachmentRecord(
        id=attachment.id,
        file_name=stored.original_name,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        page_count=stored.page_count,
        status=stored.status,
        has_preview=stored.preview_key is not None,
        uploaded_at=attachment.created_at,
        uploaded_by_name=uploader,
        can_remove=_can_remove(access, attachment),
    )


async def _attachment_rows(session: AsyncSession, tenant_id: uuid.UUID, idea_id: uuid.UUID):
    return (
        await session.execute(
            select(IdeaAttachment, StoredFile, User.display_name)
            .join(StoredFile, StoredFile.id == IdeaAttachment.file_id)
            .outerjoin(User, User.id == StoredFile.uploaded_by_user_id)
            .where(IdeaAttachment.tenant_id == tenant_id, IdeaAttachment.idea_id == idea_id)
            .order_by(IdeaAttachment.position, IdeaAttachment.created_at)
        )
    ).all()


async def list_attachments(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: list[str],
    idea_id: uuid.UUID,
    settings: Settings,
    session: AsyncSession,
) -> AttachmentList:
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        access = await _idea_access(session, tenant_id, idea_id, user_id, roles)
        rows = await _attachment_rows(session, tenant_id, idea_id)
        policy = _policy_record(await _policy(session, tenant_id), settings)
        visible = [
            (attachment, stored, uploader)
            for attachment, stored, uploader in rows
            # Someone else's half-finished upload is noise; the owner sees their own.
            if stored.status == StoredFileStatus.READY.value or access.can_edit
        ]
        items = [
            _record(attachment, stored, uploader, access)
            for attachment, stored, uploader in visible
        ]
    if policy.storage_ready:
        storage = get_storage(settings)
        if _scan_required(settings, storage):
            ready = [
                (item, stored)
                for item, (_, stored, _) in zip(items, visible, strict=True)
                if stored.status == StoredFileStatus.READY.value
            ]
            results = await asyncio.gather(
                *(storage.scan_status(stored.storage_key) for _, stored in ready),
                return_exceptions=True,
            )
            for (item, _), result in zip(ready, results, strict=True):
                item.scan_status = "pending" if isinstance(result, BaseException) else result
    return AttachmentList(can_edit=access.can_edit, policy=policy, items=items)


# ---------------------------------------------------------------- upload


def _keys(
    settings: Settings, tenant_id: uuid.UUID, idea_id: uuid.UUID, file_id: uuid.UUID
) -> tuple[str, str]:
    """Object keys are stored in full, so changing S3_PREFIX later never orphans old files."""
    prefix = settings.storage_prefix.strip().strip("/")
    base = f"tenants/{tenant_id}/ideas/{idea_id}/files/{file_id}"
    base = f"{prefix}/{base}" if prefix else base
    return f"{base}/original", f"{base}/preview.webp"


def _clean_name(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch.isprintable() and ch not in '\\/:*?"<>|').strip()
    return (cleaned or "file")[:255]


async def start_upload(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: list[str],
    idea_id: uuid.UUID,
    command: UploadIntentCommand,
    settings: Settings,
    session: AsyncSession,
) -> UploadIntentRecord:
    storage = get_storage(settings)
    content_type = command.content_type.strip().casefold()
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        access = await _idea_access(session, tenant_id, idea_id, user_id, roles, lock=True)
        if not access.can_edit:
            raise AttachmentAccessError
        policy = _policy_record(await _policy(session, tenant_id), settings)
        if content_type not in policy.accepted_types:
            raise AttachmentRejectedError(_accepted_message(policy))
        max_bytes = policy.max_file_mb * 1024 * 1024
        if command.size_bytes > max_bytes:
            raise AttachmentRejectedError(f"Files must be {policy.max_file_mb} MB or smaller.")
        count = await session.scalar(
            select(func.count())
            .select_from(IdeaAttachment)
            .where(IdeaAttachment.tenant_id == tenant_id, IdeaAttachment.idea_id == idea_id)
        )
        if (count or 0) >= policy.max_files:
            raise AttachmentRejectedError(
                f"An idea can have at most {policy.max_files} attachments."
            )
        file_id = uuid.uuid4()
        original_key, _ = _keys(settings, tenant_id, idea_id, file_id)
        stored = StoredFile(
            id=file_id,
            tenant_id=tenant_id,
            purpose=PURPOSE_IDEA_ATTACHMENT,
            storage_key=original_key,
            original_name=_clean_name(command.file_name),
            content_type=content_type,
            size_bytes=command.size_bytes,
            status=StoredFileStatus.PENDING.value,
            uploaded_by_user_id=user_id,
        )
        session.add(stored)
        await session.flush()
        attachment = IdeaAttachment(
            tenant_id=tenant_id, idea_id=idea_id, file_id=file_id, position=(count or 0)
        )
        session.add(attachment)
        await session.flush()
        await session.refresh(attachment)
        form = await storage.upload_form(
            key=original_key,
            content_type=content_type,
            max_bytes=max_bytes,
            upload_path=f"/files/uploads/{file_id}",
        )
        uploader = await session.scalar(select(User.display_name).where(User.id == user_id))
        return UploadIntentRecord(
            attachment=_record(attachment, stored, uploader, access),
            upload=UploadInstructions(
                target=form.target,
                url=form.url,
                fields=form.fields,
                expires_in_seconds=settings.storage_url_seconds,
            ),
        )


def _accepted_message(policy: AttachmentPolicyRecord) -> str:
    kinds = [
        *(["PDF"] if policy.allow_pdf else []),
        *(["PNG, JPEG or WebP images"] if policy.allow_images else []),
    ]
    if not kinds:
        return "Attachments are turned off for this workspace."
    return f"Only {' and '.join(kinds)} can be attached."


async def receive_local_upload(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    file_id: uuid.UUID,
    chunks: AsyncIterator[bytes],
    settings: Settings,
    session: AsyncSession,
) -> None:
    """Development stand-in for the S3 form post. Same checks S3 would apply."""

    storage = get_storage(settings)
    if storage.backend != "local":
        raise AttachmentNotFoundError
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        stored = await session.scalar(
            select(StoredFile).where(
                StoredFile.tenant_id == tenant_id,
                StoredFile.id == file_id,
                StoredFile.uploaded_by_user_id == user_id,
                StoredFile.status == StoredFileStatus.PENDING.value,
            )
        )
        if stored is None:
            raise AttachmentNotFoundError
        key, max_bytes = stored.storage_key, stored.size_bytes
    try:
        await storage.write_stream(key, chunks, max_bytes)
    except ValueError as error:
        raise AttachmentRejectedError("The file is larger than the size you declared.") from error


def _detect_type(head: bytes) -> str | None:
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _limit_child() -> None:  # pragma: no cover - runs in the child process
    for limit, value in ((resource.RLIMIT_CPU, RENDER_TIMEOUT_SECONDS),):
        resource.setrlimit(limit, (value, value))
    if sys.platform.startswith("linux"):
        memory = 1024 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))


async def _render_preview(kind: str, source: Path, target: Path) -> int | None:
    """Returns the page count, or None when no preview could be produced."""

    backend_root = Path(__file__).resolve().parents[3]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.domains.files.render",
        kind,
        str(source),
        str(target),
        cwd=str(backend_root),
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(backend_root)},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        preexec_fn=_limit_child,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), RENDER_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        return None
    if process.returncode != 0 or not target.is_file():
        return None
    try:
        return int(json.loads(stdout.decode() or "{}").get("page_count") or 0) or None
    except (ValueError, AttributeError):
        return None


async def complete_upload(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: list[str],
    idea_id: uuid.UUID,
    attachment_id: uuid.UUID,
    settings: Settings,
    session: AsyncSession,
) -> AttachmentRecord:
    storage = get_storage(settings)
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        access = await _idea_access(session, tenant_id, idea_id, user_id, roles)
        if not access.can_edit:
            raise AttachmentAccessError
        row = (
            await session.execute(
                select(IdeaAttachment, StoredFile)
                .join(StoredFile, StoredFile.id == IdeaAttachment.file_id)
                .where(
                    IdeaAttachment.tenant_id == tenant_id,
                    IdeaAttachment.idea_id == idea_id,
                    IdeaAttachment.id == attachment_id,
                )
            )
        ).first()
        if row is None:
            raise AttachmentNotFoundError
        attachment, stored = row
        if stored.status == StoredFileStatus.READY.value:
            return _record(attachment, stored, None, access)
        original_key, preview_key = _keys(settings, tenant_id, idea_id, stored.id)
        declared_type, declared_size = stored.content_type, stored.size_bytes

    # Verify and render outside the transaction: this is the slow part.
    async def reject(message: str) -> AttachmentRejectedError:
        await storage.delete([original_key, preview_key])
        async with session.begin():
            await apply_tenant_to_transaction(session, tenant_id)
            doomed = await session.get(StoredFile, stored.id)
            if doomed is not None:
                await session.delete(doomed)
        return AttachmentRejectedError(message)

    actual_size = await storage.size(original_key)
    if actual_size is None:
        raise AttachmentRejectedError("The upload hasn't arrived yet. Try again.")
    if actual_size != declared_size:
        raise await reject("The uploaded file doesn't match what was announced. Upload it again.")
    with tempfile.TemporaryDirectory(prefix="transpire-upload-") as workdir:
        source, preview = Path(workdir) / "original", Path(workdir) / "preview.webp"
        try:
            await storage.download_to(original_key, source)
        except StoredObjectMissingError:
            raise AttachmentRejectedError("The upload hasn't arrived yet. Try again.") from None
        detected = _detect_type(await asyncio.to_thread(_read_head, source))
        if detected != declared_type:
            raise await reject(
                "This file's contents don't match its type. Only real PDF or image files can be attached."
            )
        digest = await asyncio.to_thread(_sha256, source)
        pages = await _render_preview("pdf" if detected in PDF_TYPES else "image", source, preview)
        if detected in IMAGE_TYPES and pages is None:
            raise await reject("This image couldn't be read. It may be damaged.")
        has_preview = pages is not None and preview.is_file()
        if has_preview:
            await storage.put_bytes(preview_key, preview.read_bytes(), "image/webp")

    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        access = await _idea_access(session, tenant_id, idea_id, user_id, roles)
        stored = await session.get(StoredFile, stored.id, with_for_update=True)
        attachment = await session.get(IdeaAttachment, attachment_id)
        if stored is None or attachment is None:
            raise AttachmentNotFoundError
        stored.status = StoredFileStatus.READY.value
        stored.sha256 = digest
        stored.page_count = pages
        stored.preview_key = preview_key if has_preview else None
        stored.completed_at = datetime.now(UTC)
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_user_id=user_id,
            subject_user_id=user_id,
            event_type="idea.attachment_added",
            entity_type="idea",
            entity_id=idea_id,
            entity_reference=access.idea.reference,
            summary=f"Attached {stored.original_name} to {access.idea.reference}.",
            details={
                "file_id": str(stored.id),
                "file_name": stored.original_name,
                "size_bytes": stored.size_bytes,
                "sha256": digest,
            },
        )
        await session.flush()
        uploader = await session.scalar(select(User.display_name).where(User.id == user_id))
        return _record(attachment, stored, uploader, access)


def _read_head(path: Path) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(16)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------- remove / serve


async def remove_attachment(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: list[str],
    idea_id: uuid.UUID,
    attachment_id: uuid.UUID,
    settings: Settings,
    session: AsyncSession,
) -> None:
    storage = get_storage(settings)
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        access = await _idea_access(session, tenant_id, idea_id, user_id, roles, lock=True)
        row = (
            await session.execute(
                select(IdeaAttachment, StoredFile)
                .join(StoredFile, StoredFile.id == IdeaAttachment.file_id)
                .where(
                    IdeaAttachment.tenant_id == tenant_id,
                    IdeaAttachment.idea_id == idea_id,
                    IdeaAttachment.id == attachment_id,
                )
            )
        ).first()
        if row is None:
            raise AttachmentNotFoundError
        attachment, stored = row
        if not access.can_edit:
            raise AttachmentAccessError
        if not _can_remove(access, attachment):
            raise AttachmentRejectedError(
                "Reviewers have already seen this file, so it can't be removed."
            )
        keys = [stored.storage_key, *([stored.preview_key] if stored.preview_key else [])]
        if stored.status == StoredFileStatus.READY.value:
            record_audit_event(
                session,
                tenant_id=tenant_id,
                actor_user_id=user_id,
                subject_user_id=user_id,
                event_type="idea.attachment_removed",
                entity_type="idea",
                entity_id=idea_id,
                entity_reference=access.idea.reference,
                summary=f"Removed {stored.original_name} from {access.idea.reference}.",
                details={"file_id": str(stored.id), "file_name": stored.original_name},
            )
        await session.delete(stored)  # cascades to the attachment row
    await storage.delete(keys)


async def attachment_file(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: list[str],
    idea_id: uuid.UUID,
    attachment_id: uuid.UUID,
    variant: str,
    settings: Settings,
    session: AsyncSession,
) -> ServedFile:
    storage = get_storage(settings)
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        await _idea_access(session, tenant_id, idea_id, user_id, roles)
        stored = await session.scalar(
            select(StoredFile)
            .join(IdeaAttachment, IdeaAttachment.file_id == StoredFile.id)
            .where(
                IdeaAttachment.tenant_id == tenant_id,
                IdeaAttachment.idea_id == idea_id,
                IdeaAttachment.id == attachment_id,
                StoredFile.status == StoredFileStatus.READY.value,
            )
        )
        if stored is None:
            raise AttachmentNotFoundError
        if variant == "preview" and stored.preview_key is None:
            raise AttachmentNotFoundError
        original_key = stored.storage_key
        served = (
            ServedFile(storage, stored.preview_key, "preview.webp", "image/webp", True)
            if variant == "preview"
            else ServedFile(storage, original_key, stored.original_name, stored.content_type, True)
        )
    # The original's scan result gates its preview too: the preview was drawn from it.
    if _scan_required(settings, storage):
        status = await storage.scan_status(original_key)
        if status == "pending":
            raise AttachmentScanPendingError
        if status == "blocked":
            raise AttachmentBlockedError
    if await storage.size(served.key) is None:
        raise AttachmentExpiredError
    return served


# ---------------------------------------------------------------- housekeeping


async def purge_abandoned_uploads(
    tenant_id: uuid.UUID, settings: Settings, session: AsyncSession
) -> int:
    """Remove uploads never completed within a day, and files whose idea was deleted."""

    try:
        storage = get_storage(settings)
    except StorageNotConfiguredError:
        return 0
    cutoff = datetime.now(UTC) - ABANDONED_AFTER
    async with session.begin():
        await apply_tenant_to_transaction(session, tenant_id)
        linked = select(IdeaAttachment.file_id).where(IdeaAttachment.tenant_id == tenant_id)
        doomed = list(
            (
                await session.scalars(
                    select(StoredFile).where(
                        StoredFile.tenant_id == tenant_id,
                        StoredFile.purpose == PURPOSE_IDEA_ATTACHMENT,
                        (
                            (StoredFile.status == StoredFileStatus.PENDING.value)
                            & (StoredFile.created_at < cutoff)
                        )
                        | StoredFile.id.not_in(linked),
                    )
                )
            ).all()
        )
        keys = [key for stored in doomed for key in (stored.storage_key, stored.preview_key) if key]
        for stored in doomed:
            await session.delete(stored)
    await storage.delete(keys)
    return len(doomed)
