import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  CHECK_KIND_META, CaseDetail as CaseDetailT, CheckKind, CheckResult, Finding,
  api, sevMeta, sevName, statusMeta,
} from "../api";
import { OpsChat } from "../components/OpsChat";

/**
 * The ops verification report.
 *
 * Organised around one question: can a reviewer justify this decision to
 * someone else? So everything is evidence-first — the verdict states its own
 * reason, deterministic findings are separated from model judgements because
 * they warrant different trust, and every finding can be expanded to the raw
 * evidence that produced it. Nothing asks the reviewer to take a score on
 * faith.
 */
export default function CaseDetail() {
  const { caseId } = useParams();
  const [c, setC] = useState<CaseDetailT | null>(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"report" | "checks" | "documents" | "chat">("report");

  useEffect(() => {
    if (!caseId) return;
    api.getCase(caseId).then(setC).catch((e) => setErr(String(e)));
  }, [caseId]);

  if (err) return <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{err}</div>;
  if (!c) return <div className="p-6 text-sm text-slate-500">Loading…</div>;

  const meta = statusMeta(c.status);
  const conf: any = c.confidence || {};
  const findings = c.findings || [];
  const blocking = findings.filter((f) =>
    ["NEEDS_INFO", "NEEDS_REVIEW", "REJECT"].includes(sevName(f)));
  const conditions = findings.filter((f) => sevName(f) === "CONDITION");
  const risks = findings.filter((f) => ["NEEDS_REVIEW", "REJECT"].includes(sevName(f)));

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">{c.legal_name}</h1>
          <p className="mt-1 text-sm text-slate-500">
            <span className="font-mono">{c.case_id}</span> · {c.country}
            {c.submission?.category && <> · {c.submission.category}</>}
          </p>
        </div>
        <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-semibold ring-1 ring-inset ${meta.cls}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />{meta.label}
        </span>
      </header>

      <section className="grid gap-3 sm:grid-cols-4">
        <Tile label="Verdict" value={conf.recommendation || meta.label} sub={meta.who} />
        <Tile label="Confidence"
              value={typeof conf.score === "number" ? `${Math.round(conf.score * 100)}%` : "—"}
              sub="weighted, explainable" />
        <Tile label="Blocking findings" value={blocking.length}
              sub={`${findings.length} total`} />
        <Tile label="Risk findings" value={risks.length} sub="needs a human" />
      </section>

      <nav className="flex flex-wrap gap-1 border-b border-slate-200">
        {([["report", "Report"], ["checks", "All checks"],
           ["documents", "Documents"], ["chat", "Ask the copilot"]] as const).map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition ${
              tab === k ? "border-slate-900 text-slate-900"
                        : "border-transparent text-slate-500 hover:text-slate-800"}`}>
            {l}
          </button>
        ))}
      </nav>

      {tab === "report" && (
        <div className="space-y-4">
          <Card title="Why this verdict">
            <p className="text-sm text-slate-800">{conf.decision_reason || meta.blurb}</p>
            {c.reviewer_summary && (
              <p className="mt-3 whitespace-pre-wrap text-sm text-slate-700">{c.reviewer_summary}</p>
            )}
          </Card>

          {!!conditions.length && (
            <Card title={`Conditions to resolve (${conditions.length})`} tone="teal">
              <ul className="space-y-2">
                {conditions.map((f, i) => (
                  <li key={i} className="text-sm text-slate-800">• {f.message}</li>
                ))}
              </ul>
            </Card>
          )}

          {!!risks.length && (
            <Card title={`Risk / red flags (${risks.length})`} tone="rose">
              {risks.map((f, i) => <FindingCard key={i} f={f} />)}
            </Card>
          )}

          <SplitChecks checks={c.checks || []} findings={findings} />

          {!!c.vendor_email && (
            <Card title="Drafted reply to the vendor">
              <pre className="whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs text-slate-700">
                {c.vendor_email}
              </pre>
            </Card>
          )}
        </div>
      )}

      {tab === "checks" && (
        <div className="space-y-2">
          {(c.checks || []).map((ck) => (
            <CheckRow key={ck.check} ck={ck}
                      findings={findings.filter((f) => f.check === ck.check)} />
          ))}
        </div>
      )}

      {tab === "documents" && <Documents checks={c.checks || []} />}

      {tab === "chat" && caseId && <OpsChat caseId={caseId} />}
    </div>
  );
}

/* ------------------------------------------------------------------ */

function SplitChecks({ checks, findings }: { checks: CheckResult[]; findings: Finding[] }) {
  const groups: [CheckKind, CheckResult[]][] = [
    ["deterministic", checks.filter((c) => (c.kind ?? "deterministic") === "deterministic")],
    ["ai", checks.filter((c) => c.kind === "ai")],
  ];
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {groups.map(([kind, list]) => {
        const m = CHECK_KIND_META[kind];
        return (
          <div key={kind} className="rounded-xl border border-slate-200 bg-white">
            <div className="border-b border-slate-200 px-4 py-3">
              <div className="flex items-center gap-2">
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ring-1 ring-inset ${m.cls}`}>
                  {m.label}
                </span>
                <span className="text-sm font-semibold text-slate-800">
                  {kind === "ai" ? "AI checks" : "Deterministic checks"}
                </span>
                <span className="text-xs text-slate-400">{list.length}</span>
              </div>
              <p className="mt-1 text-[11px] text-slate-500">{m.blurb}</p>
            </div>
            <ul className="divide-y divide-slate-100">
              {list.map((ck) => {
                const fs = findings.filter((f) => f.check === ck.check);
                const worst = fs.length ? sevMeta(fs[0]) : null;
                return (
                  <li key={ck.check} className="px-4 py-2.5">
                    <div className="flex items-start justify-between gap-2">
                      <span className="text-sm text-slate-800">{ck.label}</span>
                      {worst
                        ? <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset ${worst.cls}`}>{worst.label}</span>
                        : <span className="shrink-0 text-[11px] text-emerald-600">pass</span>}
                    </div>
                    <p className="mt-0.5 text-[11px] text-slate-500">{ck.summary}</p>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

function CheckRow({ ck, findings }: { ck: CheckResult; findings: Finding[] }) {
  const [open, setOpen] = useState(false);
  const m = CHECK_KIND_META[(ck.kind ?? "deterministic") as CheckKind];
  return (
    <div className="rounded-xl border border-slate-200 bg-white">
      <button onClick={() => setOpen(!open)}
              className="flex w-full items-start justify-between gap-3 px-4 py-3 text-left">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ring-1 ring-inset ${m.cls}`}>{m.label}</span>
            <span className="text-sm font-medium text-slate-800">{ck.label}</span>
            {!!findings.length && (
              <span className="rounded-full bg-slate-800 px-1.5 text-[10px] font-bold text-white">
                {findings.length}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-slate-600">{ck.summary}</p>
        </div>
        <span className="shrink-0 font-mono text-[10px] text-slate-400">{ck.duration_ms}ms</span>
      </button>
      {open && (
        <div className="border-t border-slate-100 px-4 py-3 space-y-2">
          {findings.length
            ? findings.map((f, i) => <FindingCard key={i} f={f} />)
            : <p className="text-xs text-slate-500">No findings.</p>}
          {!!Object.keys(ck.data || {}).length && (
            <details className="mt-2">
              <summary className="cursor-pointer text-[11px] text-slate-500">Raw check data</summary>
              <pre className="mt-1 overflow-auto rounded bg-slate-50 p-2 text-[10px] text-slate-600">
                {JSON.stringify(ck.data, null, 1)}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function FindingCard({ f }: { f: Finding }) {
  const m = sevMeta(f);
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset ${m.cls}`}>{m.label}</span>
        <span className="font-mono text-[10px] text-slate-500">{f.code}</span>
        {f.field && <span className="text-[10px] text-slate-400">{f.field}</span>}
      </div>
      <p className="mt-1 text-sm text-slate-800">{f.message}</p>
      {f.vendor_message && (
        <p className="mt-1 rounded bg-sky-50 p-2 text-[11px] text-sky-800">
          <span className="font-semibold">Sent to vendor: </span>{f.vendor_message}
        </p>
      )}
      {!!Object.keys(f.evidence || {}).length && (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-[11px] text-slate-500">Evidence</summary>
          <dl className="mt-1 grid gap-1 sm:grid-cols-2">
            {Object.entries(f.evidence).map(([k, v]) => (
              <div key={k} className="text-[11px]">
                <dt className="inline font-mono text-slate-500">{k}: </dt>
                <dd className="inline text-slate-700">{String(v)}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </div>
  );
}

function Documents({ checks }: { checks: CheckResult[] }) {
  const dv = checks.find((c) => c.check === "documents");
  const verdicts: any[] = (dv?.data as any)?.verdicts || [];
  const reqs = (checks.find((c) => c.check === "completeness")?.data as any)?.requirements;
  return (
    <div className="space-y-4">
      {reqs && (
        <div className="rounded-xl border border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-800">
            What this vendor was required to supply
          </div>
          <ul className="divide-y divide-slate-100">
            {reqs.documents.map((d: any) => (
              <li key={d.key} className="flex items-start justify-between gap-3 px-4 py-2.5">
                <div>
                  <span className="text-sm text-slate-800">{d.label}</span>
                  {d.declared === "conditional" && d.when_explained && (
                    <p className="text-[11px] text-slate-500">
                      conditional — {d.when_explained}
                    </p>
                  )}
                  {d.effective === "na" && d.why && (
                    <p className="text-[11px] text-slate-400">{d.why}</p>
                  )}
                </div>
                <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset ${
                  d.effective === "required" ? "bg-rose-50 text-rose-700 ring-rose-200"
                  : d.effective === "optional" ? "bg-slate-100 text-slate-600 ring-slate-200"
                  : "bg-slate-50 text-slate-400 ring-slate-200"}`}>
                  {d.effective}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!!verdicts.length && (
        <div className="rounded-xl border border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-800">
            What we read from the files
          </div>
          <ul className="divide-y divide-slate-100">
            {verdicts.map((v, i) => (
              <li key={i} className="px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-medium text-slate-800">
                    {v.doc_type} <span className="text-slate-400">· {v.filename}</span>
                  </span>
                  <span className="text-[11px] text-slate-600">{v.status}</span>
                </div>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  read via {v.read_source} ({Math.round((v.read_confidence ?? 0) * 100)}%)
                  {v.detected_type && <> · classified as {v.detected_type}</>}
                  {v.name_on_document && <> · name on document “{v.name_on_document}”</>}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Card({ title, children, tone }: {
  title: string; children: React.ReactNode; tone?: "rose" | "teal";
}) {
  const border = tone === "rose" ? "border-rose-200" : tone === "teal" ? "border-teal-200" : "border-slate-200";
  return (
    <section className={`rounded-xl border bg-white ${border}`}>
      <div className="border-b border-slate-100 px-4 py-3 text-sm font-semibold text-slate-800">{title}</div>
      <div className="space-y-2 px-4 py-3">{children}</div>
    </section>
  );
}

function Tile({ label, value, sub }: { label: string; value: any; sub?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-xl font-bold text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}
