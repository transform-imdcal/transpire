# File storage — Phase 2 (hardening)

Last updated: 2026-11-27 (after IT set up the QA bucket)

"Phase 2" here means the second step of the **file storage / attachments / certificates** work,
not the business Phase 2 in `Project.md`.

## Where things stand

**Phase 1 is built (not yet committed at the time of writing):**

- Private S3 storage behind `backend/app/core/storage.py`. Browser uploads straight to the bucket with a
  short-lived signed form; the server uses the EC2 instance role (no access keys). Local development uses
  `STORAGE_BACKEND=local` (files in `backend/file/storage/`).
- Ideas take multiple PDF / PNG / JPEG / WebP files. Each shows its first page as a badge
  (`frontend/components/ideas/idea-attachments.tsx`).
- Files are checked on upload: real type from the contents, size, count. Preview rendering runs in a
  separate, time-limited process (`backend/app/domains/files/render.py`).
- Workspace Admin → Idea setup → **Attachments** sets allowed types and limits, within the platform caps
  (`ATTACHMENT_MAX_FILES`, `ATTACHMENT_MAX_FILE_MB`).
- `scripts/run_scheduled_jobs.py` clears unfinished uploads after 24 hours and files whose idea was
  deleted.
- Tables: `stored_files`, `idea_attachments`, `attachment_policies` (migration `20261127_0035`).

**Decisions already made (don't re-ask):**

- PDFs and images only. **No Office (Word/Excel/PowerPoint) previews**, so no LibreOffice.
- S3 is platform infrastructure, not a workspace setting. Workspace Admins only set policy.
- One bucket per running environment. Today that is QA only; production gets its own bucket when it
  goes live.

## QA bucket (done by IT, 2026-11-27)

- Block Public Access on, ACLs off, versioning on, SSE-KMS with a customer-managed key (Bucket
  Key on, rotation on). Bucket policy denies non-HTTPS; not restricted to a VPC endpoint.
- CORS: `https://transpire-qa.imdcal.com`, `PUT, POST, GET, HEAD`, all headers, exposes `ETag`.
- Lifecycle: incomplete multipart uploads aborted after 30 days; objects under `tenants/` expire
  **3 years after upload**; old versions removed after 30 days.
- IAM: inline policy `TranspireAttachmentsBucket` on the EC2 instance role, which is named
  **`SSM`** (not `transpire-ec2`). The KMS key policy lets this role use the key through S3 only.
  IMDSv2 enforced with hop limit 2; `transpire-backend-1` gets credentials from it.
- Agreed upload limit: **5 MB per file** (signed POST with `content-length-range`). The app's
  platform cap `ATTACHMENT_MAX_FILE_MB` now defaults to 5.

App settings (`S3_*` names): `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_REGION`, `S3_KMS_KEY_ARN`,
`S3_PREFIX` (keep empty: IAM and lifecycle cover `tenants/*` only), `S3_REQUIRE_MALWARE_SCAN`.
The VPC route table, endpoint and VPC IDs are infrastructure records; the app doesn't read them.

- [ ] Add GitHub secrets `S3_BUCKET`, `S3_REGION`, `S3_KMS_KEY_ARN` (and `S3_PREFIX`,
      `S3_REQUIRE_MALWARE_SCAN` only if they differ from the defaults), then deploy QA and try an
      upload in the browser.

## Open decision: retention

Files are deleted by the bucket **3 years after upload**, not 3 years after the idea closes. The
app already handles a removed file cleanly (it says the file is no longer stored rather than
showing a broken link). If retention must count from closure, the app has to delete files itself
and IT should drop the lifecycle expiry rule.

## Phase 2 work

### 1. Malware scanning

Built so it can be switched on without code changes:

- [x] `S3_REQUIRE_MALWARE_SCAN=true` makes every open, download and preview require the object tag
      `GuardDutyMalwareScanStatus=NO_THREATS_FOUND` on the original file. No tag yet → "still being
      checked" (409). Any other result (threats, unsupported, access denied, failed) → blocked
      (403). Fails closed.
- [x] Attachment lists report each file's scan state; badges show "Checking for viruses…" or
      "Blocked by virus scan".
- [x] Local development never scans. Tests cover pending, clean, blocked and switching it off.
- [ ] When IT enables GuardDuty Malware Protection for S3 (with result tagging): set
      `S3_REQUIRE_MALWARE_SCAN=true` and redeploy. The `SSM` role already has
      `s3:GetObjectTagging` in the agreed policy.
- [ ] Nice to have: when a file is found infected, delete it, remove the attachment, audit it and
      tell the uploader, instead of only blocking it.

### 2. Storage quota and usage per workspace

Goal: the platform operator can see and cap what each workspace stores.

- [ ] A per-workspace storage limit, set by the platform admin (default from an env setting).
- [ ] Refuse a new upload that would go over the limit, with a clear message for the uploader and an
      alert for Workspace Owners near the limit (e.g. at 80%).
- [ ] Platform console (`/admin/tenants`): storage used, file count and the ideas using the most
      space, per workspace. Sum from `stored_files.size_bytes` (ready + previews).
- [ ] Workspace Admin → Attachments: show the workspace's own usage against its limit.

### 3. Delete a workspace's files when the workspace is deleted

Goal: no orphaned customer files left in S3.

- [ ] When a workspace is deleted, delete every object under `tenants/{tenant_id}/` (paged
      `ListObjectsV2` + `DeleteObjects`), including old versions if retention allows.
- [ ] Run it as a job that can be retried, and record the result in the platform audit log.

## Not in Phase 2

- **Certificates (Phase 3).** Issued as PDFs after a project reaches `success`, stored in S3 and shown
  on the recipients' profiles. Decided: recipients are the **project lead and project team** only; an
  assigned issuer **always confirms** before anything is issued; **no public verification page**.
  Issuers are an assignment configured by Workspace Admins, not a new role.
- Moving profile photos from Postgres (`users.avatar_data`) into S3. Optional, any time later.
- Letting a customer bring their own bucket or region. The `tenants/{id}/` key layout keeps this open.
