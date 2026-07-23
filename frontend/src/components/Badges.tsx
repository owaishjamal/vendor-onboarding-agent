import { SEVERITY_META, SeverityName, statusMeta } from "../api";

export function StatusBadge({ s, size = "md" }: { s: string | null; size?: "sm" | "md" | "lg" }) {
  if (!s || s === "RUNNING") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-500 ring-1 ring-inset ring-slate-200">
        <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
        Running
      </span>
    );
  }
  const m = statusMeta(s);
  const pad = size === "lg" ? "px-3.5 py-1.5 text-sm"
    : size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full font-semibold ring-1 ring-inset ${m.cls} ${pad}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${m.dot}`} />
      {m.label}
    </span>
  );
}

export function SeverityChip({ s }: { s: SeverityName }) {
  const m = SEVERITY_META[s];
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ring-1 ring-inset ${m.cls}`}>
      {m.label}
    </span>
  );
}

export function CheckIcon({ severity, active, pending }: {
  severity?: SeverityName; active?: boolean; pending?: boolean;
}) {
  if (active) {
    return (
      <span className="relative flex h-5 w-5 items-center justify-center">
        <span className="absolute h-5 w-5 animate-ping rounded-full bg-indigo-400 opacity-60" />
        <span className="relative h-2.5 w-2.5 rounded-full bg-indigo-600" />
      </span>
    );
  }
  if (pending) return <span className="h-5 w-5 rounded-full border-2 border-dashed border-slate-300" />;

  const base = "flex h-5 w-5 items-center justify-center rounded-full text-white text-[11px] font-bold";
  if (severity === "REJECT") return <span className={`${base} bg-rose-500`}>✕</span>;
  if (severity === "NEEDS_REVIEW") return <span className={`${base} bg-amber-500`}>!</span>;
  if (severity === "NEEDS_INFO") return <span className={`${base} bg-sky-500`}>?</span>;
  if (severity === "ADVISORY") return <span className={`${base} bg-slate-400`}>i</span>;
  return <span className={`${base} bg-emerald-500`}>✓</span>;
}

export function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}
