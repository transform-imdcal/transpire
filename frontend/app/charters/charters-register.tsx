"use client";

import { ArrowRight, BadgeCheck, FilePen, FileText, RefreshCw } from "lucide-react";
import { TenantLink as Link } from "@/components/app/tenant-link";
import { useTenantRouter as useRouter } from "@/lib/use-tenant-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminShell } from "@/components/admin/admin-shell";
import { RegisterToolbar } from "@/components/ui/register-toolbar";
import { SelectField } from "@/components/ui/select-field";
import { SectionCard, StatusPill } from "@/components/ui/workspace";
import { apiRequest } from "@/lib/api";
import { useSession } from "@/lib/use-session";
import projectStyles from "../projects/projects.module.css";
import styles from "./charters.module.css";

type Charter = {
  project_id: string;
  project_reference: string;
  idea_reference: string;
  title: string;
  idea_type: string;
  project_subtype: string | null;
  charter_status: "drafting" | "baselined";
  project_status: string;
  leader_name: string | null;
  sponsor_name: string | null;
  owner_name: string;
  site: string | null;
  department: string | null;
  target_completion_date: string | null;
  team_size: number;
  submitted_at: string | null;
  updated_at: string;
  my_role: "owner" | "lead" | "sponsor" | "team" | null;
  can_edit: boolean;
};
type View = "mine" | "all" | "drafting" | "baselined";

const roleLabels: Record<NonNullable<Charter["my_role"]>, string> = { owner: "You own this", lead: "You lead this", sponsor: "You sponsor this", team: "You're on the team" };

