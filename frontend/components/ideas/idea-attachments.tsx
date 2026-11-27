"use client";

import { AlertCircle, Download, ExternalLink, FileText, ImageIcon, Loader2, Paperclip, Trash2, UploadCloud, X } from "lucide-react";
import { DragEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { apiRequest, apiUrl, csrfToken } from "@/lib/api";
import styles from "./idea-attachments.module.css";

export type AttachmentRecord = {
  id: string;
  file_name: string;
  content_type: string;
  size_bytes: number;
  page_count: number | null;
  status: "pending" | "ready";
  has_preview: boolean;
  uploaded_at: string;
  uploaded_by_name: string | null;
  can_remove: boolean;
  scan_status: "not_required" | "pending" | "clean" | "blocked";
};
type AttachmentPolicy = { max_files: number; max_file_mb: number; accepted_types: string[]; storage_ready: boolean };
type AttachmentList = { can_edit: boolean; policy: AttachmentPolicy; items: AttachmentRecord[] };
type UploadIntent = { attachment: AttachmentRecord; upload: { target: "s3" | "api"; url: string; fields: Record<string, string> } };
type Upload = { key: string; name: string; size: number; progress: number; phase: "uploading" | "checking" | "failed"; error?: string };

const DEFAULT_TYPES = ["application/pdf", "image/png", "image/jpeg", "image/webp"];
const PARALLEL_UPLOADS = 3;

type Props = {
  /** The saved idea, or null while the form has not been saved yet. */
  ideaId: string | null;
  /** Saves the draft when needed and returns its id; files need an idea to belong to. */
  ensureIdea?: () => Promise<string | null>;
  editable: boolean;
  /** Read-only views hide the whole block when there is nothing to show. */
  hideWhenEmpty?: boolean;
};

export function IdeaAttachments({ ideaId, ensureIdea, editable, hideWhenEmpty = false }: Props) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [list, setList] = useState<AttachmentList | null>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [loadError, setLoadError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [removing, setRemoving] = useState<AttachmentRecord | null>(null);
  const [busyRemove, setBusyRemove] = useState(false);

  const load = useCallback(async (id: string) => {
    try {
      const response = await apiRequest(`/ideas/${id}/attachments`);
      if (!response.ok) throw new Error();
      setList(await response.json() as AttachmentList);
      setLoadError("");
    } catch {
      setLoadError("Attachments couldn't be loaded. Reload the page to try again.");
    }
  }, []);

  useEffect(() => {
    if (!ideaId) return;
    const timer = window.setTimeout(() => void load(ideaId), 0);
    return () => window.clearTimeout(timer);
  }, [ideaId, load]);

  const policy = list?.policy;
  const accepted = policy?.accepted_types ?? DEFAULT_TYPES;
  const canEdit = editable && (list ? list.can_edit : true);
  const ready = list?.items ?? [];
  const patch = (key: string, change: Partial<Upload>) => setUploads((current) => current.map((item) => item.key === key ? { ...item, ...change } : item));

  const uploadOne = async (id: string, file: File, key: string) => {
    try {
      const intentResponse = await apiRequest(`/ideas/${id}/attachments`, {
        method: "POST",
        body: JSON.stringify({ file_name: file.name, size_bytes: file.size, content_type: file.type || "application/octet-stream" }),
      });
      const intentBody = await intentResponse.json().catch(() => null) as UploadIntent | { detail?: string } | null;
      if (!intentResponse.ok) throw new Error((intentBody as { detail?: string } | null)?.detail ?? "This file couldn't be added.");
      const intent = intentBody as UploadIntent;
      try {
        await send(file, intent.upload, (progress) => patch(key, { progress }));
      } catch (reason) {
        await apiRequest(`/ideas/${id}/attachments/${intent.attachment.id}`, { method: "DELETE" }).catch(() => undefined);
        throw reason;
      }
      patch(key, { phase: "checking", progress: 1 });
      const completed = await apiRequest(`/ideas/${id}/attachments/${intent.attachment.id}/complete`, { method: "POST" });
      if (!completed.ok) {
        const body = await completed.json().catch(() => null) as { detail?: string } | null;
        throw new Error(body?.detail ?? "The file couldn't be checked. Try again.");
      }
      setUploads((current) => current.filter((item) => item.key !== key));
    } catch (reason) {
      patch(key, { phase: "failed", error: reason instanceof Error ? reason.message : "Upload failed." });
    }
  };

  const addFiles = async (files: File[]) => {
    if (!files.length || !canEdit) return;
    const queued = files.map((file) => ({ file, key: `${file.name}-${file.size}-${crypto.randomUUID()}` }));
    setUploads((current) => [...current, ...queued.map(({ file, key }) => ({ key, name: file.name, size: file.size, progress: 0, phase: "uploading" as const }))]);
    const id = ideaId ?? await ensureIdea?.() ?? null;
    if (!id) {
      for (const { key } of queued) patch(key, { phase: "failed", error: "Save the draft first, then add files." });
      return;
    }
    const queue = [...queued];
    const worker = async () => {
      for (let next = queue.shift(); next; next = queue.shift()) await uploadOne(id, next.file, next.key);
    };
    await Promise.all(Array.from({ length: Math.min(PARALLEL_UPLOADS, queue.length) }, worker));
    await load(id);
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    void addFiles(Array.from(event.dataTransfer.files));
  };

  const confirmRemove = async () => {
    if (!removing || !ideaId) return;
    setBusyRemove(true);
    try {
      const response = await apiRequest(`/ideas/${ideaId}/attachments/${removing.id}`, { method: "DELETE" });
      if (!response.ok && response.status !== 404) {
        const body = await response.json().catch(() => null) as { detail?: string } | null;
        setLoadError(body?.detail ?? "The file couldn't be removed.");
      }
      await load(ideaId);
    } finally {
      setBusyRemove(false);
      setRemoving(null);
    }
  };

  if (hideWhenEmpty && !canEdit && ready.length === 0) return null;
  const limitText = policy ? `Up to ${policy.max_files} files, ${policy.max_file_mb} MB each` : "Up to 10 files, 5 MB each";
  const full = policy ? ready.length + uploads.filter((item) => item.phase !== "failed").length >= policy.max_files : false;
  const storageMissing = policy ? !policy.storage_ready : false;

  return (
    <div className={styles.attachments}>
      {canEdit ? (
        <label
          htmlFor={inputId}
          className={styles.dropzone}
          data-dragging={dragging}
          data-disabled={full || storageMissing || accepted.length === 0}
          onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <UploadCloud size={22} aria-hidden="true" />
          <span>
            <strong>{storageMissing ? "File storage isn't set up yet" : accepted.length === 0 ? "Attachments are turned off" : full ? "File limit reached" : "Drop files here or browse"}</strong>
            <small>{storageMissing ? "Ask your administrator to finish the storage setup." : `${describeTypes(accepted)} · ${limitText}`}</small>
          </span>
          <input
            ref={inputRef}
            id={inputId}
            type="file"
            multiple
            accept={accepted.join(",")}
            disabled={full || storageMissing || accepted.length === 0}
            onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ""; void addFiles(files); }}
          />
        </label>
      ) : null}

      {loadError ? <p className={styles.error} role="alert"><AlertCircle size={15} aria-hidden="true" />{loadError}</p> : null}

      {ready.length || uploads.length ? (
        <ul className={styles.grid} aria-label="Attached files">
          {ready.map((item) => <ReadyBadge key={item.id} ideaId={ideaId!} item={item} canRemove={canEdit && item.can_remove} onRemove={() => setRemoving(item)} />)}
          {uploads.map((item) => <UploadBadge key={item.key} item={item} onDismiss={() => setUploads((current) => current.filter((entry) => entry.key !== item.key))} />)}
        </ul>
      ) : !canEdit ? <p className={styles.empty}><Paperclip size={15} aria-hidden="true" />No files attached.</p> : null}

      <ConfirmDialog
        open={Boolean(removing)}
        title="Remove this file?"
        description={removing ? `${removing.file_name} will be deleted from this idea. This can't be undone.` : ""}
        confirmLabel="Remove file"
        busyLabel="Removing"
        busy={busyRemove}
        onClose={() => setRemoving(null)}
        onConfirm={() => void confirmRemove()}
      />
    </div>
  );
}

