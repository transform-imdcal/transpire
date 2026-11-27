import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.core.storage import StorageNotConfiguredError, content_disposition
from app.domains.files.controllers import (
    AttachmentAccessError,
    AttachmentBlockedError,
    AttachmentExpiredError,
    AttachmentNotFoundError,
    AttachmentRejectedError,
    AttachmentScanPendingError,
    attachment_file,
    complete_upload,
    get_attachment_policy,
    list_attachments,
    receive_local_upload,
    remove_attachment,
    start_upload,
    update_attachment_policy,
)
from app.domains.files.schemas import (
    AttachmentList,
    AttachmentPolicyCommand,
    AttachmentPolicyRecord,
    AttachmentRecord,
    UploadIntentCommand,
    UploadIntentRecord,
)
from app.domains.identity.dependencies import (
    AuthenticatedIdentity,
    ContributorIdentity,
    SessionDependency,
    SettingsDependency,
    TenantAdminIdentity,
    TenantDependency,
)

router = APIRouter(tags=["attachments"])


def _http(error: Exception) -> HTTPException:
    if isinstance(error, StorageNotConfiguredError):
        return HTTPException(503, "File storage isn't set up for this environment yet.")
    if isinstance(error, AttachmentNotFoundError):
        return HTTPException(404, "Attachment not found.")
    if isinstance(error, AttachmentAccessError):
        return HTTPException(403, "You can't change or open this idea's attachments.")
    if isinstance(error, AttachmentRejectedError):
        return HTTPException(422, str(error))
    if isinstance(error, AttachmentScanPendingError):
        return HTTPException(
            409, "This file is still being checked for viruses. Try again in a minute."
        )
    if isinstance(error, AttachmentBlockedError):
        return HTTPException(403, "This file was blocked by the virus scan and can't be opened.")
    if isinstance(error, AttachmentExpiredError):
        return HTTPException(410, "This file is no longer stored. Files are kept for three years.")
    raise error


HANDLED = (
    StorageNotConfiguredError,
    AttachmentScanPendingError,
    AttachmentBlockedError,
    AttachmentExpiredError,
    AttachmentNotFoundError,
    AttachmentAccessError,
    AttachmentRejectedError,
)


@router.get("/ideas/{idea_id}/attachments", response_model=AttachmentList)
async def read_attachments(
    idea_id: uuid.UUID,
    identity: AuthenticatedIdentity,
    tenant: TenantDependency,
    settings: SettingsDependency,
    session: SessionDependency,
) -> AttachmentList:
    try:
        return await list_attachments(
            tenant.tenant_id, identity.user.id, identity.roles, idea_id, settings, session
        )
    except HANDLED as error:
        raise _http(error) from error


@router.post(
    "/ideas/{idea_id}/attachments",
    response_model=UploadIntentRecord,
    status_code=status.HTTP_201_CREATED,
)
async def begin_attachment_upload(
    idea_id: uuid.UUID,
    command: UploadIntentCommand,
    identity: ContributorIdentity,
    tenant: TenantDependency,
    settings: SettingsDependency,
    session: SessionDependency,
) -> UploadIntentRecord:
    try:
        return await start_upload(
            tenant.tenant_id, identity.user.id, identity.roles, idea_id, command, settings, session
        )
    except HANDLED as error:
        raise _http(error) from error


@router.post(
    "/ideas/{idea_id}/attachments/{attachment_id}/complete", response_model=AttachmentRecord
)
async def finish_attachment_upload(
    idea_id: uuid.UUID,
    attachment_id: uuid.UUID,
    identity: ContributorIdentity,
    tenant: TenantDependency,
    settings: SettingsDependency,
    session: SessionDependency,
) -> AttachmentRecord:
    try:
        return await complete_upload(
            tenant.tenant_id,
            identity.user.id,
            identity.roles,
            idea_id,
            attachment_id,
            settings,
            session,
        )
    except HANDLED as error:
        raise _http(error) from error


@router.delete(
    "/ideas/{idea_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_attachment(
    idea_id: uuid.UUID,
    attachment_id: uuid.UUID,
    identity: ContributorIdentity,
    tenant: TenantDependency,
    settings: SettingsDependency,
    session: SessionDependency,
) -> None:
    try:
        await remove_attachment(
            tenant.tenant_id,
            identity.user.id,
            identity.roles,
            idea_id,
            attachment_id,
            settings,
            session,
        )
    except HANDLED as error:
        raise _http(error) from error


@router.get("/ideas/{idea_id}/attachments/{attachment_id}/{variant}")
async def open_attachment(
    idea_id: uuid.UUID,
    attachment_id: uuid.UUID,
    variant: Literal["content", "preview", "download"],
    identity: AuthenticatedIdentity,
    tenant: TenantDependency,
    settings: SettingsDependency,
    session: SessionDependency,
) -> Response:
    try:
        served = await attachment_file(
            tenant.tenant_id,
            identity.user.id,
            identity.roles,
            idea_id,
            attachment_id,
            "preview" if variant == "preview" else "content",
            settings,
            session,
        )
    except HANDLED as error:
        raise _http(error) from error
    inline = variant != "download"
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=60"}
    url = await served.storage.download_url(
        served.key, served.file_name, served.content_type, inline
    )
    if url:
        return RedirectResponse(
            url, status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers=headers
        )
    path = served.storage.path(served.key)
    if not path.is_file():
        raise HTTPException(404, "Attachment not found.")
    headers["Content-Disposition"] = content_disposition(served.file_name, inline)
    return FileResponse(path, media_type=served.content_type, headers=headers)


@router.put("/files/uploads/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def upload_to_local_storage(
    file_id: uuid.UUID,
    request: Request,
    identity: ContributorIdentity,
    tenant: TenantDependency,
    settings: SettingsDependency,
    session: SessionDependency,
) -> None:
    """Development-only stand-in for the S3 upload form."""

    try:
        await receive_local_upload(
            tenant.tenant_id, identity.user.id, file_id, request.stream(), settings, session
        )
    except HANDLED as error:
        raise _http(error) from error


@router.get("/admin/configuration/attachment-policy", response_model=AttachmentPolicyRecord)
async def read_attachment_policy(
    _: TenantAdminIdentity,
    tenant: TenantDependency,
    settings: SettingsDependency,
    session: SessionDependency,
) -> AttachmentPolicyRecord:
    return await get_attachment_policy(tenant.tenant_id, settings, session)


@router.put("/admin/configuration/attachment-policy", response_model=AttachmentPolicyRecord)
async def write_attachment_policy(
    command: AttachmentPolicyCommand,
    identity: TenantAdminIdentity,
    tenant: TenantDependency,
    settings: SettingsDependency,
    session: SessionDependency,
) -> AttachmentPolicyRecord:
    try:
        return await update_attachment_policy(
            tenant.tenant_id, identity.user.id, command, settings, session
        )
    except AttachmentRejectedError as error:
        raise HTTPException(422, str(error)) from error
