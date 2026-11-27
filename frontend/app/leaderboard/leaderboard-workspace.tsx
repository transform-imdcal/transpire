"use client";

import { Building2, Info, RefreshCw, Trophy, UserRound } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "@/components/admin/admin-shell";
import { SelectField } from "@/components/ui/select-field";
import { apiRequest } from "@/lib/api";
import { useSession } from "@/lib/use-session";
import { useTenantRouter as useRouter } from "@/lib/use-tenant-router";
import styles from "./leaderboard.module.css";

type Row = {
  rank: number;
  key: string;
  name: string;
  detail: string | null;
  submitted: number;
  approved: number;
  implemented: number;
  savings: string | null;
  validated: string | null;
  estimated: string | null;
  is_me: boolean;
};
type Board = {
  financial_year: number;
  financial_years: number[];
  quarter: Quarter;
  idea_type: IdeaType;
  period_label: string;
  shows_savings: boolean;
  ranked_by: "savings" | "delivery";
  employees: Row[];
  sites: Row[];
  totals: { submitted: number; approved: number; implemented: number; savings: string | null };
};
type Quarter = "all" | "q1" | "q2" | "q3" | "q4";
type IdeaType = "all" | "kaizen" | "project";

const quarters: Array<{ value: Quarter; label: string; hint: string }> = [
  { value: "all", label: "Full year", hint: "April to March" },
  { value: "q1", label: "Q1", hint: "Apr–Jun" },
  { value: "q2", label: "Q2", hint: "Jul–Sep" },
  { value: "q3", label: "Q3", hint: "Oct–Dec" },
  { value: "q4", label: "Q4", hint: "Jan–Mar" },
];
const types: Array<{ value: IdeaType; label: string }> = [
  { value: "all", label: "All ideas" },
  { value: "kaizen", label: "Kaizen" },
  { value: "project", label: "Projects" },
];

export function LeaderboardWorkspace() {
  const router = useRouter();
  const { session, loading } = useSession();
  const [board, setBoard] = useState<Board | null>(null);
  const [year, setYear] = useState<number | null>(null);
  const [quarter, setQuarter] = useState<Quarter>("all");
  const [ideaType, setIdeaType] = useState<IdeaType>("all");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setBusy(true); setError("");
    try {
      const params = new URLSearchParams({ quarter, idea_type: ideaType });
      if (year) params.set("financial_year", String(year));
      const response = await apiRequest(`/ideas/leaderboard?${params.toString()}`);
      if (response.status === 401) return router.replace("/sign-in");
      if (!response.ok) throw new Error();
      setBoard(await response.json() as Board);
    } catch {
      setError("The leaderboard couldn't be loaded. Check your connection, then retry.");
    } finally {
      setBusy(false);
    }
  }, [ideaType, quarter, router, year]);

  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load, session]);

  if (loading || !session) return <main className={styles.loading}>Opening the Leaderboard</main>;
  const money = board?.shows_savings ?? false;

  return (
    <AdminShell session={session} active="leaderboard" title="Leaderboard" description="The people and sites delivering the most improvement value.">
      <section className={styles.controls} aria-label="Leaderboard period and type">
        <div className={styles.year}>
          <SelectField
            id="leaderboard-year"
            name="financial_year"
            label="Financial year"
            value={String(board?.financial_year ?? year ?? "")}
            onChange={(value) => setYear(Number(value))}
            options={(board?.financial_years ?? []).map((value) => ({ value: String(value), label: `FY ${value}–${String(value + 1).slice(-2)}`, description: `April ${value} to March ${value + 1}` }))}
          />
        </div>
        <Segmented label="Quarter" options={quarters.map((entry) => ({ value: entry.value, label: entry.label, title: entry.hint }))} value={quarter} onChange={(value) => setQuarter(value as Quarter)} />
        <Segmented label="Idea type" options={types} value={ideaType} onChange={(value) => setIdeaType(value as IdeaType)} />
      </section>

      {error ? <div className={styles.error} role="alert"><span>{error}</span><button type="button" onClick={() => void load()}><RefreshCw size={14} aria-hidden="true" />Retry</button></div> : null}

      {board ? (
        <>
          <dl className={styles.totals} aria-label={`Totals for ${board.period_label}`} data-busy={busy}>
            <div><dt>Period</dt><dd>{board.period_label}</dd></div>
            <div><dt>Ideas submitted</dt><dd>{board.totals.submitted}</dd></div>
            <div><dt>Approved</dt><dd>{board.totals.approved}</dd></div>
            <div><dt>Implemented</dt><dd>{board.totals.implemented}</dd></div>
            {money ? <div><dt>Savings</dt><dd>{lakhs(board.totals.savings)}</dd></div> : null}
          </dl>

          <div className={styles.boards} data-busy={busy}>
            <Ranking title="People" icon={<UserRound size={17} />} rows={board.employees} money={money} empty="No ideas were submitted in this period." />
            <Ranking title="Sites" icon={<Building2 size={17} />} rows={board.sites} money={money} empty="No site activity in this period." />
          </div>

          <p className={styles.rules}>
            <Info size={15} aria-hidden="true" />
            <span>
              {money
                ? "Ranked by savings: Finance-validated benefit where a project has it, otherwise the annual estimate (saving plus cost avoidance) the idea was submitted with. "
                : "Ranked by ideas implemented, then approved. Savings are hidden because your workspace doesn't share financial estimates in the Idea Bank. "}
              Ideas count in the financial-year quarter they were submitted, and are credited to their owner and their site. Drafts, withdrawn and rejected ideas don&apos;t count.
            </span>
          </p>
        </>
      ) : busy ? <div className={styles.skeleton} aria-label="Loading the leaderboard"><span /><span /></div> : null}
    </AdminShell>
  );
}

