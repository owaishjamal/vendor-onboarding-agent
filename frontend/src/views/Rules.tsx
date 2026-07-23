import { useEffect, useState } from "react";
import { api, flag } from "../api";

/**
 * The rule packs, rendered.
 *
 * Worth having in the UI because the most common question in a demo is "where
 * did that rule come from?". Being able to switch here and show the exact
 * regex, the exact threshold, and the exact required-document list closes the
 * loop — and makes the point that these live in YAML a procurement lead can
 * edit, not in code only an engineer can change.
 */
export default function Rules({ nonce }: { nonce: number }) {
  const [countries, setCountries] = useState<any[]>([]);
  const [common, setCommon] = useState<any>(null);
  const [master, setMaster] = useState<any[]>([]);
  const [denied, setDenied] = useState<any[]>([]);
  const [open, setOpen] = useState<string>("");

  useEffect(() => {
    api.countries().then((c) => { setCountries(c); setOpen(c[0]?.code ?? ""); }).catch(() => {});
    api.policy().then((p) => setCommon(p.common)).catch(() => {});
    api.vendorMaster().then(setMaster).catch(() => {});
    api.deniedParties().then(setDenied).catch(() => {});
  }, [nonce]);

  return (
    <div className="space-y-5">
      {common && (
        <section className="rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-semibold text-slate-800">Global thresholds</h2>
          <p className="mb-4 text-xs text-slate-500">
            From <code className="font-mono text-[11px]">backend/app/rules/common.yaml</code>.
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <P k="Name match — accept" v={`${common.name_matching?.strong}%`}
               note="treat as the same entity" />
            <P k="Name match — floor" v={`${common.name_matching?.weak}%`}
               note="below this, a different entity" />
            <P k="Denied party — confirm" v={`${common.denied_party_screening?.match_threshold}%`}
               note="rejects outright" />
            <P k="Denied party — possible" v={`${common.denied_party_screening?.near_match_threshold}%`}
               note="escalates to a human" />
            <P k="Document max age" v={`${common.document_rules?.max_age_months} months`}
               note={`applies only to: ${(common.document_rules?.freshness_required ?? []).join(", ")}`} />
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-slate-400">
            The two-band name matching is the reason "Kessler Industrietechnik GmbH" vs
            "Kessler Industrietechnik" passes while "K. Weber" does not. A single threshold
            would have to choose between false alarms and missed redirections.
          </p>
        </section>
      )}

      <section className="rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-800">Country rule packs</h2>
          <p className="text-xs text-slate-500">
            One YAML file per country. Adding a country is adding a file — no code change.
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5 border-b border-slate-200 px-5 py-3">
          {countries.map((c) => (
            <button key={c.code} onClick={() => setOpen(c.code)}
              className={`rounded-full px-2.5 py-1 text-xs font-medium transition ${
                open === c.code ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>
              {c.code} · {c.name}
            </button>
          ))}
        </div>
        {countries.filter((c) => c.code === open).map((c) => (
          <div key={c.code} className="grid gap-5 p-5 lg:grid-cols-2">
            <div className="space-y-3">
              <Field label={c.tax_id?.label ?? "Tax ID"} spec={c.tax_id} />
              <Field label={c.registration_number?.label ?? "Registration number"}
                     spec={c.registration_number} />
              <div className="rounded-lg bg-slate-50 p-3 ring-1 ring-inset ring-slate-200">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                  Payment scheme
                </div>
                <div className="mt-0.5 font-mono text-sm text-slate-900">{c.bank_scheme}</div>
                <div className="mt-1 text-[10.5px] leading-snug text-slate-500">
                  {c.bank_scheme === "iban" && "IBAN validated with ISO 13616 mod-97 check digits."}
                  {c.bank_scheme === "aba" && "Routing number validated with the weighted 3-7-1 checksum."}
                  {c.bank_scheme === "ifsc" && "IFSC code plus account number."}
                  {c.bank_scheme === "swift_account" && "SWIFT/BIC plus account number."}
                </div>
              </div>
            </div>
            <div>
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                Required documents
              </div>
              <ul className="space-y-2">
                {(c.required_documents ?? []).map((d: any) => (
                  <li key={d.doc_type} className="rounded-lg bg-slate-50 p-2.5 ring-1 ring-inset ring-slate-200">
                    <div className="text-xs font-medium text-slate-800">{d.label}</div>
                    <div className="mt-0.5 font-mono text-[10px] text-slate-500">
                      {d.doc_type} · accepts: {(d.accepted ?? []).join(", ")}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ))}
      </section>

      <div className="grid gap-5 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-5 py-3">
            <h2 className="text-sm font-semibold text-slate-800">Existing vendor master</h2>
            <p className="text-xs text-slate-500">What duplicate and shared-banking checks compare against.</p>
          </div>
          <ul className="divide-y divide-slate-100">
            {master.map((v) => (
              <li key={v.vendor_id} className="px-5 py-2.5">
                <div className="text-xs font-medium text-slate-800">{v.legal_name}</div>
                <div className="mt-0.5 flex flex-wrap gap-x-3 font-mono text-[10px] text-slate-400">
                  <span>{v.vendor_id}</span><span>{flag(v.country)}</span>
                  <span>{v.tax_id}</span>
                </div>
                <div className="mt-0.5 font-mono text-[9.5px] text-slate-400">
                  bank fp {v.bank_account_fingerprint?.slice(0, 16)}…
                </div>
              </li>
            ))}
          </ul>
          <p className="border-t border-slate-100 px-5 py-2 text-[10.5px] leading-relaxed text-slate-400">
            Bank details are stored as a salted fingerprint, not raw account numbers, so
            collisions can be detected and logged without spreading account data through
            the audit trail.
          </p>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-5 py-3">
            <h2 className="text-sm font-semibold text-slate-800">Denied-party list</h2>
            <p className="text-xs text-slate-500">Entity and individual names, with aliases.</p>
          </div>
          <ul className="divide-y divide-slate-100">
            {denied.map((d) => (
              <li key={d.name} className="px-5 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-slate-800">{d.name}</span>
                  <span className="rounded bg-rose-100 px-1.5 py-0.5 text-[9.5px] font-semibold text-rose-700">
                    {d.list_name}
                  </span>
                </div>
                <div className="mt-0.5 text-[10px] text-slate-400">
                  {d.kind.toLowerCase()} · {d.country}
                  {!!d.aliases?.length && ` · aka ${d.aliases.join(", ")}`}
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

const P = ({ k, v, note }: { k: string; v: any; note: string }) => (
  <div className="rounded-lg bg-slate-50 p-3 ring-1 ring-inset ring-slate-200">
    <div className="text-[10px] font-medium uppercase tracking-wide text-slate-500">{k}</div>
    <div className="mt-0.5 font-mono text-sm font-semibold text-slate-900">{v}</div>
    <div className="mt-0.5 text-[10px] leading-snug text-slate-400">{note}</div>
  </div>
);

const Field = ({ label, spec }: { label: string; spec: any }) => {
  if (!spec?.regex) return null;
  return (
    <div className="rounded-lg bg-slate-50 p-3 ring-1 ring-inset ring-slate-200">
      <div className="flex items-center justify-between">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{label}</div>
        {spec.required === false && (
          <span className="text-[9.5px] font-medium text-slate-400">optional</span>
        )}
      </div>
      <code className="mt-1 block break-all font-mono text-[11px] text-slate-900">{spec.regex}</code>
      {spec.example && (
        <div className="mt-1 font-mono text-[10px] text-slate-500">e.g. {spec.example}</div>
      )}
    </div>
  );
};
