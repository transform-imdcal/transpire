import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class StoredFileStatus(str, enum.Enum):
    # pending: upload form issued, bytes not yet confirmed.
    # ready: verified type and size, preview generated (when the type has one).
    PENDING = "pending"
    READY = "ready"


class StoredFile(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    """One object in file storage. Purpose-specific tables (attachments, certificates) link here."""

    __tablename__ = "stored_files"
    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_stored_files_storage_key"),
        CheckConstraint("status IN ('pending', 'ready')", name="ck_stored_files_status"),
        CheckConstraint("size_bytes > 0", name="ck_stored_files_size"),
        Index("ix_stored_files_tenant_status_created", "tenant_id", "status", "created_at"),
    )

    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    preview_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=StoredFileStatus.PENDING.value
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdeaAttachment(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "idea_attachments"
    __table_args__ = (
        UniqueConstraint("file_id", name="uq_idea_attachments_file"),
        Index("ix_idea_attachments_tenant_idea", "tenant_id", "idea_id", "position"),
    )

    idea_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stored_files.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AttachmentPolicy(UUIDPrimaryKeyMixin, TenantOwnedMixin, TimestampMixin, Base):
    """Workspace limits for idea attachments; always within the platform ceilings."""

    __tablename__ = "attachment_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_attachment_policies_tenant"),
        CheckConstraint("max_files >= 0", name="ck_attachment_policies_max_files"),
        CheckConstraint("max_file_mb >= 1", name="ck_attachment_policies_max_file_mb"),
    )

    max_files: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    max_file_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    allow_pdf: Mapped[bool] = mapped_column(nullable=False, default=True)
    allow_images: Mapped[bool] = mapped_column(nullable=False, default=True)
