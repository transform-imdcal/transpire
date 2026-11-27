import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AttachmentPolicyRecord(BaseModel):
    max_files: int
    max_file_mb: int
    allow_pdf: bool
    allow_images: bool
    # Platform ceilings, so the admin form can show the allowed range.
    platform_max_files: int
    platform_max_file_mb: int
    # MIME types the upload control accepts right now.
    accepted_types: list[str]
    storage_ready: bool


class AttachmentPolicyCommand(BaseModel):
    max_files: int = Field(ge=0, le=50)
    max_file_mb: int = Field(ge=1, le=100)
    allow_pdf: bool
    allow_images: bool


class AttachmentRecord(BaseModel):
    id: uuid.UUID
    file_name: str
    content_type: str
    size_bytes: int
    page_count: int | None
    status: Literal["pending", "ready"]
    has_preview: bool
    uploaded_at: datetime
    uploaded_by_name: str | None
    can_remove: bool
    # not_required: scanning is off. pending/clean/blocked: GuardDuty's verdict on the file.
    scan_status: Literal["not_required", "pending", "clean", "blocked"] = "not_required"


class AttachmentList(BaseModel):
    can_edit: bool
    policy: AttachmentPolicyRecord
    items: list[AttachmentRecord]


class UploadIntentCommand(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    content_type: str = Field(min_length=1, max_length=100)


class UploadInstructions(BaseModel):
    target: Literal["s3", "api"]
    url: str
    fields: dict[str, str]
    expires_in_seconds: int


class UploadIntentRecord(BaseModel):
    attachment: AttachmentRecord
    upload: UploadInstructions
