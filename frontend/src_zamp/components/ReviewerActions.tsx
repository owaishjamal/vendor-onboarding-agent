import { useState } from "react";
import { Case, CaseAction, ReviewerAction, api, shortTime } from "../api";

/**
 * Reviewer actions + the action history.
 *
 * This is the piece that stops the tool being a read-only viewer. The status
 * the checks produced is a recommendation; a human confirms it, and that
 * decision is recorded here rather than in an inbox. The available actions
 * depend on where the case is: a pending-review case can be approved or
 * rejected; a pending-info case can have its request sent; an already-decided
 * case can be reopened.
 */

const ACTIONS: Record<string, { key: ReviewerAction; label: string; cls: string }[]> = {
  PENDING_REVIEW: [
    { key: "approve", label: "Approve", cls: "bg-emerald-600 hover:bg-emerald-700" },
    { key: "reject", label: "Reject", cls: "bg-rose-600 hover:bg-rose-700" },
    { key: "request_info", label: "Request info from vendor", cls: "bg-sky-600 hover:bg-sky-700" },
  ],
  PENDING_INFO: [
    { key: "resolve", label: "Mark email sent", cls: "bg-sky-600 hover:bg-sky-700" },
    { key: "reject", label: "Reject", cls: "bg-rose-600 hover:bg-rose-700" },
  ],
  REJECTED: [
    { key: "reopen", label: "Reopen", cls: "bg-slate-700 hover:bg-slate-800" },
  ],
  APPROVED: [
    { key: "reopen", label: "Reopen", cls: "bg-slate-700 hover:bg-slate-800" },
  ],
};

export default function ReviewerActions({ c, onActed }: { c: Case; onActed: () => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const base = c.status.replace("_BY_REVIEWER", "");
  const options = ACTIONS[base] ?? ACTIONS[c.status] ?? [];
  const decided = c.resolution && c.status.includes("_BY_REVIEWER");

  const act = async (action: ReviewerAction) => {
    setBusy(true);
    try { await api.action(c.case_id, action, note || undefined); setNote(""); onActed(); }
    finally { setBusy(false); }
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-800">Reviewer decision</h3>

      {decided ? (
        <p className="mt-1 text-xs text-slate-500">
          Resolved as <span className="font-semibold">{c.resolution}</span>. You can reopen if this needs revisiting.
        </p>
      ) : (
        <p className="mt-1 text-xs text-slate-500">
          Record the outcome. This writes to the case's audit trail, not to an inbox.
        </p>
      )}

      {options.length > 0 && (
        <>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Optional note — e.g. 'Confirmed account holder by phone using the number on file.'"
            className="mt-3 h-16 w-full resize-none rounded-lg border border-slate-300 bg-white p-2 text-xs text-slate-800 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100"
          />
          <div className="mt-2 flex flex-wrap gap-2">
            {options.map((o) => (
              <button
                key={o.key}
                disabled={busy}
                onClick={() => act(o.key)}
                className={`rounded-lg px-3 py-1.5 text-xs font-semibold text-white transition disabled:opacity-50 ${o.cls}`}
              >
                {o.label}
              </button>
            ))}
          </div>
        </>
      )}

      {!!c.actions?.length && (
        <div className="mt-4 border-t border-slate-100 pt-3">
          <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
            Action history
          </div>
          <ul className="space-y-1.5">
            {c.actions.map((a: CaseAction, i) => (
              <li key={i} className="flex items-start gap-2 text-xs">
                <span className="mt-0.5 font-mono text-[10px] text-slate-400">{shortTime(a.created_at)}</span>
                <div>
                  <span className="font-medium text-slate-800">{a.reviewer}</span>
                  <span className="text-slate-500"> — {a.action.replace("_", " ")}</span>
                  <span className="text-slate-400"> ({a.prev_status} → {a.new_status})</span>
                  {a.note && <div className="text-slate-500">“{a.note}”</div>}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
