"use client";

import { ArrowRight, FolderKanban, RefreshCw } from "lucide-react";
import { TenantLink as Link } from "@/components/app/tenant-link";
import { useTenantRouter as useRouter } from "@/lib/use-tenant-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminShell } from "@/components/admin/admin-shell";
import { RegisterToolbar, type RegisterView } from "@/components/ui/register-toolbar";
import { SelectField } from "@/components/ui/select-field";
import { SectionCard } from "@/components/ui/workspace";
import { apiRequest } from "@/lib/api";
import { useSession } from "@/lib/use-session";
import {
  filterProjects,
  formatLakhs,
  summarizeFinancials,
  uniqueProjectValues,
  type ProjectPortfolioRecord,
} from "./projects-portfolio-utils";
import { HealthPill, ProjectStatusPill, healthStates, healthTone, projectStatuses } from "./project-status";
import styles from "./projects.module.css";

type Project = ProjectPortfolioRecord;
const projectViews: Record<string, (project: Project) => boolean> = {
  chartering: (project) => project.status === "charter_in_progress",
  active: (project) => ["ready_to_start", "in_progress", "on_hold"].includes(project.status),
  on_track: (project) => project.health === "on_track",
  at_risk: (project) => project.health === "at_risk",
  blocked: (project) => project.health === "blocked",
  review: (project) => ["completion_review", "oe_verified", "finance_validation"].includes(project.status),
  success: (project) => project.status === "success",
};

