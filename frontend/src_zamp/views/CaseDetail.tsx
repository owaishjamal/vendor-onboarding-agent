import { useEffect, useState } from "react";
import { Case, statusMeta, api, shortTime, flag } from "../api";
import CheckTimeline from "../components/CheckTimeline";
import FindingCard from "../components/FindingCard";
import ReviewerActions from "../components/ReviewerActions";
import VerificationReport from "../components/VerificationReport";
import { StatusBadge } from "../components/Badges";

export default function CaseDetail({ caseId, onBack }: { caseId: string; onBack: () => void }) {
  const [c, setCase] = useState<Case | null>(null);
  const [tab, setTab] = useState<"report" | "findings" | "checks" | "submission">("report");

  const reload = () => api.case(caseId).then(setCase).catch(() => setCase(null));
  useEffect(() => { reload(); }, [caseId]);
  if (!c) return <div className="py-16 text-center text-sm text-slate-500">Loading…</div>;

  const meta = statusMeta(c.status);
  const cs = c.change_summary;
  const fv = (c.checks ?? []).find((x) => x.check === "field_verification");
  const matrix: any[] = fv?.data?.matrix ?? [];
  const vendorLink = (c as any).vendor_token
    ? `${window.location.origin}/#/vendor/${(c as any).vendor_token}` : null;

  const findings = (c.findings ?? []).filter((f) => f.severity_name !== "INFO");
  const blocking = findings.filter((f) => ["NEEDS_REVIEW", "REJECT"].includes(f.severity_name));
  const askVendor = findings.filter((f) => f.severity_name === "NEEDS_INFO");
  const advisory = findings.filter((f) => f.severity_name === "ADVISORY");

  return (
    <div className="space-y-4">
      <button onClick={onBack} className="text-xs font-medium text-slate-500 hover:text-slate-800">
        ← Back to queue
      </button>

      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge s={c.status} size="lg" />
              <span className="text-xs text-slate-500">{meta.who}</span>
              {(c.revision ?? 1) > 1 && (
                <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700">
                  revision {c.revision}
                </span>
              )}
              {c.superseded_by && (
                <span className="rounded bg-slate-200 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">
                  superseded
                </span>
              )}
            </div>
            <h1 className="mt-2 text-lg font-semibold text-slate-900">{c.legal_name}</h1>
            {c.trading_name && c.trading_name !== c.legal_name && (
              <div className="text-xs text-slate-500">trading as {c.trading_name}</div>
            )}
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-700">{c.reviewer_summary}</p>
          </div>
          <dl className="shrink-0 space-y-1 text-right text-xs">
            <Meta k="Country" v={flag(c.country)} />
            <Meta k="Contact" v={c.contact_email ?? "—"} />
            <Meta k="Submitted" v={shortTime(c.created_at)} />
            <Meta k="Case" v={c.case_id} />
          </dl>
        </div>

        {c.vendor_email ? (
          <div className="mt-4 overflow-hidden rounded-lg ring-1 ring-inset ring-sky-200">
            <div className="flex items-center justify-between bg-sky-50 px-3 py-1.5">
              <span className="text-[10px] font-bold uppercase tracking-wide text-sky-800">
                Drafted reply — ready to send
              </span>
              <span className="text-[10px] text-sky-600">{c.contact_email}</span>
            </div>
            <pre className="whitespace-pre-wrap bg-white px-3 py-2.5 font-sans text-[12px] leading-relaxed text-slate-700">
              {c.vendor_email}
            </pre>
          </div>
        ) : c.status !== "APPROVED" && (
          <div className="mt-4 rounded-lg bg-slate-900 px-3 py-2 text-[11px] leading-relaxed text-slate-200">
            <span className="font-semibold">No vendor email generated.</span>{" "}
            {c.status === "REJECTED"
              ? "Rejected on screening — contacting the vendor would disclose which control caught them."
              : "Under internal review — contacting the vendor now could tip off a fraudster and taint the review."}
          </div>
        )}
      </div>

      {vendorLink && (
        <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Vendor portal link
          </span>
          <code className="flex-1 truncate rounded bg-slate-50 px-2 py-1 font-mono text-[11px] text-slate-600">
            {vendorLink}
          </code>
          <button
            onClick={() => navigator.clipboard?.writeText(vendorLink)}
            className="rounded-lg bg-slate-900 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-slate-800"
          >
            Copy
          </button>
          <span className="text-[10px] text-slate-400">
            shows only status + requested items
          </span>
        </div>
      )}

      {!!(c as any).outcomes?.length && (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-800">Downstream actions</h3>
          <p className="mb-2 text-[11px] text-slate-400">
            What the profile routed on this decision. Side effects are audited too.
          </p>
          <ul className="space-y-1.5">
            {(c as any).outcomes.map((o: any, i: number) => (
              <li key={i} className="flex items-start gap-2 text-xs">
                <span className={o.ok ? "text-emerald-600" : "text-amber-600"}>
                  {o.ok ? "✓" : "!"}
                </span>
                <div>
                  <span className="font-medium text-slate-800">{o.action}</span>
                  <span className="text-slate-400"> on {o.status}</span>
                  {o.detail && <div className="text-slate-500">{o.detail}</div>}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {cs && (
        <div className="rounded-xl border border-indigo-200 bg-indigo-50/60 p-4">
          <h3 className="text-sm font-semibold text-indigo-900">
            Resubmission — what changed since the previous attempt
          </h3>
          <div className="mt-2 grid gap-3 sm:grid-cols-3">
            <DiffCol title="Resolved" items={cs.resolved} tone="emerald" />
            <DiffCol title="Still outstanding" items={cs.remaining} tone="amber" />
            <DiffCol title="New this time" items={cs.new} tone="rose" />
          </div>
          {!cs.remaining.length && !cs.new.length && (
            <p className="mt-2 text-xs font-medium text-emerald-700">
              Everything flagged last time has been resolved.
            </p>
          )}
        </div>
      )}

      {["PENDING_REVIEW", "PENDING_INFO", "REJECTED", "APPROVED"].includes(
        c.status.replace("_BY_REVIEWER", "")) && (
        <ReviewerActions c={c} onActed={reload} />
      )}

      <div className="flex gap-1 rounded-lg bg-slate-100 p-1">
        {([["report", "Verification report"],
           ["findings", `Findings (${findings.length})`],
           ["checks", `Checks (${c.checks?.length ?? 0})`],
           ["submission", "Submitted data"]] as const).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition ${
              tab === k ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === "report" && <VerificationReport c={c} />}

      {tab === "findings" && (
        <div className="space-y-5">
          {!findings.length && (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-5 text-sm text-emerald-800">
              No findings. Every check passed cleanly.
            </div>
          )}
          <Group title="Blocking — needs a decision from us" items={blocking}
                 note="Not disclosed to the vendor." />
          <Group title="To request from the vendor" items={askVendor}
                 note={c.vendor_email
                   ? "Included in the drafted email above."
                   : "Held back — the case is not in a state where the vendor should be contacted."} />
          <Group title="Advisory — recorded, not blocking" items={advisory} />
        </div>
      )}

      {tab === "checks" && (
        <div className="rounded-xl border border-slate-200 bg-white p-5">
          <CheckTimeline
            plan={(c.checks ?? []).map((x) => ({ check: x.check, label: x.label }))}
            results={(c.checks ?? []).map((x) => ({
              ...x,
              findings: (c.findings ?? []).filter((f) => f.check === x.check),
            }))}
            running={false}
            autoExpand={false}
          />
        </div>
      )}

      {tab === "submission" && (
        <pre className="scroll-thin max-h-[600px] overflow-auto rounded-xl bg-slate-900 p-4 font-mono text-[10.5px] leading-relaxed text-slate-200">
          {JSON.stringify(c.submission, null, 2)}
        </pre>
      )}
    </div>
  );
}

const Meta = ({ k, v }: { k: string; v: string }) => (
  <div className="flex justify-end gap-3">
    <dt className="text-slate-400">{k}</dt>
    <dd className="w-44 truncate font-mono text-slate-700">{v}</dd>
  </div>
);

function DiffCol({ title, items, tone }: { title: string; items: string[]; tone: string }) {
  return (
    <div>
      <div className={`text-[10px] font-semibold uppercase tracking-wide text-${tone}-700`}>
        {title} ({items.length})
      </div>
      {items.length ? (
        <ul className="mt-1 space-y-1">
          {items.map((code) => (
            <li key={code} className="font-mono text-[10px] text-slate-600">
              {code.replace(/_/g, " ").toLowerCase()}
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-1 text-[10px] text-slate-400">—</div>
      )}
    </div>
  );
}

function Group({ title, items, note }: { title: string; items: any[]; note?: string }) {
  if (!items.length) return null;
  return (
    <section>
      <div className="mb-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-600">{title}</h3>
        {note && <p className="text-[11px] text-slate-400">{note}</p>}
      </div>
      <div className="space-y-2">
        {items.map((f, i) => <FindingCard key={`${f.code}-${i}`} f={f} />)}
      </div>
    </section>
  );
}
