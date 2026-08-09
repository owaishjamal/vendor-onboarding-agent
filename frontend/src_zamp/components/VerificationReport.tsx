import { Case, sevName } from "../api";

/**
 * The verification report a reviewer reads INSTEAD of opening every document.
 *
 * Everything needed for a decision, in one place and in reading order:
 * recommendation and confidence, why the AI decided that, the field-by-field
 * comparison of form vs document with mismatches highlighted, which documents
 * were accepted, and what's missing.
 */
export default function VerificationReport({ c }: { c: Case }) {
  const conf = (c as any).confidence ?? {};
  const score: number | null = typeof conf.score === "number" ? conf.score : null;
  const fv = (c.checks ?? []).find((x) => x.check === "field_verification");
  const matrix: any[] = fv?.data?.matrix ?? [];
  const docsCheck = (c.checks ?? []).find((x) => x.check === "documents");
  const docs: any[] = docsCheck?.data?.documents ?? [];
  const findings = (c.findings ?? []).filter((f) => sevName(f) !== "INFO");
  const missing = findings.filter((f) =>
    f.code === "MISSING_REQUIRED_DOCUMENT" || f.code === "MISSING_REQUIRED_FIELD");
  const mismatches = findings.filter((f) =>
    f.code.includes("MISMATCH") || f.code === "FIELD_CONTRADICTED");

  const band = score === null ? "slate"
    : score >= 0.85 ? "emerald" : score >= 0.7 ? "amber" : "rose";
  const bandCls: Record<string, string> = {
    emerald: "text-emerald-700 bg-emerald-50 ring-emerald-200",
    amber: "text-amber-800 bg-amber-50 ring-amber-200",
    rose: "text-rose-700 bg-rose-50 ring-rose-200",
    slate: "text-slate-600 bg-slate-50 ring-slate-200",
  };

  return (
    <div className="space-y-4">
      {/* --- headline: recommendation + confidence ------------------- */}
      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              AI recommendation
            </div>
            <div className="mt-1 text-2xl font-semibold text-slate-900">
              {conf.recommendation ?? "—"}
            </div>
          </div>
          {score !== null && (
            <div className={`rounded-xl px-4 py-2 text-center ring-1 ring-inset ${bandCls[band]}`}>
              <div className="text-[10px] font-semibold uppercase tracking-wide opacity-80">
                Confidence
              </div>
              <div className="text-2xl font-bold tabular-nums">{(score * 100).toFixed(0)}%</div>
            </div>
          )}
        </div>

        {conf.decision_reason && (
          <p className="mt-3 rounded-lg bg-slate-50 p-3 text-sm leading-relaxed text-slate-700">
            {conf.decision_reason}
          </p>
        )}

        {/* how the score was built — an explainable number, not a vibe */}
        {conf.components && (
          <div className="mt-3 grid gap-2 sm:grid-cols-4">
            {Object.entries(conf.components).map(([k, v]: any) => (
              <div key={k} className="rounded-lg bg-slate-50 px-2.5 py-2">
                <div className="text-[10px] uppercase tracking-wide text-slate-500">
                  {k.replace(/_/g, " ")}
                </div>
                <div className="mt-0.5 flex items-center gap-1.5">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200">
                    <div className="h-full bg-slate-600" style={{ width: `${v * 100}%` }} />
                  </div>
                  <span className="font-mono text-[10px] tabular-nums text-slate-600">
                    {(v * 100).toFixed(0)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}

        {!!conf.reasons?.length && (
          <ul className="mt-3 space-y-1">
            {conf.reasons.map((r: string, i: number) => (
              <li key={i} className="text-[11.5px] text-slate-500">• {r}</li>
            ))}
          </ul>
        )}
      </div>

      {/* --- field-by-field: form vs document ------------------------ */}
      {matrix.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-800">
            Form vs document — field by field
          </h3>
          <p className="mb-3 text-[11px] text-slate-400">
            Documents are validated first; only accepted documents can corroborate a value.
          </p>
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-1.5 pr-3 font-medium">Field</th>
                <th className="py-1.5 pr-3 font-medium">Submitted on form</th>
                <th className="py-1.5 pr-3 font-medium">Found in document</th>
                <th className="py-1.5 font-medium">Result</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {matrix.map((r) => {
                const bad = r.outcome === "CONTRADICTED";
                return (
                  <tr key={r.field} className={bad ? "bg-rose-50/50" : ""}>
                    <td className="py-1.5 pr-3 font-mono text-[11px] text-slate-600">{r.field}</td>
                    <td className="py-1.5 pr-3 font-medium text-slate-800">{r.claim}</td>
                    <td className="py-1.5 pr-3 text-slate-600">
                      {r.evidence_value
                        ? <>{r.evidence_value}{" "}
                            <span className="text-[10px] text-slate-400">({r.source})</span></>
                        : <span className="text-slate-300">not found</span>}
                    </td>
                    <td className="py-1.5">
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                        r.outcome === "CORROBORATED" ? "bg-emerald-100 text-emerald-700"
                          : bad ? "bg-rose-100 text-rose-700"
                          : "bg-amber-100 text-amber-800"}`}>
                        {r.outcome === "CORROBORATED" ? "MATCH"
                          : bad ? "MISMATCH" : "NO EVIDENCE"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* --- documents ---------------------------------------------- */}
      {docs.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-800">Documents</h3>
          <ul className="space-y-1.5">
            {docs.map((d, i) => (
              <li key={i} className="flex items-start justify-between gap-3 text-xs">
                <div>
                  <span className="font-medium text-slate-800">
                    {d.doc_type?.replace(/_/g, " ")}
                  </span>
                  <span className="text-slate-400"> — {d.filename}</span>
                  <div className="text-[10.5px] text-slate-500">
                    read by {d.read_source} at {Math.round((d.read_confidence ?? 0) * 100)}%
                    {d.detected_type && ` · identified as ${d.detected_type.replace(/_/g, " ")}`}
                    {d.irrelevant_as && ` · looks like a ${d.irrelevant_as}`}
                  </div>
                </div>
                <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${
                  d.status === "VERIFIED" ? "bg-emerald-100 text-emerald-700"
                    : d.status === "NEEDS_REVIEW" ? "bg-amber-100 text-amber-800"
                    : "bg-rose-100 text-rose-700"}`}>
                  {d.status === "VERIFIED" ? "ACCEPTED" : d.status?.replace(/_/g, " ")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* --- mismatches & missing ------------------------------------ */}
      <div className="grid gap-4 sm:grid-cols-2">
        <Panel title="Mismatches detected" items={mismatches} tone="rose"
               empty="No mismatches between the form and the documents." />
        <Panel title="Missing information" items={missing} tone="amber"
               empty="Nothing missing — all required fields and documents supplied." />
      </div>
    </div>
  );
}

function Panel({ title, items, tone, empty }: {
  title: string; items: any[]; tone: string; empty: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-800">
        {title}{" "}
        {items.length > 0 && (
          <span className={`ml-1 rounded-full px-1.5 py-0.5 text-[10px] font-bold ${
            tone === "rose" ? "bg-rose-100 text-rose-700" : "bg-amber-100 text-amber-800"}`}>
            {items.length}
          </span>
        )}
      </h3>
      {items.length === 0 ? (
        <p className="mt-1.5 text-[11.5px] text-slate-400">{empty}</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {items.map((f, i) => (
            <li key={i} className="text-[11.5px] leading-relaxed text-slate-700">
              • {f.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