export function ProjectsPortfolio() {
  const router = useRouter();
  const { session, loading } = useSession();
  const [projects, setProjects] = useState<ProjectPortfolioRecord[]>([]);
  const [view, setView] = useState("all");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [health, setHealth] = useState("");
  const [site, setSite] = useState("");
  const [department, setDepartment] = useState("");
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [error, setError] = useState("");

  const loadProjects = useCallback(async () => {
    setLoadingProjects(true);
    setError("");
    try {
      const response = await apiRequest("/projects");
      if (response.status === 401) {
        router.replace("/sign-in");
        return;
      }
      if (!response.ok) throw new Error();
      setProjects(await response.json() as ProjectPortfolioRecord[]);
    } catch {
      setError("Projects could not be reached. Check your connection, then retry.");
    } finally {
      setLoadingProjects(false);
    }
  }, [router]);

  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => void loadProjects(), 0);
    return () => window.clearTimeout(timer);
  }, [loadProjects, session]);

  const visible = useMemo(
    () => filterProjects(projects, { query, status, health, site, department }).filter(projectViews[view] ?? (() => true)),
    [department, health, projects, query, site, status, view],
  );
  const sites = useMemo(() => uniqueProjectValues(projects, "site"), [projects]);
  const departments = useMemo(
    () => uniqueProjectValues(projects, "department"),
    [projects],
  );
  const financials = useMemo(() => summarizeFinancials(visible), [visible]);
  const isEmpty = !loadingProjects && !error && projects.length === 0;
  const views: RegisterView[] = [
    { key: "all", label: "All", count: projects.length },
    { key: "chartering", label: "Charter in progress", count: projects.filter(projectViews.chartering).length, tone: "info", hint: "Approved, charter not yet submitted" },
    { key: "active", label: "Active", count: projects.filter(projectViews.active).length, tone: "info", hint: "Ready, in progress or on hold" },
    { key: "on_track", label: "On track", count: projects.filter(projectViews.on_track).length, tone: "success" },
    { key: "at_risk", label: "At risk", count: projects.filter(projectViews.at_risk).length, tone: "warning" },
    { key: "blocked", label: "Blocked", count: projects.filter(projectViews.blocked).length, tone: "danger" },
    { key: "review", label: "Completion review", count: projects.filter(projectViews.review).length, hint: "Completion review, OE verification or finance validation" },
    { key: "success", label: "Closed as success", count: projects.filter(projectViews.success).length, tone: "success" },
  ];

  const clearFilters = () => {
    setView("all");
    setQuery("");
    setStatus("");
    setHealth("");
    setSite("");
    setDepartment("");
  };

  if (loading || !session) {
    return <main className={styles.loading}>Opening the Projects portfolio</main>;
  }

  return (
    <AdminShell
      session={session}
      active="projects"
      title="Projects"
      description="Track implementation, benefits and completion across the organisation."
    >
      {error ? (
        <div className={styles.error} role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => void loadProjects()}>
            <RefreshCw aria-hidden="true" size={14} />Retry
          </button>
        </div>
      ) : null}

      {loadingProjects || isEmpty || error ? null : (
        <RegisterToolbar
          label="Find projects"
          views={views}
          activeView={view}
          onViewChange={setView}
          query={query}
          onQueryChange={setQuery}
          searchLabel="Search projects"
          searchPlaceholder="Search project, idea, lead, site or department"
          resultCount={visible.length}
          filterCount={[status, health, site, department].filter(Boolean).length}
          onClear={clearFilters}
        >
          <SelectField
            id="project-status"
            name="status"
            label="Status"
            value={status}
            onChange={setStatus}
            options={[
              { value: "", label: "All statuses", description: "No status restriction" },
              ...Object.entries(projectStatuses).map(([value, entry]) => ({
                value,
                label: entry.label,
                description: "Project lifecycle state",
              })),
            ]}
          />
          <SelectField
            id="project-health"
            name="health"
            label="Health"
            value={health}
            onChange={setHealth}
            options={[
              { value: "", label: "All health states", description: "No health restriction" },
              ...Object.entries(healthStates).map(([value, entry]) => ({
                value,
                label: entry.label,
                description: "Current delivery assessment",
              })),
            ]}
          />
          <SelectField
            id="project-site"
            name="site"
            label="Site"
            value={site}
            onChange={setSite}
            options={[
              { value: "", label: "All sites", description: "Across the tenant" },
              ...sites.map((value) => ({ value, label: value, description: "Project site" })),
            ]}
          />
          <SelectField
            id="project-department"
            name="department"
            label="Department"
            value={department}
            onChange={setDepartment}
            options={[
              { value: "", label: "All departments", description: "Across all functions" },
              ...departments.map((value) => ({
                value,
                label: value,
                description: "Owning department",
              })),
            ]}
          />
        </RegisterToolbar>
      )}


      <SectionCard
        className={styles.results}
        icon={<FolderKanban size={17} />}
        title={view === "all" ? "Project portfolio" : `Projects: ${views.find((entry) => entry.key === view)?.label.toLowerCase()}`}
        meta={loadingProjects || isEmpty ? undefined : (
          <span className={styles.totals}>
            <span>Target <b>{formatLakhs(financials.target)}</b></span>
            <span>Achieved <b>{formatLakhs(financials.achieved)}</b></span>
            <span>LOS <b>{formatLakhs(financials.lineOfSight)}</b></span>
          </span>
        )}
        flush
      >
        {loadingProjects ? (
          <div className={styles.skeleton} aria-label="Loading projects">
            <span /><span /><span />
          </div>
        ) : null}

        {!loadingProjects && visible.length ? (
          <div className={styles.tableWrap}>
            <table>
              <thead>
                <tr>
                  <th>Project</th>
                  <th>Status and health</th>
                  <th>Milestones</th>
                  <th className={styles.numeric}>Target</th>
                  <th className={styles.numeric}>Achieved</th>
                  <th className={styles.numeric}>LOS</th>
                  <th className={styles.numeric}>Projected</th>
                  <th><span className={styles.srOnly}>Open</span></th>
                </tr>
              </thead>
              <tbody>
                {visible.map((project) => {
                  const progress = Math.min(100, Math.max(0, Number(project.progress) || 0));
                  return (
                    <tr key={project.id}>
                      <td data-label="Project">
                        <span className={styles.reference}>{project.reference}</span>
                        <Link className={styles.projectLink} href={project.status === "charter_in_progress" ? `/projects/${project.id}/charter` : `/projects/${project.id}`}>{project.title}</Link>
                        <small>{project.lead_name || "Lead not recorded"}</small>
                        <small>
                          {[project.site, project.department].filter(Boolean).join(" · ")
                            || "Location not recorded"}
                        </small>
                      </td>
                      <td data-label="Status and health">
                        <div className={styles.pills}>
                          <ProjectStatusPill status={project.status} />
                          <HealthPill health={project.health} />
                        </div>
                        <small>{project.status === "charter_in_progress" ? "Charter draft" : `Baseline v${project.baseline_version}`} · {formatCompletionDate(project.target_completion_date)}</small>
                      </td>
                      <td data-label="Milestones">
                        <div className={styles.barRow}>
                          <span className={styles.bar} data-tone={healthTone(project.health)} aria-hidden="true">
                            <span style={{ width: `${progress}%` }} />
                          </span>
                          <strong>{Number(project.progress).toFixed(0)}%</strong>
                        </div>
                        <small>
                          {project.milestone_count
                            ? `${project.milestone_count} milestone${project.milestone_count === 1 ? "" : "s"}`
                            : "Plan not created"}
                        </small>
                      </td>
                      <MoneyCell label="Target" value={project.target} />
                      <MoneyCell label="Achieved" value={project.achieved} />
                      <MoneyCell label="LOS" value={project.line_of_sight} />
                      <MoneyCell label="Projected" value={project.projected_outcome} />
                      <td className={styles.open}>
                        <Link
                          href={project.status === "charter_in_progress" ? `/projects/${project.id}/charter` : `/projects/${project.id}`}
                          aria-label={`Open project workspace for ${project.reference}`}
                        >
                          <ArrowRight aria-hidden="true" size={16} />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}

        {!loadingProjects && !visible.length ? (
          <div className={styles.empty}>
            <FolderKanban aria-hidden="true" size={26} />
            <h3>{projects.length ? "No projects match these filters." : "No projects yet."}</h3>
            <p>
              {projects.length
                ? "Clear a filter or try a broader search."
                : "A project is created when an approved idea's charter is submitted."}
            </p>
            {projects.length ? (
              <button type="button" onClick={clearFilters}>Clear filters</button>
            ) : (
              <Link href="/ideas">Open Idea Bank</Link>
            )}
          </div>
        ) : null}
      </SectionCard>
    </AdminShell>
  );
}

function MoneyCell({ label, value }: { label: string; value: string }) {
  return <td className={styles.numeric} data-label={label}><strong className={styles.money}>{formatLakhs(value)}</strong></td>;
}

function formatCompletionDate(value: string | null) {
  if (!value) return "Completion date not recorded";
  return `Due ${new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`))}`;
}