function ReadyBadge({ ideaId, item, canRemove, onRemove }: { ideaId: string; item: AttachmentRecord; canRemove: boolean; onRemove: () => void }) {
  const [previewFailed, setPreviewFailed] = useState(false);
  const base = `/ideas/${ideaId}/attachments/${item.id}`;
  const pending = item.status !== "ready";
  const scanning = item.scan_status === "pending";
  const blocked = item.scan_status === "blocked";
  const closed = pending || scanning || blocked;
  return (
    <li className={styles.badge} data-pending={pending}>
      <a className={styles.thumb} href={closed ? undefined : apiUrl(`${base}/content`)} target="_blank" rel="noopener noreferrer" aria-label={`Open ${item.file_name}`}>
        {item.has_preview && !previewFailed && !closed
          // eslint-disable-next-line @next/next/no-img-element -- authenticated API image, not a static asset
          ? <img src={apiUrl(`${base}/preview`)} alt="" loading="lazy" data-document={isPdf(item.content_type)} onError={() => setPreviewFailed(true)} />
          : <span className={styles.placeholder}>{isPdf(item.content_type) ? <FileText size={30} /> : <ImageIcon size={30} />}</span>}
        <span className={styles.kind}>{kindLabel(item.content_type)}</span>
        {pending ? <span className={styles.overlay}>Upload not finished</span> : null}
        {scanning ? <span className={styles.overlay}>Checking for viruses…</span> : null}
        {blocked ? <span className={styles.overlay} data-tone="danger">Blocked by virus scan</span> : null}
      </a>
      <div className={styles.meta}>
        <strong title={item.file_name}>{item.file_name}</strong>
        <small>{[formatSize(item.size_bytes), item.page_count && isPdf(item.content_type) ? `${item.page_count} ${item.page_count === 1 ? "page" : "pages"}` : null].filter(Boolean).join(" · ")}</small>
      </div>
      <div className={styles.actions}>
        {!closed ? <a href={apiUrl(`${base}/content`)} target="_blank" rel="noopener noreferrer" title="Open"><ExternalLink size={15} /><span className={styles.srOnly}>Open {item.file_name}</span></a> : null}
        {!closed ? <a href={apiUrl(`${base}/download`)} title="Download"><Download size={15} /><span className={styles.srOnly}>Download {item.file_name}</span></a> : null}
        {canRemove ? <button type="button" onClick={onRemove} title="Remove"><Trash2 size={15} /><span className={styles.srOnly}>Remove {item.file_name}</span></button> : null}
      </div>
    </li>
  );
}

