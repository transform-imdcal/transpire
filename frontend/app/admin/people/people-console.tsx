"use client";

import { Ban, BellRing, ShieldCheck, CheckCircle2, ChevronRight, CircleDot, Clock, Hourglass, Lock, Mail, MailCheck, MailPlus, MailX, RefreshCw, UserCheck, UserX, Users, type LucideIcon } from "lucide-react";
import { useTenantRouter as useRouter } from "@/lib/use-tenant-router";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { AdminShell } from "@/components/admin/admin-shell";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DismissibleNotice } from "@/components/ui/dismissible-notice";
import { SearchField } from "@/components/ui/search-field";
import { SelectField } from "@/components/ui/select-field";
import { StatusPill } from "@/components/ui/workspace";
import { SidePanel } from "@/components/admin/side-panel";
import register from "@/components/admin/admin-register.module.css";
import { apiRequest } from "@/lib/api";
import { useAdminSession } from "@/lib/use-admin-session";
import { roleName, roleNames } from "@/lib/roles";
import styles from "../admin.module.css";
import local from "./people.module.css";

type Member = {
  id: string;
  email: string;
  display_name: string;
  status: string;
  roles: string[];
  can_manage: boolean;
  last_signed_in_at: string | null;
  provisioned_by: string;
  employee_id: string | null;
  department: string | null;
  sub_department: string | null;
  location: string | null;
  manager_name: string | null;
  hod_name: string | null;
  hr_status: string | null;
  is_approver: boolean;
};

/** An employee from the HR file who has no account yet (awaiting verification, or set aside). */
type HrEmployee = {
  employee_id: string;
  display_name: string;
  email: string | null;
  location: string;
  department: string;
  sub_department: string;
  manager_name: string | null;
  hod_name: string | null;
  status: string;
  has_account: boolean;
  access_review: "pending" | "approved" | "excluded";
  is_approver: boolean;
};

type VerifyOutcome = { employee_id: string; name: string; reason: string | null };
type VerifyResult = { invited: VerifyOutcome[]; linked: VerifyOutcome[]; skipped: VerifyOutcome[] };

/** One row of the People table: a workspace member, or an HR employee without an account. */
type Row = {
  key: string;
  member?: Member;
  employee?: HrEmployee;
  name: string;
  email: string;
  state: string;
  source: "hr" | "manual";
  employeeId: string | null;
  department: string;
  subDepartment: string;
  location: string;
  managerName: string | null;
  hodName: string | null;
  isApprover: boolean;
};

type Role = { key: string; name: string; description: string; rank: number; assignable: boolean };
type Invitation = {
  id: string;
  email: string;
  display_name: string;
  role_key: string;
  status: "pending" | "expired" | "revoked";
  expires_at: string;
  last_sent_at: string;
};

type ReminderState = { last_signed_in_at: string | null; cooldown_until: string | null };
type ReminderOverview = { cooldown_hours: number; candidates: (ReminderState & { membership_id: string })[] };
type ReminderResult = { queued: string[]; skipped: { membership_id: string; reason: "cooldown" | "not_eligible" }[] };

const reminderDate = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const relativeTime = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

function signedInAgo(date: Date) {
  const minutes = Math.round((date.getTime() - Date.now()) / 60_000);
  if (Math.abs(minutes) < 1) return "just now";
  if (Math.abs(minutes) < 60) return relativeTime.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return relativeTime.format(hours, "hour");
  const days = Math.round(hours / 24);
  if (Math.abs(days) < 30) return relativeTime.format(days, "day");
  return `on ${new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" }).format(date)}`;
}

type DirectoryFilter = "all" | "awaiting" | "invited" | "active" | "inactive" | "not_invited";
const FILTERS: { key: DirectoryFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "awaiting", label: "Awaiting verification" },
  { key: "invited", label: "Invited" },
  { key: "active", label: "Active" },
  { key: "inactive", label: "Inactive" },
  { key: "not_invited", label: "Not invited" },
];
const filterOf = (state: string): Exclude<DirectoryFilter, "all"> => state === "awaiting" ? "awaiting" : state === "not_invited" ? "not_invited" : state === "active" ? "active" : ["invited", "pending", "expired"].includes(state) ? "invited" : "inactive";
const INVITE_ROLES = new Set(["viewer", "idea_submitter"]);

type Tone = "neutral" | "info" | "success" | "warning" | "danger";

const accountStates: Record<string, { label: string; invitationLabel?: string; tone: Tone; Icon: LucideIcon }> = {
  awaiting: { label: "Awaiting verification", tone: "warning", Icon: Hourglass },
  not_invited: { label: "Not invited", tone: "neutral", Icon: MailX },
  active: { label: "Active", tone: "success", Icon: CheckCircle2 },
  invited: { label: "Invited", tone: "info", Icon: Mail },
  pending: { label: "Pending", invitationLabel: "Invitation pending", tone: "info", Icon: Mail },
  expired: { label: "Expired", invitationLabel: "Invitation expired", tone: "warning", Icon: Clock },
  revoked: { label: "Revoked", invitationLabel: "Invitation revoked", tone: "danger", Icon: Ban },
  inactive: { label: "Inactive", tone: "neutral", Icon: UserX },
  locked: { label: "Locked", tone: "danger", Icon: Lock },
};