export function ChartersRegister() {
  const router = useRouter();
  const { session, loading } = useSession();
  const [charters, setCharters] = useState<Charter[]>([]);
  const [view, setView] = useState<View | null>(null);
  const [query, setQuery] = useState("");
  const [site, setSite] = useState("");
  const [department, setDepartment] = useState("");
  const [loadingCharters, setLoadingCharters] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoadingCharters(true);
    setError("");
    try {
      const response = await apiRequest("/projects/charters");
      if (response.status === 401) return router.replace("/sign-in");
      if (!response.ok) throw new Error();
      const rows = await response.json() as Charter[];
      setCharters(rows);
      // Start where the person is involved; fall back to everything.
      setView((current) => current ?? (rows.some((row) => row.my_role) ? "mine" : "all"));
    } catch {
      setError("Charters could not be reached. Check your connection, then retry.");
    } finally {
      setLoadingCharters(false);
    }
  }, [router]);

  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load, session]);

  const activeView = view ?? "all";
  const counts = useMemo(() => ({
    mine: charters.filter((row) => row.my_role).length,
    drafting: charters.filter((row) => row.charter_status === "drafting").length,
    baselined: charters.filter((row) => row.charter_status === "baselined").length,
  }), [charters]);
  const sites = useMemo(() => unique(charters.map((row) => row.site)), [charters]);
  const departments = useMemo(() => unique(charters.map((row) => row.department)), [charters]);
  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return charters.filter((row) =>
      (activeView === "all" || (activeView === "mine" ? Boolean(row.my_role) : row.charter_status === activeView))
      && (!site || row.site === site)
      && (!department || row.department === department)
      && (!needle || [row.title, row.project_reference, row.idea_reference, row.leader_name, row.sponsor_name, row.owner_name, row.site, row.department]
        .some((value) => value?.toLocaleLowerCase().includes(needle))));
  }, [activeView, charters, department, query, site]);
  const filtersActive = Boolean(query || site || department);
  const clearFilters = () => { setQuery(""); setSite(""); setDepartment(""); };

  if (loading || !session) return <main className={projectStyles.loading}>Opening Charters</main>;

  const isEmpty = !loadingCharters && !error && charters.length === 0;

  return (
    <AdminShell session={session} active="charters" title="Charters" description="Every project charter in the workspace: who owns it, where it stands, and a direct way in.">
      {error ? (
        <div className={projectStyles.error} role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => void load()}><RefreshCw aria-hidden="true" size={14} />Retry</button>
        </div>
      ) : null}

      {loadingCharters || isEmpty || error ? null : (
        <RegisterToolbar
          label="Find charters"
          views={[
            { key: "mine", label: "Involving me", count: counts.mine, tone: "info", hint: "You own, lead, sponsor or are on the team" },
            { key: "drafting", label: "Being drafted", count: counts.drafting, tone: "warning", hint: "Approved idea, charter still open" },
            { key: "baselined", label: "Baselined", count: counts.baselined, tone: "success", hint: "Submitted; baseline v1 published" },
            { key: "all", label: "All", count: charters.length },
          ]}
          activeView={activeView}
          onViewChange={(key) => setView(key as View)}
          query={query}
          onQueryChange={setQuery}
          searchLabel="Search charters"
          searchPlaceholder="Search title, reference, lead, sponsor, site or department"
          resultCount={visible.length}
          filterCount={[site, department].filter(Boolean).length}
          onClear={clearFilters}
        >
          <SelectField id="charter-site" name="site" label="Site" value={site} onChange={setSite} options={[{ value: "", label: "All sites", description: "Across the workspace" }, ...sites.map((value) => ({ value, label: value, description: "Charter site" }))]} />
          <SelectField id="charter-department" name="department" label="Department" value={department} onChange={setDepartment} options={[{ value: "", label: "All departments", description: "Across all functions" }, ...departments.map((value) => ({ value, label: value, description: "Owning department" }))]} />
        </RegisterToolbar>
      )}


      <SectionCard
        className={projectStyles.results}
        icon={<FileText size={17} />}
        title={{ mine: "Charters involving you", all: "All charters", drafting: "Charters being drafted", baselined: "Baselined charters" }[activeView]}
        flush
      >
        {loadingCharters ? <div className={projectStyles.skeleton} aria-label="Loading charters"><span /><span /><span /></div> : null}

        {!loadingCharters && visible.length ? (
          <div className={`${projectStyles.tableWrap} ${styles.compact}`}>
            <table className={styles.fit}>
              <thead>
                <tr>
                  <th>Charter</th>
                  <th>Status</th>
                  <th>People</th>
                  <th>Location</th>
                  <th><span className={projectStyles.srOnly}>Open</span></th>
                </tr>
              </thead>
              <tbody>
                {visible.map((row) => {
                  const href = `/projects/${row.project_id}/charter`;
                  return (
                    <tr key={row.project_id}>
                      <td data-label="Charter">
                        <span className={projectStyles.reference}>{row.project_reference}</span>
                        <Link className={projectStyles.projectLink} href={href}>{row.title || "Untitled charter"}</Link>
                        <small>{[row.idea_type === "project" ? row.project_subtype || "Project" : "Kaizen", `from ${row.idea_reference}`].join(" · ")}</small>
                        {row.my_role ? <span className={styles.role}>{roleLabels[row.my_role]}</span> : null}
                      </td>
                      <td data-label="Status">
                        <div className={projectStyles.pills}>
                          {row.charter_status === "baselined"
                            ? <StatusPill tone="success" icon={<BadgeCheck size={14} aria-hidden="true" />}>Baselined</StatusPill>
                            : <StatusPill tone="warning" icon={<FilePen size={14} aria-hidden="true" />}>Being drafted</StatusPill>}
                        </div>
                        <small>{row.submitted_at ? `Submitted ${formatDate(row.submitted_at)}` : `Last edited ${formatDate(row.updated_at)}`}</small>
                        <small>{row.target_completion_date ? `Target ${formatDate(`${row.target_completion_date}T00:00:00Z`)}` : "Target date not set"}</small>
                      </td>
                      <td data-label="People">
                        <div className={styles.people}>
                          <strong>{row.leader_name ? `Lead: ${row.leader_name}` : "Lead not named"}</strong>
                          <small>{row.sponsor_name ? `Sponsor: ${row.sponsor_name}` : "Sponsor not named"}</small>
                          <small>{row.team_size ? `${row.team_size} in the team` : "No team yet"} · Owner {row.owner_name}</small>
                        </div>
                      </td>
                      <td data-label="Location">
                        <small>{row.site || "Site not recorded"}</small>
                        <small>{row.department || "Department not recorded"}</small>
                      </td>
                      <td className={styles.action}>
                        <Link className={row.can_edit ? styles.continue : styles.view} href={href} aria-label={`${row.can_edit ? "Continue" : "View"} charter ${row.project_reference}`}>
                          {row.can_edit ? "Continue" : "View"}<ArrowRight aria-hidden="true" size={15} />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}

        {!loadingCharters && !visible.length && !error ? (
          <div className={projectStyles.empty}>
            <FileText aria-hidden="true" size={26} />
            <h3>{charters.length ? (activeView === "mine" && !filtersActive ? "No charters involve you yet." : "No charters match.") : "No charters yet."}</h3>
            <p>{charters.length ? "Switch view, clear a filter or try a broader search." : "A charter opens when an idea is approved. The idea's owner completes it, then submits it to publish the project baseline."}</p>
            {charters.length ? <button type="button" onClick={() => { clearFilters(); setView("all"); }}>Show all charters</button> : <Link href="/ideas">Open Idea Bank</Link>}
          </div>
        ) : null}
      </SectionCard>
    </AdminShell>
  );
}

function unique(values: Array<string | null>) {
  return [...new Set(values.filter((value): value is string => Boolean(value)))].sort((a, b) => a.localeCompare(b));
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(value));
}
