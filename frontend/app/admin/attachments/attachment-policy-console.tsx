"use client";

import { AlertTriangle, Paperclip, Save } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "@/components/admin/admin-shell";
import { DismissibleNotice } from "@/components/ui/dismissible-notice";
import { SectionCard } from "@/components/ui/workspace";
import { apiRequest } from "@/lib/api";
import { useAdminSession } from "@/lib/use-admin-session";
import { useTenantRouter as useRouter } from "@/lib/use-tenant-router";
import shared from "../admin.module.css";
import styles from "../configuration.module.css";
import register from "@/components/admin/admin-register.module.css";
import local from "./attachments.module.css";

type Policy = {
  max_files: number;
  max_file_mb: number;
  allow_pdf: boolean;
  allow_images: boolean;
  platform_max_files: number;
  platform_max_file_mb: number;
  storage_ready: boolean;
};

export function AttachmentPolicyConsole() {
  const router = useRouter();
  const { session, loading } = useAdminSession();
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await apiRequest("/admin/configuration/attachment-policy");
      if (response.status === 401) return router.replace("/sign-in");
      if (response.status === 403) return router.replace("/home");
      if (!response.ok) throw new Error();
      setPolicy(await response.json() as Policy);
    } catch { setError("The attachment policy couldn't be loaded. Retry in a moment."); }
  }, [router]);

  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load, session]);

  const save = async () => {
    if (!policy) return;
    setBusy(true); setMessage(""); setError("");
    try {
      const response = await apiRequest("/admin/configuration/attachment-policy", {
        method: "PUT",
        body: JSON.stringify({ max_files: policy.max_files, max_file_mb: policy.max_file_mb, allow_pdf: policy.allow_pdf, allow_images: policy.allow_images }),
      });
      const body = await response.json().catch(() => null) as Policy | { detail?: string | { msg: string }[] } | null;
      if (!response.ok) {
        const detail = (body as { detail?: unknown } | null)?.detail;
        return setError(typeof detail === "string" ? detail : "Check the numbers, then save again.");
      }
      setPolicy(body as Policy);
      setMessage("Attachment policy saved. It applies to new uploads straight away.");
    } catch { setError("The policy service is temporarily unavailable."); }
    finally { setBusy(false); }
  };

  if (loading || !session) return <main className={shared.loading}>Loading administration</main>;

  const update = (change: Partial<Policy>) => setPolicy((current) => current ? { ...current, ...change } : current);
  const toggle = (key: "allow_pdf" | "allow_images", label: string, hint: string) => policy ? (
    <button type="button" aria-pressed={policy[key]} onClick={() => update({ [key]: !policy[key] })}>
      <span><strong>{label}</strong><small>{hint}</small></span>
      <span className={styles.switch}><em aria-hidden="true">{policy[key] ? "On" : "Off"}</em><i data-on={policy[key]}><b /></i></span>
    </button>
  ) : null;

  return (
    <AdminShell session={session} active="attachments" title="Attachments" description="Which supporting files people can attach to ideas, and how many.">
      {message || error ? <div className={register.notices}>
        {message ? <DismissibleNotice onDismiss={() => setMessage("")}>{message}</DismissibleNotice> : null}
        {error ? <DismissibleNotice tone="error" onDismiss={() => setError("")}>{error}</DismissibleNotice> : null}
      </div> : null}
      {!policy ? <p className={register.empty}>Loading attachment policy</p> : <>
        {!policy.storage_ready ? <div className={local.warning} role="status"><AlertTriangle size={18} aria-hidden="true" /><p><strong>File storage isn&apos;t connected yet.</strong><span>People can&apos;t upload until the platform team finishes the storage setup. You can still set the policy now.</span></p></div> : null}
        <SectionCard className={styles.policy} icon={<Paperclip size={17} />} title="Set what can be attached to an idea." description="Files are checked on upload: contents must really be the type they claim. The platform limits are the most you can allow." meta="Workspace policy">
          <fieldset>
            <legend>File types</legend>
            <div className={styles.policyToggles}>
              {toggle("allow_pdf", "PDF documents", "Reports, process maps, calculations. Reviewers see the first page as a preview.")}
              {toggle("allow_images", "Images", "PNG, JPEG or WebP photographs and screenshots.")}
            </div>
          </fieldset>
          <fieldset>
            <legend>Limits</legend>
            <div className={local.limits}>
              <label><span>Files per idea</span><input type="number" min={0} max={policy.platform_max_files} value={policy.max_files} onChange={(event) => update({ max_files: Number(event.target.value) })} /><small>Up to {policy.platform_max_files}. Set 0 to turn attachments off.</small></label>
              <label><span>Largest file (MB)</span><input type="number" min={1} max={policy.platform_max_file_mb} value={policy.max_file_mb} onChange={(event) => update({ max_file_mb: Number(event.target.value) })} /><small>Up to {policy.platform_max_file_mb} MB.</small></label>
            </div>
          </fieldset>
          <button className={styles.policySave} type="button" onClick={() => void save()} disabled={busy}><Save size={16} />{busy ? "Saving policy" : "Save attachment policy"}</button>
        </SectionCard>
      </>}
    </AdminShell>
  );
}
