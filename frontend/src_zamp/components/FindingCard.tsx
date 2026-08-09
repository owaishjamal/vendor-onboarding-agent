import { Finding, sevMeta, sevName } from "../api";
import { SeverityChip } from "./Badges";

/**
 * One finding, rendered so the reviewer can see three things at a glance:
 * how serious it is, what the evidence was, and — critically — whether this
 * is something the vendor will be told about.
 *
 * That last part is why `vendor_message` is shown separately and labelled.
 * A reviewer needs to know at a glance which findings are going out in an
 * email and which are staying internal, because the distinction is the
 * difference between a routine request and tipping off a fraudster.
 */
export default function FindingCard({ f }: { f: Finding }) {
  const m = sevMeta(f);
  const name = sevName(f);
  const ev = Object.entries(f.evidence || {}).filter(([, v]) => v !== null && v !== undefined);

  return (
    <div className="animate-slidein overflow-hidden rounded-lg border border-slate-200 bg-white">
      <div className={`h-0.5 w-full ${m.bar}`} />
      <div className="p-3">
        <div className="mb-1.5 flex flex-wrap items-center gap-2">
          <SeverityChip s={name} />
          <span className="font-mono text-[10px] font-medium text-slate-500">{f.code}</span>
          {f.field && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
              {f.field}
            </span>
          )}
        </div>

        <p className="text-xs leading-relaxed text-slate-700">{f.message}</p>

        {f.vendor_message && (
          <div className="mt-2 rounded border-l-2 border-sky-400 bg-sky-50/60 px-2.5 py-1.5">
            <div className="text-[9.5px] font-bold uppercase tracking-wide text-sky-700">
              Text sent to vendor
            </div>
            <p className="mt-0.5 text-[11px] leading-relaxed text-sky-900">{f.vendor_message}</p>
          </div>
        )}

        {name === "NEEDS_REVIEW" && !f.vendor_message && (
          <div className="mt-2 text-[10px] font-medium text-amber-700">
            Internal only — not disclosed to the vendor.
          </div>
        )}

        {!!ev.length && (
          <details className="mt-2 group">
            <summary className="cursor-pointer text-[10px] font-medium text-slate-400 hover:text-slate-600">
              Evidence ({ev.length})
            </summary>
            <dl className="mt-1.5 rounded bg-slate-50 p-2 ring-1 ring-inset ring-slate-200">
              {ev.map(([k, v]) => (
                <div key={k} className="flex items-baseline justify-between gap-3 border-b border-slate-100 py-1 last:border-0">
                  <dt className="font-mono text-[10px] text-slate-500">{k}</dt>
                  <dd className="max-w-[65%] break-words text-right font-mono text-[10px] text-slate-800">
                    {typeof v === "object" ? JSON.stringify(v) : String(v)}
                  </dd>
                </div>
              ))}
            </dl>
          </details>
        )}
      </div>
    </div>
  );
}
