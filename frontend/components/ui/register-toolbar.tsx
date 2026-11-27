"use client";

import { Search, SlidersHorizontal, X } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import styles from "./register-toolbar.module.css";

type Tone = "neutral" | "info" | "success" | "warning" | "danger";
export type RegisterView = { key: string; label: string; count: number; tone?: Tone; hint?: string };

type Props = {
  label: string;
  views: RegisterView[];
  activeView: string;
  onViewChange: (key: string) => void;
  query: string;
  onQueryChange: (value: string) => void;
  searchLabel: string;
  searchPlaceholder: string;
  resultCount: number;
  /** How many dropdown filters are set; the panel opens itself while any are. */
  filterCount: number;
  onClear: () => void;
  /** The dropdown filters, shown in a collapsible panel. */
  children: ReactNode;
};

/**
 * One compact block for a register page: views that double as the headline counts,
 * a search line, and the detailed filters folded away until needed.
 */
export function RegisterToolbar({ label, views, activeView, onViewChange, query, onQueryChange, searchLabel, searchPlaceholder, resultCount, filterCount, onClear, children }: Props) {
  const searchId = useId();
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const expanded = open || filterCount > 0;
  const anything = Boolean(query) || filterCount > 0;

  return (
    <section className={styles.toolbar} aria-label={label}>
      <div className={styles.views} role="group" aria-label="Views">
        {views.map((view) => (
          <button key={view.key} type="button" aria-pressed={activeView === view.key} data-tone={view.tone ?? "neutral"} title={view.hint} onClick={() => onViewChange(view.key)}>
            <span className={styles.dot} aria-hidden="true" />
            <span>{view.label}</span>
            <b>{view.count}</b>
          </button>
        ))}
      </div>
      <div className={styles.row}>
        <label className={styles.search} htmlFor={searchId}>
          <span className={styles.srOnly}>{searchLabel}</span>
          <Search size={16} aria-hidden="true" />
          <input id={searchId} type="search" value={query} placeholder={searchPlaceholder} autoComplete="off" onChange={(event) => onQueryChange(event.target.value)} />
          {query ? <button type="button" aria-label={`Clear ${searchLabel.toLowerCase()}`} onClick={() => onQueryChange("")}><X size={15} aria-hidden="true" /></button> : null}
        </label>
        <button type="button" className={styles.filterToggle} aria-expanded={expanded} aria-controls={panelId} data-active={filterCount > 0} onClick={() => setOpen((value) => !value || filterCount > 0)}>
          <SlidersHorizontal size={15} aria-hidden="true" />Filters{filterCount ? <b>{filterCount}</b> : null}
        </button>
        {anything ? <button type="button" className={styles.clear} onClick={() => { onClear(); setOpen(false); }}><X size={14} aria-hidden="true" />Clear</button> : null}
        <span className={styles.count} aria-live="polite">{resultCount} shown</span>
      </div>
      <div id={panelId} className={styles.panel} hidden={!expanded}>{children}</div>
    </section>
  );
}