function AccountStatus({ state, invitation = false }: { state: string; invitation?: boolean }) {
  const entry = accountStates[state] ?? { label: state.replaceAll("_", " "), tone: "neutral" as const, Icon: CircleDot };
  return <StatusPill tone={entry.tone} icon={<entry.Icon aria-hidden="true" size={14} />}>{invitation ? entry.invitationLabel ?? entry.label : entry.label}</StatusPill>;
}

function initials(name: string, fallback: string) {
  const letters = name.split(/\s+/).filter(Boolean).map((word) => word[0]).join("").slice(0, 2);
  return (letters || fallback.slice(0, 1)).toLocaleUpperCase();
}

type Confirmation = {
  title: string;
  description: string;
  confirmLabel: string;
  busyLabel: string;
  run: () => Promise<void>;
};

export function PeopleConsole() {
  const router = useRouter();
  const { session, loading } = useAdminSession();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [filter, setFilter] = useState<DirectoryFilter>("all");
  const [members, setMembers] = useState<Member[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [roleKey, setRoleKey] = useState("idea_submitter");
  const [roleTarget, setRoleTarget] = useState<Member | null>(null);
  const [nextRole, setNextRole] = useState("");
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [reminders, setReminders] = useState<Record<string, ReminderState>>({});
  const [cooldownHours, setCooldownHours] = useState(0);
  const [unverified, setUnverified] = useState<HrEmployee[]>([]);
  const [sourceFilter, setSourceFilter] = useState("all");
  const [departmentFilter, setDepartmentFilter] = useState("all");
  const [locationFilter, setLocationFilter] = useState("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [verifyTargets, setVerifyTargets] = useState<HrEmployee[] | null>(null);
  const [verifyRole, setVerifyRole] = useState("idea_submitter");
  const [verifyGroup, setVerifyGroup] = useState<string | null>(null);
  const [groupByDepartment, setGroupByDepartment] = useState(true);
  const [expandedDepartments, setExpandedDepartments] = useState<Set<string>>(new Set());

  useEffect(() => {
    // The HR directory page links here with ?status=awaiting.
    const wanted = new URLSearchParams(window.location.search).get("status");
    if (wanted && FILTERS.some((item) => item.key === wanted)) {
      const timer = window.setTimeout(() => setFilter(wanted as DirectoryFilter), 0);
      return () => window.clearTimeout(timer);
    }
  }, []);

  const loadDirectory = useCallback(async () => {
    const [membersResponse, rolesResponse, invitationsResponse, remindersResponse, pendingResponse, excludedResponse] = await Promise.all([
      apiRequest("/admin/members"),
      apiRequest("/admin/roles"),
      apiRequest("/admin/invitations"),
      apiRequest("/admin/check-in-reminders").catch(() => null),
      apiRequest("/admin/directory/employees?review=pending&limit=5000").catch(() => null),
      apiRequest("/admin/directory/employees?review=excluded&limit=5000").catch(() => null),
    ]);
    const hr: HrEmployee[] = [];
    if (pendingResponse?.ok) hr.push(...(await pendingResponse.json()) as HrEmployee[]);
    if (excludedResponse?.ok) hr.push(...(await excludedResponse.json()) as HrEmployee[]);
    // Only people with no account; anyone already linked shows as a member.
    setUnverified(hr.filter((employee) => !employee.has_account && employee.status !== "left"));
    setSelected(new Set());
    if (remindersResponse?.ok) {
      const overview = (await remindersResponse.json()) as ReminderOverview;
      setCooldownHours(overview.cooldown_hours);
      setReminders(Object.fromEntries(overview.candidates.map(({ membership_id, ...state }) => [membership_id, state])));
    }
    if (membersResponse.status === 403 || rolesResponse.status === 403) {
      router.replace("/home");
      return;
    }
    if (membersResponse.ok) setMembers((await membersResponse.json()) as Member[]);
    if (rolesResponse.ok) {
      const availableRoles = ((await rolesResponse.json()) as Role[]).sort((a, b) => a.rank - b.rank);
      setRoles(availableRoles);
      const grantable = availableRoles.filter((role) => role.assignable);
      setRoleKey((current) => grantable.some((role) => role.key === current) ? current : grantable.find((role) => role.key === "idea_submitter")?.key ?? grantable[0]?.key ?? "");
    }
    if (invitationsResponse.ok) setInvitations((await invitationsResponse.json()) as Invitation[]);
  }, [router]);

  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => void loadDirectory(), 0);
    return () => window.clearTimeout(timer);
  }, [loadDirectory, session]);

  const invite = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setMessage("");
    setError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      const response = await apiRequest("/admin/invitations", {
        method: "POST",
        body: JSON.stringify({
          display_name: String(form.get("display_name") ?? "").trim(),
          email: String(form.get("email") ?? "").trim(),
          role_key: roleKey,
        }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
        setError(payload?.detail ?? "The invitation could not be sent.");
        return;
      }
      formElement.reset();
      setInviteOpen(false);
      setMessage("Invitation queued. The person will appear here after accepting it.");
      await loadDirectory();
    } catch {
      setError("Invitations are temporarily unavailable.");
    } finally {
      setSubmitting(false);
    }
  };

  const invitationAction = async (invitation: Invitation, action: "resend" | "revoke") => {
    setBusyAction(`${action}:${invitation.id}`);
    setError(""); setMessage("");
    try {
      const response = await apiRequest(`/admin/invitations/${invitation.id}${action === "resend" ? "/resend" : ""}`, { method: action === "resend" ? "POST" : "DELETE" });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
        setError(payload?.detail ?? `Invitation could not be ${action === "resend" ? "resent" : "revoked"}.`);
      } else {
        setMessage(action === "resend" ? "A fresh invitation was queued and the previous link was revoked." : "Invitation revoked.");
        await loadDirectory();
      }
    } catch {
      setError("Invitation controls are temporarily unavailable.");
    } finally {
      setBusyAction("");
    }
  };

  const memberAction = async (member: Member, status: "active" | "inactive") => {
    setBusyAction(`member:${member.id}`); setError(""); setMessage("");
    try {
      const response = await apiRequest(`/admin/members/${member.id}/status`, { method: "PATCH", body: JSON.stringify({ status }) });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
        setError(payload?.detail ?? "Member status could not be changed.");
      } else {
        setMessage(status === "inactive" ? "Member deactivated and sessions revoked." : "Member reactivated.");
        await loadDirectory();
      }
    } catch {
      setError("Member controls are temporarily unavailable.");
    } finally {
      setBusyAction("");
    }
  };

  const openRoleChange = (member: Member) => {
    setRoleTarget(member);
    setNextRole(member.roles[0] ?? "");
  };

  const changeRole = async () => {
    if (!roleTarget || !nextRole) return;
    setBusyAction(`role:${roleTarget.id}`); setError(""); setMessage("");
    try {
      const response = await apiRequest(`/admin/members/${roleTarget.id}/role`, { method: "PATCH", body: JSON.stringify({ role_key: nextRole }) });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
        setError(typeof payload?.detail === "string" ? payload.detail : "The role could not be changed.");
        return;
      }
      setMessage(`${roleTarget.display_name} is now a ${roleName(nextRole)}.`);
      setRoleTarget(null);
      await loadDirectory();
    } catch {
      setError("Role controls are temporarily unavailable.");
    } finally {
      setBusyAction("");
    }
  };

  const grantableRoles = roles.filter((role) => role.assignable);

  const remind = async (member: Member) => {
    setBusyAction(`remind:${member.id}`); setError(""); setMessage("");
    try {
      const response = await apiRequest("/admin/check-in-reminders", { method: "POST", body: JSON.stringify({ membership_ids: [member.id] }) });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
        setError(typeof payload?.detail === "string" ? payload.detail : "The reminder could not be sent.");
        return;
      }
      const result = (await response.json()) as ReminderResult;
      const skip = result.skipped[0]?.reason;
      if (skip === "cooldown") setError(`${member.display_name} was reminded recently, so no reminder was sent.`);
      else if (skip) setError(`${member.display_name} is no longer an active member.`);
      else setMessage(`Check-in reminder queued for ${member.display_name}.`);
      await loadDirectory();
    } catch {
      setError("Reminders are temporarily unavailable.");
    } finally {
      setBusyAction("");
    }
  };

  const verify = async () => {
    if (!verifyTargets?.length) return;
    setBusyAction("verify"); setError(""); setMessage("");
    try {
      const response = await apiRequest("/admin/directory/employees/verify", { method: "POST", body: JSON.stringify({ employee_ids: verifyTargets.map((item) => item.employee_id), role_key: verifyRole }) });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
        setError(typeof payload?.detail === "string" ? payload.detail : "The invitations could not be sent.");
        return;
      }
      const result = (await response.json()) as VerifyResult;
      const parts = [`${result.invited.length} invitation${result.invited.length === 1 ? "" : "s"} sent`];
      if (result.linked.length) parts.push(`${result.linked.length} already had an account`);
      setMessage(`Verified. ${parts.join(", ")}.`);
      if (result.skipped.length) setError(`Not invited: ${result.skipped.slice(0, 5).map((item) => `${item.name} (${item.reason})`).join("; ")}${result.skipped.length > 5 ? `, and ${result.skipped.length - 5} more` : ""}.`);
      setVerifyTargets(null);
      await loadDirectory();
    } catch {
      setError("Verification is temporarily unavailable.");
    } finally {
      setBusyAction("");
    }
  };

  const exclude = async (targets: HrEmployee[]) => {
    setBusyAction("exclude"); setError(""); setMessage("");
    try {
      const response = await apiRequest("/admin/directory/employees/exclude", { method: "POST", body: JSON.stringify({ employee_ids: targets.map((item) => item.employee_id) }) });
      if (!response.ok) { setError("These employees could not be updated."); return; }
      const { excluded } = (await response.json()) as { excluded: number };
      setMessage(`${excluded} ${excluded === 1 ? "employee" : "employees"} marked as not invited. You can still verify them later.`);
      await loadDirectory();
    } catch {
      setError("Verification is temporarily unavailable.");
    } finally {
      setBusyAction("");
    }
  };

  const cooldownLabel = cooldownHours % 24 === 0 ? `${cooldownHours / 24} day${cooldownHours === 24 ? "" : "s"}` : `${cooldownHours} hour${cooldownHours === 1 ? "" : "s"}`;

  // Helper text under Access, for every member regardless of status.
  const lastSignedIn = (member: Member) => {
    if (!member.last_signed_in_at) return <small className={styles.lastSignedIn}>Never signed in</small>;
    const signedIn = new Date(member.last_signed_in_at);
    return <small className={styles.lastSignedIn} title={reminderDate.format(signedIn)}>Last signed in {signedInAgo(signedIn)}</small>;
  };

  const reminderButton = (member: Member) => {
    const state = reminders[member.id];
    const coolingUntil = state?.cooldown_until ? new Date(state.cooldown_until) : null;
    const lastSeen = state?.last_signed_in_at ? `Last signed in ${reminderDate.format(new Date(state.last_signed_in_at))}` : "Never signed in";
    const label = coolingUntil
      ? `Reminded recently. ${member.display_name} can be reminded again after ${reminderDate.format(coolingUntil)}.`
      : `Send ${member.display_name} a check-in reminder. ${lastSeen}.`;
    return <button type="button" aria-label={label} title={label} onClick={() => setConfirmation({ title: `Remind ${member.display_name} to check in?`, description: `They will get an email asking them to sign in to ${session?.tenant.name ?? "TRANSPIRE"}. ${lastSeen}.${cooldownHours ? ` They can't be reminded again for ${cooldownLabel}.` : ""}`, confirmLabel: "Send reminder", busyLabel: "Sending", run: () => remind(member) })} disabled={Boolean(busyAction) || Boolean(coolingUntil) || !state}><BellRing aria-hidden="true" size={15} /></button>;
  };

  const confirmAction = async () => {
    if (!confirmation) return;
    setConfirming(true);
    try {
      await confirmation.run();
      setConfirmation(null);
    } finally {
      setConfirming(false);
    }
  };

  const normalizedSearch = searchQuery.trim().toLocaleLowerCase();
  const stateOf = (member: Member) => invitations.find((item) => item.email === member.email)?.status ?? member.status;
  const rows: Row[] = [
    ...members.map((member): Row => ({
      key: `m:${member.id}`,
      member,
      name: member.display_name,
      email: member.email,
      state: stateOf(member),
      source: member.provisioned_by === "hr_directory" || member.employee_id ? "hr" : "manual",
      employeeId: member.employee_id,
      department: member.department ?? "",
      subDepartment: member.sub_department ?? "",
      location: member.location ?? "",
      managerName: member.manager_name,
      hodName: member.hod_name,
      isApprover: member.is_approver,
    })),
    ...unverified.map((employee): Row => ({
      key: `hr:${employee.employee_id}`,
      employee,
      name: employee.display_name,
      email: employee.email ?? "",
      state: employee.access_review === "excluded" ? "not_invited" : "awaiting",
      source: "hr",
      employeeId: employee.employee_id,
      department: employee.department,
      subDepartment: employee.sub_department,
      location: employee.location,
      managerName: employee.manager_name,
      hodName: employee.hod_name,
      isApprover: employee.is_approver,
    })),
  ];
  const uniqueSorted = (values: string[]) => [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
  const departments = uniqueSorted(rows.map((row) => row.department));
  const locations = uniqueSorted(rows.map((row) => row.location));
  // Status counts honour the other filters, so each tab says what clicking it would show.
  const matchesOtherFilters = (row: Row) => (sourceFilter === "all" || row.source === sourceFilter)
    && (departmentFilter === "all" || row.department === departmentFilter)
    && (locationFilter === "all" || row.location === locationFilter)
    && (!normalizedSearch || [row.name, row.email, row.employeeId ?? "", row.department, row.subDepartment, row.location, row.managerName ?? "", row.hodName ?? "", accountStates[row.state]?.label ?? row.state, ...(row.member ? roleNames(row.member.roles).split(",") : [])].some((value) => value.toLocaleLowerCase().includes(normalizedSearch)));
  const filterCounts: Record<DirectoryFilter, number> = { all: 0, awaiting: 0, invited: 0, active: 0, inactive: 0, not_invited: 0 };
  rows.filter(matchesOtherFilters).forEach((row) => { filterCounts.all += 1; filterCounts[filterOf(row.state)] += 1; });
  const visibleRows = rows.filter((row) => matchesOtherFilters(row) && (filter === "all" || filterOf(row.state) === filter))
    .sort((a, b) => Number(Boolean(b.employee && b.state === "awaiting")) - Number(Boolean(a.employee && a.state === "awaiting")) || a.name.localeCompare(b.name));
  const selectableVisible = visibleRows.filter((row) => row.employee).map((row) => row.employee as HrEmployee);
  const selectedEmployees = unverified.filter((employee) => selected.has(employee.employee_id));
  const allVisibleSelected = selectableVisible.length > 0 && selectableVisible.every((employee) => selected.has(employee.employee_id));
  const toggle = (employeeId: string) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(employeeId)) next.delete(employeeId); else next.add(employeeId);
    return next;
  });
  const toggleAllVisible = () => setSelected((current) => {
    const next = new Set(current);
    selectableVisible.forEach((employee) => { if (allVisibleSelected) next.delete(employee.employee_id); else next.add(employee.employee_id); });
    return next;
  });
  const awaitingTotal = unverified.filter((employee) => employee.access_review === "pending").length;
  const awaitingApprovers = unverified.filter((employee) => employee.access_review === "pending" && employee.is_approver).length;
  const filtersActive = sourceFilter !== "all" || departmentFilter !== "all" || locationFilter !== "all" || filter !== "all" || Boolean(normalizedSearch);
  const inviteRoles = grantableRoles.filter((role) => INVITE_ROLES.has(role.key));
  const openVerify = (targets: HrEmployee[], group: string | null = null) => { setVerifyRole("idea_submitter"); setVerifyGroup(group); setVerifyTargets(targets); };
  const NO_DEPARTMENT = "No department";
  // Accordion groups: departments as reported in the HR file. Departments with people
  // awaiting verification come first, then alphabetical.
  const departmentGroups = Object.values(visibleRows.reduce<Record<string, { name: string; rows: Row[]; locations: string[] }>>((groups, row) => {
    const name = row.department || NO_DEPARTMENT;
    const group = groups[name] ?? (groups[name] = { name, rows: [], locations: [] });
    group.rows.push(row);
    if (row.location && !group.locations.includes(row.location)) group.locations.push(row.location);
    return groups;
  }, {})).sort((a, b) => Number(b.rows.some((row) => row.state === "awaiting")) - Number(a.rows.some((row) => row.state === "awaiting")) || Number(a.name === NO_DEPARTMENT) - Number(b.name === NO_DEPARTMENT) || a.name.localeCompare(b.name));
  const toggleDepartment = (name: string) => setExpandedDepartments((current) => {
    const next = new Set(current);
    if (next.has(name)) next.delete(name); else next.add(name);
    return next;
  });
  const allExpanded = departmentGroups.length > 0 && departmentGroups.every((group) => expandedDepartments.has(group.name));
  const askExclude = (targets: HrEmployee[]) => setConfirmation({
    title: targets.length === 1 ? `Don't invite ${targets[0].display_name}?` : `Don't invite ${targets.length} employees?`,
    description: "They stay in the HR directory without access. You can verify and invite them at any time from the Not invited filter.",
    confirmLabel: "Don't invite",
    busyLabel: "Saving",
    run: () => exclude(targets),
  });

  const renderRow = (row: Row) => {
                  const member = row.member;
                  const employee = row.employee;
                  const invitation = member ? invitations.find((item) => item.email === member.email) : undefined;
                  return <tr key={row.key} data-inactive={row.state === "inactive" || row.state === "not_invited"}>
                    <td className={local.check}>{employee ? <input type="checkbox" aria-label={`Select ${row.name}`} checked={selected.has(employee.employee_id)} onChange={() => toggle(employee.employee_id)} /> : null}</td>
                    <td><div className={styles.person}><span className={styles.avatar} aria-hidden="true">{initials(row.name, row.email || "?")}</span><span><strong>{row.name}</strong><small>{row.email || "No email in the HR file"}</small>
                      <span className={local.tags}>{row.employeeId ? <span className={local.tag}>ID {row.employeeId}</span> : null}<span className={local.tag}>{row.source === "hr" ? "HR directory" : "Added manually"}</span>{row.isApprover ? <span className={local.tag} data-tone="approver">Approver</span> : null}{member?.hr_status === "missing" ? <span className={local.tag}>Missing from last HR file</span> : member?.hr_status === "left" ? <span className={local.tag}>Left per HR</span> : null}</span>
                    </span></div></td>
                    <td>{row.department || "—"}{row.subDepartment || row.location ? <small className={local.sub}>{[row.subDepartment, row.location].filter(Boolean).join(" · ")}</small> : null}</td>
                    <td>{row.managerName || row.hodName ? <div className={local.reports}>{row.managerName ? <span>RM <b>{row.managerName}</b></span> : null}{row.hodName ? <span>HoD <b>{row.hodName}</b></span> : null}</div> : "—"}</td>
                    <td>{member ? <><span className={styles.access}>{roleNames(member.roles)}</span>{lastSignedIn(member)}</> : <small className={styles.muted}>No account yet</small>}</td>
                    <td><AccountStatus state={row.state} invitation={Boolean(invitation)} /></td>
                    <td><div className={register.actions}>{employee ? <>
                      <button type="button" onClick={() => openVerify([employee])} disabled={Boolean(busyAction) || !employee.email}><MailCheck aria-hidden="true" size={15} /> Invite</button>
                      {employee.access_review === "pending" ? <button type="button" onClick={() => askExclude([employee])} disabled={Boolean(busyAction)}><MailX aria-hidden="true" size={15} /> Don&apos;t invite</button> : null}
                    </> : !member ? null : !member.can_manage ? <span className={styles.muted}>{member.email === session?.user.email ? "You" : "Managed by a Workspace Owner"}</span> : invitation ? <><button type="button" onClick={() => void invitationAction(invitation, "resend")} disabled={Boolean(busyAction)}><RefreshCw aria-hidden="true" size={15} /> Resend</button>{invitation.status !== "revoked" ? <button type="button" data-tone="danger" onClick={() => setConfirmation({ title: "Revoke this invitation?", description: `The invitation for ${invitation.email} will stop working immediately. You can send a new invitation later.`, confirmLabel: "Revoke invitation", busyLabel: "Revoking", run: () => invitationAction(invitation, "revoke") })} disabled={Boolean(busyAction)}><Ban aria-hidden="true" size={15} /> Revoke</button> : null}</> : member.status === "active" ? <>{reminderButton(member)}<button type="button" onClick={() => openRoleChange(member)} disabled={Boolean(busyAction)}><ShieldCheck aria-hidden="true" size={15} /> Change role</button><button type="button" data-tone="danger" onClick={() => setConfirmation({ title: `Deactivate ${member.display_name}?`, description: "Their active sessions will end immediately and they will be unable to sign in until reactivated.", confirmLabel: "Deactivate member", busyLabel: "Deactivating", run: () => memberAction(member, "inactive") })} disabled={Boolean(busyAction)}><UserX aria-hidden="true" size={15} /> Deactivate</button></> : <button type="button" onClick={() => void memberAction(member, "active")} disabled={Boolean(busyAction)}><UserCheck aria-hidden="true" size={15} /> Reactivate</button>}</div></td>
                  </tr>;
  };

  if (loading || !session) return <main className={styles.loading}>Loading administration</main>;

  return (
    <AdminShell
      session={session}
      active="people"
      title="People"
      description="Everyone with access to this workspace, open invitations, and employees from the HR file awaiting your verification."
      actions={<button type="button" className={styles.primary} onClick={() => setInviteOpen(true)}><MailPlus aria-hidden="true" size={16} />Invite people</button>}
    >
      {message || error ? <div className={styles.notices}>
        {message ? <DismissibleNotice onDismiss={() => setMessage("")}>{message}</DismissibleNotice> : null}
        {error ? <DismissibleNotice tone="error" onDismiss={() => setError("")}>{error}</DismissibleNotice> : null}
      </div> : null}
      {awaitingTotal ? <div className={local.banner} role="status">
        <p><Hourglass aria-hidden="true" size={17} /><span><strong>{awaitingTotal} {awaitingTotal === 1 ? "employee" : "employees"} from the HR file {awaitingTotal === 1 ? "is" : "are"} awaiting verification.</strong> Nobody has been invited yet.{awaitingApprovers ? ` ${awaitingApprovers} of them ${awaitingApprovers === 1 ? "is a reporting manager or head of department" : "are reporting managers or heads of department"}, so approvals routed to them fall back to Workspace Admins until they join.` : ""}</span></p>
        {filter !== "awaiting" ? <button type="button" className={styles.primary} onClick={() => { setFilter("awaiting"); setSelected(new Set()); }}>Review them</button> : null}
      </div> : null}
      <section className={register.surface} aria-label="Organisation directory">
        <div className={register.toolbar}>
          <div className={local.statusRow}>
            <SearchField id="people-search" label="Search people" value={searchQuery} onChange={setSearchQuery} placeholder="Name, email, employee ID, department, manager" resultCount={visibleRows.length} />
            {filtersActive ? <button type="button" className={local.clearFilters} onClick={() => { setFilter("all"); setSourceFilter("all"); setDepartmentFilter("all"); setLocationFilter("all"); setSearchQuery(""); }}>Clear filters</button> : null}
          </div>
          <div className={local.viewRow}>
          <div className={register.segmented} role="group" aria-label="Filter by status">
            {FILTERS.filter((item) => item.key === "all" || item.key === filter || filterCounts[item.key] > 0).map((item) => <button key={item.key} type="button" aria-pressed={filter === item.key} onClick={() => setFilter(item.key)}>{item.label}<span>{filterCounts[item.key]}</span></button>)}
          </div>
          <div className={local.viewTools}>
            {groupByDepartment && departmentGroups.length > 1 ? <button type="button" className={local.clearFilters} onClick={() => setExpandedDepartments(allExpanded ? new Set() : new Set(departmentGroups.map((group) => group.name)))}>{allExpanded ? "Collapse all" : "Expand all"}</button> : null}
            <div className={register.segmented} role="group" aria-label="Layout">
              <button type="button" aria-pressed={groupByDepartment} onClick={() => setGroupByDepartment(true)}>By department</button>
              <button type="button" aria-pressed={!groupByDepartment} onClick={() => setGroupByDepartment(false)}>List</button>
            </div>
          </div>
          </div>
          <div className={local.filters}>
            <SelectField id="people-source" name="source" label="Source" value={sourceFilter} onChange={setSourceFilter} options={[{ value: "all", label: "All sources" }, { value: "hr", label: "HR directory" }, { value: "manual", label: "Added manually" }]} />
            <SelectField id="people-department" name="department" label="Department" value={departmentFilter} onChange={setDepartmentFilter} options={[{ value: "all", label: "All departments" }, ...departments.map((value) => ({ value, label: value }))]} disabled={!departments.length} />
            <SelectField id="people-location" name="location" label="Location" value={locationFilter} onChange={setLocationFilter} options={[{ value: "all", label: "All locations" }, ...locations.map((value) => ({ value, label: value }))]} disabled={!locations.length} />
          </div>
        </div>
        {selectedEmployees.length ? <div className={local.bulkBar} role="region" aria-label="Selected employees">
          <strong>{selectedEmployees.length} selected</strong>
          <div className={register.actions}>
            <button type="button" onClick={() => openVerify(selectedEmployees)} disabled={Boolean(busyAction)}><MailCheck aria-hidden="true" size={15} /> Invite selected</button>
            {selectedEmployees.some((item) => item.access_review === "pending") ? <button type="button" onClick={() => askExclude(selectedEmployees.filter((item) => item.access_review === "pending"))} disabled={Boolean(busyAction)}><MailX aria-hidden="true" size={15} /> Don&apos;t invite</button> : null}
            <button type="button" onClick={() => setSelected(new Set())}>Clear selection</button>
          </div>
        </div> : null}
          {rows.length ? (
            <div className={styles.tableWrap}>
              <table className={register.table}>
                <thead><tr>
                  <th className={local.check}>{selectableVisible.length ? <input type="checkbox" aria-label={allVisibleSelected ? "Clear selection" : `Select all ${selectableVisible.length} employees awaiting a decision`} checked={allVisibleSelected} onChange={toggleAllVisible} /> : null}</th>
                  <th>Person</th><th>Department</th><th>Reports to</th><th>Access</th><th>Status</th><th>Actions</th>
                </tr></thead>
                {groupByDepartment ? departmentGroups.map((group) => {
                  const open = expandedDepartments.has(group.name) || Boolean(normalizedSearch);
                  const invitable = group.rows.filter((row) => row.employee?.access_review === "pending" && row.employee.email).map((row) => row.employee as HrEmployee);
                  const awaiting = group.rows.filter((row) => row.state === "awaiting").length;
                  return <tbody key={group.name} className={local.group} data-open={open}>
                    <tr className={local.groupHeader}><td colSpan={7}><div className={local.groupBar}>
                      <button type="button" className={local.groupToggle} aria-expanded={open} onClick={() => toggleDepartment(group.name)}>
                        <ChevronRight aria-hidden="true" size={16} />
                        <span><strong>{group.name}</strong><small>{group.rows.length} {group.rows.length === 1 ? "person" : "people"}{awaiting ? ` · ${awaiting} awaiting verification` : ""}{group.locations.length ? ` · ${group.locations.slice(0, 3).join(", ")}${group.locations.length > 3 ? ` +${group.locations.length - 3}` : ""}` : ""}</small></span>
                      </button>
                      {invitable.length ? <button type="button" className={styles.primary} onClick={() => openVerify(invitable, group.name)} disabled={Boolean(busyAction)}><MailCheck aria-hidden="true" size={15} /> Invite all {invitable.length}</button> : null}
                    </div></td></tr>
                    {open ? group.rows.map(renderRow) : null}
                  </tbody>;
                }) : <tbody>{visibleRows.map(renderRow)}</tbody>}
              </table>
              {visibleRows.length === 0 ? <p className={styles.noResults}>No people match these filters.</p> : null}
            </div>
          ) : <div className={styles.empty}><Users aria-hidden="true" size={24} /><p>No members are visible in this workspace yet.</p></div>}
      </section>

      <SidePanel open={inviteOpen} onClose={() => setInviteOpen(false)} title="Invite people" description={`They will join ${session.tenant.name}. Choose Contributor for standard access.`}>
          <form className={styles.form} onSubmit={invite}>
            <div className={styles.field}><label htmlFor="invite-name">Full name</label><input id="invite-name" name="display_name" required minLength={2} autoComplete="name" /></div>
            <div className={styles.field}><label htmlFor="invite-email">Work email</label><input id="invite-email" name="email" type="email" required autoComplete="email" /></div>
            <SelectField id="invite-role" name="role_key" label="Access role" value={roleKey} onChange={setRoleKey} disabled={submitting || grantableRoles.length === 0} options={grantableRoles.map((role) => ({ value: role.key, label: role.name, description: role.description }))} />
            <button className={styles.primary} type="submit" disabled={submitting || grantableRoles.length === 0}><MailPlus aria-hidden="true" size={16} /> {submitting ? "Sending" : "Send invitation"}</button>
          </form>
      </SidePanel>
      <SidePanel open={Boolean(roleTarget)} onClose={() => setRoleTarget(null)} title="Change role" description={roleTarget ? `${roleTarget.display_name} is currently a ${roleNames(roleTarget.roles)}. The change takes effect immediately and is recorded in the audit log.` : ""}>
          <div className={styles.form}>
            <SelectField id="change-role" name="role_key" label="New role" value={nextRole} onChange={setNextRole} disabled={Boolean(busyAction)} options={grantableRoles.map((role) => ({ value: role.key, label: role.name, description: role.description }))} />
            <button className={styles.primary} type="button" onClick={() => void changeRole()} disabled={Boolean(busyAction) || !roleTarget || roleTarget.roles.includes(nextRole)}><ShieldCheck aria-hidden="true" size={16} /> {busyAction.startsWith("role:") ? "Saving" : "Save role"}</button>
          </div>
      </SidePanel>
      <SidePanel open={Boolean(verifyTargets)} onClose={() => setVerifyTargets(null)} title={verifyTargets?.length === 1 ? `Invite ${verifyTargets[0].display_name}` : verifyGroup ? `Invite all in ${verifyGroup}` : `Invite ${verifyTargets?.length ?? 0} employees`} description="Inviting verifies these employees from the HR file. Each person gets an email invitation and becomes Active once they accept it.">
          {verifyTargets ? <div className={styles.form}>
            <ul className={local.selectedList}>{verifyTargets.slice(0, 50).map((item) => <li key={item.employee_id}><strong>{item.display_name}</strong> <small>{item.employee_id} · {item.email ?? "no email"}{item.department ? ` · ${item.department}` : ""}</small></li>)}{verifyTargets.length > 50 ? <li><small>and {verifyTargets.length - 50} more</small></li> : null}</ul>
            {verifyTargets.some((item) => !item.email) ? <p className={local.warn}>{verifyTargets.filter((item) => !item.email).length} without an allowed company email will be skipped.</p> : null}
            <SelectField id="verify-role" name="role_key" label="Access role" value={verifyRole} onChange={setVerifyRole} disabled={busyAction === "verify" || inviteRoles.length === 0} options={inviteRoles.map((role) => ({ value: role.key, label: role.name, description: role.description }))} />
            <p className={local.note}>Admin roles are granted afterwards with Change role, once the person has joined.</p>
            <button className={styles.primary} type="button" onClick={() => void verify()} disabled={Boolean(busyAction) || inviteRoles.length === 0}><MailCheck aria-hidden="true" size={16} /> {busyAction === "verify" ? "Sending" : `Send ${verifyTargets.filter((item) => item.email).length} invitation${verifyTargets.filter((item) => item.email).length === 1 ? "" : "s"}`}</button>
          </div> : null}
      </SidePanel>
      <ConfirmDialog open={Boolean(confirmation)} title={confirmation?.title ?? "Confirm action"} description={confirmation?.description ?? ""} confirmLabel={confirmation?.confirmLabel ?? "Confirm"} busyLabel={confirmation?.busyLabel} busy={confirming} onClose={() => setConfirmation(null)} onConfirm={confirmAction} />
    </AdminShell>
  );
}