function UploadBadge({ item, onDismiss }: { item: Upload; onDismiss: () => void }) {
  const failed = item.phase === "failed";
  return (
    <li className={styles.badge} data-state={item.phase} aria-live="polite">
      <div className={styles.thumb}>
        <span className={styles.placeholder}>
          {failed ? <AlertCircle size={28} /> : <Loader2 size={28} className={styles.spin} />}
          <em>{failed ? "Not added" : item.phase === "checking" ? "Checking file…" : `${Math.round(item.progress * 100)}%`}</em>
        </span>
        {!failed ? <span className={styles.progress}><i style={{ width: `${Math.max(4, item.progress * 100)}%` }} /></span> : null}
      </div>
      <div className={styles.meta}>
        <strong title={item.name}>{item.name}</strong>
        <small className={failed ? styles.failText : undefined}>{failed ? item.error : formatSize(item.size)}</small>
      </div>
      {failed ? <div className={styles.actions}><button type="button" onClick={onDismiss} title="Dismiss"><X size={15} /><span className={styles.srOnly}>Dismiss {item.name}</span></button></div> : null}
    </li>
  );
}

/** Upload with progress. S3 gets the signed form (no cookies); local development posts to the API. */
function send(file: File, upload: UploadIntent["upload"], onProgress: (fraction: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(event.loaded / event.total); };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) return resolve();
      let detail = "";
      try { detail = (JSON.parse(request.responseText) as { detail?: string }).detail ?? ""; } catch { /* S3 answers in XML */ }
      reject(new Error(detail || (request.status === 403 ? "The upload link expired. Try again." : "The upload didn't go through. Try again.")));
    };
    request.onerror = () => reject(new Error("The upload was interrupted. Check your connection and try again."));
    if (upload.target === "s3") {
      const form = new FormData();
      for (const [name, value] of Object.entries(upload.fields)) form.append(name, value);
      form.append("file", file);
      request.open("POST", upload.url);
      request.send(form);
    } else {
      request.open("PUT", apiUrl(upload.url));
      request.withCredentials = true;
      request.setRequestHeader("Content-Type", file.type || "application/octet-stream");
      const token = csrfToken();
      if (token) request.setRequestHeader("X-CSRF-Token", token);
      request.send(file);
    }
  });
}

const isPdf = (type: string) => type === "application/pdf";
const kindLabel = (type: string) => ({ "application/pdf": "PDF", "image/png": "PNG", "image/jpeg": "JPG", "image/webp": "WEBP" })[type] ?? "FILE";

function describeTypes(types: string[]) {
  const pdf = types.includes("application/pdf");
  const images = types.some((type) => type.startsWith("image/"));
  return pdf && images ? "PDF, PNG, JPEG or WebP" : pdf ? "PDF only" : images ? "PNG, JPEG or WebP images" : "No file types allowed";
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
