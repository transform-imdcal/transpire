"use client";

import { Check, HandHeart, Search, Send, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { apiRequest } from "@/lib/api";
import styles from "./idea-endorsements.module.css";

type Endorsement = {
  id: string;
  endorser_user_id: string;
  endorser_name: string;
  status: "requested" | "endorsed" | "declined";
  message: string;
  comment: string;
  requested_at: string;
  responded_at: string | null;
  is_me: boolean;
};
type Panel = { endorsed_count: number; can_request: boolean; request_blocked_reason: string | null; items: Endorsement[] };
type Candidate = { user_id: string; display_name: string; email: string };

const errorText = async (response: Response, fallback: string) =>
  ((await response.json().catch(() => null)) as { detail?: string } | null)?.detail ?? fallback;

/**
 * Endorsements on an idea: who backs it, the owner's requests, and the viewer's own answer.
 * Support only; nothing here changes the approval route.
 */
export function IdeaEndorsements({ ideaId, viewerId, headingLevel = 2 }: { ideaId: string; viewerId: string; headingLevel?: 2 | 3 }) {
  const headingId = useId();
  const [panel, setPanel] = useState<Panel | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    const response = await apiRequest(`/ideas/${ideaId}/endorsements`);
    if (response.status === 403) return setPanel(null);
    if (!response.ok) return setError("Endorsements couldn't be loaded.");
    setPanel(await response.json() as Panel);
  }, [ideaId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  if (!panel) return error ? <p className={styles.error} role="alert">{error}</p> : null;
  const endorsed = panel.items.filter((item) => item.status === "endorsed");
  const mine = panel.items.find((item) => item.is_me);
  const isOwner = panel.can_request || panel.request_blocked_reason !== null;
  const outstanding = isOwner ? panel.items.filter((item) => item.status !== "endorsed") : [];
  const Heading = headingLevel === 2 ? "h2" : "h3";

  return (
    <section className={styles.panel} aria-labelledby={headingId}>
      <header>
        <span className={styles.icon} aria-hidden="true"><HandHeart size={18} /></span>
        <div>
          <Heading id={headingId}>Endorsements</Heading>
          <p>{panel.endorsed_count ? `${panel.endorsed_count} ${panel.endorsed_count === 1 ? "colleague backs" : "colleagues back"} this idea.` : "No endorsements yet."} Endorsements show support; they don&apos;t approve or block anything.</p>
        </div>
      </header>

      {mine ? <MyAnswer ideaId={ideaId} endorsement={mine} onDone={load} /> : null}

      {endorsed.length ? (
        <ul className={styles.list}>
          {endorsed.map((item) => (
            <li key={item.id}>
              <span className={styles.avatar} aria-hidden="true">{initials(item.endorser_name)}</span>
              <div>
                <strong>{item.endorser_name}{item.is_me ? " (you)" : ""}</strong>
                {item.comment ? <q>{item.comment}</q> : null}
                <small>Endorsed {formatDate(item.responded_at ?? item.requested_at)}</small>
              </div>
              <ThumbsUp size={15} className={styles.endorsedMark} aria-label="Endorsed" />
            </li>
          ))}
        </ul>
      ) : null}

      {isOwner ? (
        <>
          {outstanding.length ? <Outstanding ideaId={ideaId} items={outstanding} onChanged={load} /> : null}
          {panel.can_request
            ? <RequestForm ideaId={ideaId} viewerId={viewerId} alreadyAsked={panel.items.map((item) => item.endorser_user_id)} onSent={load} />
            : panel.request_blocked_reason ? <p className={styles.note}>{panel.request_blocked_reason}</p> : null}
        </>
      ) : null}
    </section>
  );
}

function MyAnswer({ ideaId, endorsement, onDone }: { ideaId: string; endorsement: Endorsement; onDone: () => Promise<void> }) {
  const [comment, setComment] = useState(endorsement.comment);
  const [editing, setEditing] = useState(endorsement.status === "requested");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const answer = async (decision: "endorse" | "decline") => {
    setBusy(decision); setError("");
    try {
      const response = await apiRequest(`/ideas/${ideaId}/endorsements/${endorsement.id}/respond`, { method: "POST", body: JSON.stringify({ decision, comment }) });
      if (!response.ok) throw new Error(await errorText(response, "Your answer didn't go through. Try again."));
      window.dispatchEvent(new Event("transpire:inbox-changed"));
      setEditing(false);
      await onDone();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Your answer didn't go through."); }
    finally { setBusy(""); }
  };

  if (!editing) {
    return (
      <p className={styles.mine} data-status={endorsement.status}>
        {endorsement.status === "endorsed" ? <Check size={15} aria-hidden="true" /> : <X size={15} aria-hidden="true" />}
        {endorsement.status === "endorsed" ? "You endorsed this idea." : "You declined to endorse this idea."}
        <button type="button" onClick={() => setEditing(true)}>Change answer</button>
      </p>
    );
  }
  return (
    <div className={styles.ask}>
      <p><strong>You&apos;ve been asked to endorse this idea.</strong>{endorsement.message ? <q>{endorsement.message}</q> : null}</p>
      <label>
        <span>Comment <em>Optional</em></span>
        <textarea rows={2} maxLength={600} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Why you support it, or what reviewers should know." />
      </label>
      <div className={styles.actions}>
        <button type="button" className={styles.primary} disabled={Boolean(busy)} onClick={() => void answer("endorse")}><ThumbsUp size={15} aria-hidden="true" />{busy === "endorse" ? "Endorsing" : "Endorse"}</button>
        <button type="button" className={styles.secondary} disabled={Boolean(busy)} onClick={() => void answer("decline")}><ThumbsDown size={15} aria-hidden="true" />{busy === "decline" ? "Declining" : "Decline"}</button>
        {endorsement.status !== "requested" ? <button type="button" className={styles.link} onClick={() => setEditing(false)}>Cancel</button> : null}
      </div>
      {error ? <p className={styles.error} role="alert">{error}</p> : null}
    </div>
  );
}

function Outstanding({ ideaId, items, onChanged }: { ideaId: string; items: Endorsement[]; onChanged: () => Promise<void> }) {
  const [busy, setBusy] = useState("");
  const withdraw = async (item: Endorsement) => {
    setBusy(item.id);
    try {
      await apiRequest(`/ideas/${ideaId}/endorsements/${item.id}`, { method: "DELETE" });
      await onChanged();
    } finally { setBusy(""); }
  };
  return (
    <div className={styles.outstanding}>
      <h4>Your requests</h4>
      <ul>
        {items.map((item) => (
          <li key={item.id}>
            <span>{item.endorser_name}</span>
            <em data-status={item.status}>{item.status === "requested" ? "Waiting" : "Declined"}</em>
            {item.status === "requested" ? <button type="button" disabled={busy === item.id} onClick={() => void withdraw(item)}>{busy === item.id ? "Withdrawing" : "Withdraw"}</button> : item.comment ? <small>“{item.comment}”</small> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RequestForm({ ideaId, viewerId, alreadyAsked, onSent }: { ideaId: string; viewerId: string; alreadyAsked: string[]; onSent: () => Promise<void> }) {
  const searchId = useId();
  const [open, setOpen] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [query, setQuery] = useState("");
  const [chosen, setChosen] = useState<Candidate[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState("");

  useEffect(() => {
    if (!open || candidates) return;
    void apiRequest("/projects/team-candidates").then(async (response) => {
      if (response.ok) setCandidates(await response.json() as Candidate[]);
      else setError("Colleagues couldn't be loaded.");
    });
  }, [candidates, open]);

  const matches = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    const excluded = new Set([viewerId, ...alreadyAsked, ...chosen.map((person) => person.user_id)]);
    return (candidates ?? [])
      .filter((person) => !excluded.has(person.user_id))
      .filter((person) => !term || person.display_name.toLocaleLowerCase().includes(term) || person.email.toLocaleLowerCase().includes(term))
      .slice(0, 6);
  }, [alreadyAsked, candidates, chosen, query, viewerId]);

  const send = async () => {
    setBusy(true); setError("");
    try {
      const response = await apiRequest(`/ideas/${ideaId}/endorsements`, { method: "POST", body: JSON.stringify({ user_ids: chosen.map((person) => person.user_id), message }) });
      if (!response.ok) throw new Error(await errorText(response, "The requests couldn't be sent."));
      setSent(`Asked ${chosen.length} ${chosen.length === 1 ? "colleague" : "colleagues"}. They'll get an email and see it in their Inbox.`);
      setChosen([]); setMessage(""); setQuery(""); setOpen(false);
      await onSent();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The requests couldn't be sent."); }
    finally { setBusy(false); }
  };

  if (!open) {
    return (
      <div className={styles.requestStart}>
        {sent ? <p className={styles.sent} role="status"><Check size={15} aria-hidden="true" />{sent}</p> : null}
        <button type="button" className={styles.secondary} onClick={() => { setOpen(true); setSent(""); }}><Send size={15} aria-hidden="true" />Request endorsements</button>
      </div>
    );
  }
  return (
    <div className={styles.request}>
      <label htmlFor={searchId}><strong>Ask colleagues to endorse your idea</strong><small>Pick people who know the problem: they get an email and an Inbox item.</small></label>
      {chosen.length ? (
        <ul className={styles.chips} aria-label="Chosen colleagues">
          {chosen.map((person) => <li key={person.user_id}>{person.display_name}<button type="button" aria-label={`Remove ${person.display_name}`} onClick={() => setChosen((current) => current.filter((entry) => entry.user_id !== person.user_id))}><X size={13} aria-hidden="true" /></button></li>)}
        </ul>
      ) : null}
      <div className={styles.search}>
        <Search size={15} aria-hidden="true" />
        <input id={searchId} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={candidates ? "Search by name or email" : "Loading colleagues"} autoComplete="off" />
      </div>
      {matches.length ? (
        <ul className={styles.results} aria-label="Matching colleagues">
          {matches.map((person) => <li key={person.user_id}><button type="button" onClick={() => { setChosen((current) => [...current, person]); setQuery(""); }}><strong>{person.display_name}</strong><small>{person.email}</small></button></li>)}
        </ul>
      ) : null}
      <label className={styles.messageField}><span>Note to them <em>Optional</em></span><textarea rows={2} maxLength={500} value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Why their view matters for this idea." /></label>
      <div className={styles.actions}>
        <button type="button" className={styles.primary} disabled={busy || chosen.length === 0} onClick={() => void send()}><Send size={15} aria-hidden="true" />{busy ? "Sending" : chosen.length ? `Send ${chosen.length} ${chosen.length === 1 ? "request" : "requests"}` : "Send requests"}</button>
        <button type="button" className={styles.link} onClick={() => { setOpen(false); setChosen([]); setError(""); }}>Cancel</button>
      </div>
      {error ? <p className={styles.error} role="alert">{error}</p> : null}
    </div>
  );
}

function initials(name: string) {
  return name.split(" ").filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("");
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" }).format(new Date(value));
}
