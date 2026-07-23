import { useState } from "react";
import { CheckResult, CheckPlan, SeverityName } from "../api";
import { CheckIcon } from "./Badges";
import FindingCard from "./FindingCard";

const ORDER: SeverityName[] = ["INFO", "ADVISORY", "NEEDS_INFO", "NEEDS_REVIEW", "REJECT"];

function worst(r: CheckResult): SeverityName {
  return r.findings.reduce<SeverityName>(
    (a, f) => (ORDER.indexOf(f.severity_name) > ORDER.indexOf(a) ? f.severity_name : a),
    "INFO",
  );
}

/**
 * The live check view.
 *
 * Every check is shown, always — including after a REJECT has already been
 * decided. That is deliberate and mirrors the pipeline: a reviewer looking at
 * a rejected vendor still wants to know whether the documents matched and
 * whether the bank account was shared, because those facts matter for the
 * compliance file even though they did not change the outcome.
 */
export default function CheckTimeline({
  plan, results, running, autoExpand = true,
}: {
  plan: CheckPlan[]; results: CheckResult[]; running: boolean; autoExpand?: boolean;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const byName = new Map(results.map((r) => [r.check, r]));
  const items = plan.length ? plan : results.map((r) => ({ check: r.check, label: r.label }));

  return (
    <ol className="relative">
      {items.map((p, i) => {
        const r = byName.get(p.check);
        const isActive = running && !r && results.length === i;
        const sev = r ? worst(r) : undefined;
        const hasFindings = !!r?.findings.length;
        const expanded = open[p.check] ?? (autoExpand && hasFindings);

        return (
          <li key={p.check} className="relative flex gap-3">
            {i < items.length - 1 && (
              <span className={`absolute left-[9px] top-6 h-[calc(100%-12px)] w-px ${
                r ? "bg-slate-200" : "bg-slate-100"}`} />
            )}

            <div className="relative z-10 mt-1 shrink-0">
              <CheckIcon severity={sev} active={isActive} pending={!r && !isActive} />
            </div>

            <div className={`min-w-0 flex-1 pb-4 ${!r && !isActive ? "opacity-40" : ""} ${
              r ? "animate-slidein" : ""}`}>
              <button
                onClick={() => hasFindings && setOpen((o) => ({ ...o, [p.check]: !expanded }))}
                disabled={!hasFindings}
                className="flex w-full items-start justify-between gap-3 text-left"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-slate-800">{p.label}</span>
                    {r && hasFindings && (
                      <span className="rounded-full bg-slate-800 px-1.5 py-px text-[10px] font-bold text-white">
                        {r.findings.length}
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs leading-relaxed text-slate-600">
                    {r ? r.summary : isActive ? "running…" : "queued"}
                  </p>
                </div>
                {r && (
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="font-mono text-[10px] tabular-nums text-slate-400">
                      {r.duration_ms}ms
                    </span>
                    {hasFindings && (
                      <span className={`text-slate-400 transition-transform ${expanded ? "rotate-90" : ""}`}>›</span>
                    )}
                  </div>
                )}
              </button>

              {r && hasFindings && expanded && (
                <div className="mt-2 space-y-2">
                  {r.findings.map((f, k) => <FindingCard key={`${f.code}-${k}`} f={f} />)}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
