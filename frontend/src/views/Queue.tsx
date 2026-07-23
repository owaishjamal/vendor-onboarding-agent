import { useEffect, useState } from "react";
import { Case, Status, STATUS_META, api, shortTime, flag, ageOf, ageDays } from "../api";
import { StatusBadge, Stat } from "../components/Badges";

const FILTERS: (Status | "ALL")[] =
  ["ALL", "PENDING_REVIEW", "PENDING_INFO", "APPROVED", "REJECTED"];

/**
 * The reviewer queue.
 *
 * Ordered so that PENDING_REVIEW surfaces first by default, because that is
 * the only bucket where someone is actually blocked on a human. Approved cases
 * need nobody, and pending-info cases are waiting on the vendor — showing them
 * with equal weight is how a queue becomes noise.
 */
export default function Queue({ nonce, onOpen }: { nonce: number; onOpen: (id: string) => void }) {
  const [cases, setCases] = useState<Case[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [ov, setOv] = useState<any>(null);
  const [filter, setFilter] = useState<Status | "ALL">("ALL");

  useEffect(() => {
    api.cases().then(setCases).catch(() => {});
    api.stats().then(setStats).catch(() => {});
    api.overrides().then(setOv).catch(() => {});
  }, [nonce]);

  const norm = (s: string) => s.replace("_BY_REVIEWER", "");
  const rank = (s: string) =>
    ({ PENDING_REVIEW: 0, REJECTED: 1, PENDING_INFO: 2, APPROVED: 3 } as Record<string, number>)[norm(s)] ?? 4;

  const shown = (filter === "ALL" ? cases : cases.filter((c) => norm(c.status) === filter))
    .slice()
    .sort((a, b) => rank(a.status) - rank(b.status));

  const counts = cases.reduce<Record<string, number>>((a, c) => {
    const k = norm(c.status);
    a[k] = (a[k] ?? 0) + 1; return a;
  }, {});

  const awaitingUs = counts["PENDING_REVIEW"] ?? 0;

  // Oldest case still open (not approved/rejected/superseded) — surfaces the
  // ones that have been waiting, which a flat list hides.
  const openCases = cases.filter(
    (c) => ["PENDING_REVIEW", "PENDING_INFO"].includes(norm(c.status)) && !c.superseded_by);
  const oldestOpen = openCases.length
    ? openCases.reduce((a, b) => (a.created_at < b.created_at ? a : b))
    : null;

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Submissions" value={stats?.total_cases ?? 0} />
        <Stat label="Auto-approved" value={stats?.auto_approved ?? 0}
              sub={stats?.total_cases ? `${(100 - (stats.touch_rate ?? 0)).toFixed(0)}% straight through` : undefined} />
        <Stat label="Awaiting our review" value={awaitingUs}
              sub="blocked on a human decision" />
        <Stat label="Oldest open case"
              value={oldestOpen ? ageOf(oldestOpen.created_at) : "—"}
              sub={oldestOpen ? oldestOpen.legal_name : "nothing waiting"} />
      </div>

      {ov && ov.override_count > 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-4">
          <div className="flex items-baseline justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-amber-800">
              Reviewer overrides — where humans disagreed with the system
            </h3>
            <span className="text-[11px] text-amber-700">
              {ov.override_count} of {ov.resolved_cases} resolved ({ov.override_rate}%)
            </span>
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {Object.entries(ov.by_check).map(([ck, n]: any) => (
              <span key={ck} className="flex items-center gap-1.5 rounded-lg bg-white px-2 py-1 ring-1 ring-inset ring-amber-200">
                <span className="text-[11px] font-medium text-slate-700">{ck}</span>
                <span className="rounded bg-amber-500 px-1 text-[10px] font-bold text-white tabular-nums">{n}</span>
              </span>
            ))}
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-amber-700/80">
            A check overridden often is mis-calibrated — crying wolf, or missing things.
            This is the feedback loop the audit log makes possible: the resolutions tell
            you which threshold to revisit.
          </p>
        </div>
      )}

      {!!stats?.top_finding_codes?.length && (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Most common blocking findings
          </h3>
          <div className="flex flex-wrap gap-2">
            {stats.top_finding_codes.map((c: any) => (
              <span key={c.code} className="flex items-center gap-1.5 rounded-lg bg-slate-100 px-2 py-1">
                <span className="font-mono text-[10.5px] text-slate-700">{c.code}</span>
                <span className="rounded bg-slate-700 px-1 text-[10px] font-bold text-white tabular-nums">{c.n}</span>
              </span>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-slate-400">
            A fixed vocabulary of findings means you can count them — and go fix whatever
            upstream is causing the most common one.
          </p>
        </div>
      )}

      <div className="rounded-xl border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-200 px-4 py-3">
          {FILTERS.map((f) => {
            const n = f === "ALL" ? cases.length : counts[f] ?? 0;
            const on = filter === f;
            return (
              <button key={f} onClick={() => setFilter(f)}
                className={`rounded-full px-2.5 py-1 text-xs font-medium transition ${
                  on ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>
                {f === "ALL" ? "All" : STATUS_META[f].label}
                <span className={`ml-1.5 tabular-nums ${on ? "text-slate-300" : "text-slate-400"}`}>{n}</span>
              </button>
            );
          })}
        </div>

        {!shown.length ? (
          <div className="px-4 py-12 text-center text-sm text-slate-500">
            {cases.length ? "No cases match this filter." : "No submissions yet — run one from the Intake tab."}
          </div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {shown.map((c) => (
              <li key={c.case_id}
                  onClick={() => onOpen(c.case_id)}
                  className={`cursor-pointer px-4 py-3 transition hover:bg-slate-50 ${
                    c.superseded_by ? "opacity-50" : ""}`}>
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge s={c.status} size="sm" />
                      <span className="truncate text-sm font-medium text-slate-800">{c.legal_name}</span>
                      <span className="text-[11px] text-slate-400">{flag(c.country)}</span>
                      {(c.revision ?? 1) > 1 && (
                        <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-[9.5px] font-semibold text-indigo-700">
                          rev {c.revision}
                        </span>
                      )}
                      {c.superseded_by && (
                        <span className="rounded bg-slate-200 px-1.5 py-0.5 text-[9.5px] font-semibold text-slate-500">
                          superseded
                        </span>
                      )}
                    </div>
                    {c.top_finding && (
                      <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-500">
                        <span className="font-mono text-[10px] text-slate-400">{c.top_finding.code}</span>
                        {" — "}{c.top_finding.message}
                      </p>
                    )}
                    {!!c.finding_counts && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {Object.entries(c.finding_counts)
                          .filter(([k]) => k !== "INFO")
                          .map(([k, n]) => (
                            <span key={k} className="rounded bg-slate-100 px-1.5 py-0.5 text-[9.5px] font-medium text-slate-600">
                              {n}× {k.replace("_", " ").toLowerCase()}
                            </span>
                          ))}
                      </div>
                    )}
                  </div>
                  <div className="shrink-0 text-right">
                    {(() => {
                      const open = ["PENDING_REVIEW", "PENDING_INFO"].includes(norm(c.status));
                      const stale = open && ageDays(c.created_at) > 7;
                      return (
                        <div className={`text-[11px] font-medium ${stale ? "text-rose-600" : "text-slate-500"}`}>
                          {ageOf(c.created_at)}{stale ? " · stale" : ""}
                        </div>
                      );
                    })()}
                    <div className="text-[10px] text-slate-400">{shortTime(c.created_at)}</div>
                    {c.vendor_email && (
                      <div className="mt-1 text-[10px] font-medium text-sky-600">email drafted</div>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