function Segmented({ label, options, value, onChange }: { label: string; options: Array<{ value: string; label: string; title?: string }>; value: string; onChange: (value: string) => void }) {
  return (
    <div className={styles.segmentedField}>
      <span>{label}</span>
      <div className={styles.segmented} role="group" aria-label={label}>
        {options.map((option) => <button key={option.value} type="button" title={option.title} aria-pressed={value === option.value} onClick={() => onChange(option.value)}>{option.label}</button>)}
      </div>
    </div>
  );
}

function Ranking({ title, icon, rows, money, empty }: { title: string; icon: React.ReactNode; rows: Row[]; money: boolean; empty: string }) {
  const top = rows.reduce((best, row) => Math.max(best, money ? Number(row.savings ?? 0) : row.implemented * 1000 + row.approved), 0) || 1;
  return (
    <section className={styles.board} aria-label={`${title} leaderboard`}>
      <header><span aria-hidden="true">{icon}</span><h2>{title}</h2><small>Top {rows.length || 0}</small></header>
      {rows.length ? (
        <ol>
          {rows.map((row) => {
            const score = money ? Number(row.savings ?? 0) : row.implemented * 1000 + row.approved;
            return (
              <li key={row.key} data-me={row.is_me} data-podium={row.rank <= 3 ? row.rank : undefined}>
                <span className={styles.rank} aria-label={`Rank ${row.rank}`}>{row.rank <= 3 ? <Trophy size={14} aria-hidden="true" /> : null}{row.rank}</span>
                <div className={styles.who}>
                  <strong>{row.name}{row.is_me ? <em>You</em> : null}</strong>
                  <small>{row.submitted} submitted · {row.approved} approved · {row.implemented} implemented</small>
                  <span className={styles.bar} aria-hidden="true"><i style={{ width: `${Math.max(3, (score / top) * 100)}%` }} /></span>
                </div>
                <div className={styles.score}>
                  {money ? <><strong>{lakhs(row.savings)}</strong><small>{Number(row.validated ?? 0) > 0 ? `${lakhs(row.validated)} validated` : "Estimated"}</small></> : <><strong>{row.implemented}</strong><small>implemented</small></>}
                </div>
              </li>
            );
          })}
        </ol>
      ) : <p className={styles.empty}>{empty}</p>}
    </section>
  );
}

function lakhs(value: string | null) {
  const number = Number(value ?? 0);
  if (number >= 100) return `₹${(number / 100).toFixed(2)} Cr`;
  return `₹${number.toFixed(number >= 10 ? 1 : 2)} L`;
}
