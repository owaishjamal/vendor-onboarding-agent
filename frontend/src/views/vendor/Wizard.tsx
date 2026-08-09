import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  CheckResult, Finding, ResolvedRequirement, Scenario, VendorCategory,
  api, consumeStream, sevMeta, sevName, statusMeta,
} from "../../api";

/**
 * The whole vendor journey, on one page:
 *
 *   pick a category → answer only the questions that apply → attach only the
 *   documents that apply → watch every check run → see the verdict
 *
 * The form is not hardcoded. Requirements come from the backend and are
 * re-resolved as the vendor types, so a conditional document appears the
 * moment the answer that triggers it is given. Nothing here knows what a
 * construction vendor needs — that lives in a JSON profile.
 */

type Step = "category" | "form" | "running" | "done";

export default function Wizard() {
  const nav = useNavigate();

  // The finished result lives in the URL (?case=<id>), not only in component
  // state. Component state alone meant a refresh, a Back, or any remount threw
  // the vendor back to step one and lost a completed verification. With the id
  // in the query string the result is addressable: refresh re-fetches it, Back
  // and Forward move between onboarding and result predictably, and the page
  // can be linked to.
  const [params, setParams] = useSearchParams();
  const resultCaseId = params.get("case");

  const [step, setStep] = useState<Step>(resultCaseId ? "done" : "category");
  const [categories, setCategories] = useState<VendorCategory[]>([]);
  const [category, setCategory] = useState("");
  const [country, setCountry] = useState(localStorage.getItem("country") || "IN");
  const [countries, setCountries] = useState<{ code: string; name: string }[]>([]);

  const [core, setCore] = useState<Record<string, string>>({
    legal_name: localStorage.getItem("business_name") || "",
    contact_name: "", contact_email: "", address_line1: "",
    registration_number: "", tax_id: "", pan: "", business_description: "",
  });
  const [bank, setBank] = useState<Record<string, string>>({
    account_name: "", account_number: "", ifsc: "", iban: "", routing_number: "",
  });
  const [custom, setCustom] = useState<Record<string, any>>({});
  const [files, setFiles] = useState<Record<string, File>>({});
  const [preflight, setPreflight] = useState<Record<string, any>>({});

  const [reqs, setReqs] = useState<{ fields: any[]; documents: any[] } | null>(null);
  const [resolved, setResolved] = useState<{
    fields: ResolvedRequirement[]; documents: ResolvedRequirement[];
  } | null>(null);

  // Demonstrable scenarios. A prefill fills the form and nothing more — the
  // submit path, the checks and the verdict are identical to typing it by hand.
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [loadedScenario, setLoadedScenario] = useState<Scenario | null>(null);
  // Documents that came from a prefill: field blocks rather than real files,
  // read through the same reader (see SubmittedDocument.extracted).
  const [prefillDocs, setPrefillDocs] =
    useState<{ doc_type: string; filename: string; extracted: any }[]>([]);

  const [plan, setPlan] = useState<{ check: string; label: string; kind: string }[]>([]);
  const [results, setResults] = useState<CheckResult[]>([]);
  const [caseData, setCaseData] = useState<any>(null);
  const [error, setError] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    api.categories().then(setCategories).catch(() => {});
    api.countries().then((cs) => setCountries(cs.map((c: any) => ({ code: c.code, name: c.name }))))
      .catch(() => {});
    api.scenarios().then(setScenarios).catch(() => {});
  }, []);

  /** Fill the form from a scenario and drop the user on it, ready to submit. */
  async function loadScenario(s: Scenario) {
    try {
      const d = await api.scenario(s.id);
      const { bank: _b, ...formFields } = d.form as any;
      setCategory(d.category);
      setCountry(d.form.country || "IN");
      setCore((prev) => ({
        ...prev,
        legal_name: formFields.legal_name ?? "",
        contact_name: formFields.contact_name ?? "",
        contact_email: formFields.contact_email ?? "",
        address_line1: formFields.address_line1 ?? "",
        registration_number: formFields.registration_number ?? "",
        tax_id: formFields.tax_id ?? "",
        pan: formFields.pan ?? "",
        business_description: formFields.business_description ?? "",
      }));
      setBank({
        account_name: d.bank.account_name ?? "", account_number: d.bank.account_number ?? "",
        ifsc: d.bank.ifsc ?? "", iban: d.bank.iban ?? "",
        routing_number: d.bank.routing_number ?? "",
      });
      setCustom(d.custom_fields ?? {});
      // Prefilled documents arrive as field blocks, not files. Clear any real
      // uploads so the two can never be submitted together and double-count.
      setFiles({});
      setPreflight({});
      setPrefillDocs(d.documents ?? []);
      setLoadedScenario(s);
      setError("");
      setStep("form");
    } catch (e: any) {
      setError(`Could not load that scenario: ${e.message ?? e}`);
    }
  }

  /** Any edit to the form means it is no longer purely the scenario. */
  function clearScenario() {
    setLoadedScenario(null);
    setPrefillDocs([]);
  }

  // Load the requirement set whenever category or country changes.
  useEffect(() => {
    if (!category) return;
    api.requirements(country, category)
      .then((r) => { setReqs({ fields: r.fields, documents: r.documents }); setResolved(r.resolved); })
      .catch((e) => setError(String(e)));
  }, [category, country]);

  // Re-resolve conditionals as answers change: the ask should tighten live.
  useEffect(() => {
    if (!category || !reqs) return;
    const t = setTimeout(() => {
      api.previewRequirements({ ...core, bank, category, country, custom_fields: custom })
        .then((r) => setResolved(r.resolved))
        .catch(() => {});
    }, 250);
    return () => clearTimeout(t);
  }, [core, bank, custom, category, country, reqs]);

  useEffect(() => {
    if (step === "running") endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [results.length, step]);

  // Rehydrate a result from ?case=<id> — on first load, on refresh, and when
  // the user navigates Back to a result they had already seen.
  useEffect(() => {
    if (!resultCaseId) return;
    if (caseData?.case_id === resultCaseId) return;
    let cancelled = false;
    api.getCase(resultCaseId)
      .then((c) => {
        if (cancelled) return;                 // a later navigation won.
        setCaseData(c);
        setResults((c.checks || []).map((ck: any) => ({
          ...ck, findings: (c.findings || []).filter((f: any) => f.check === ck.check),
        })));
        setStep("done");
      })
      .catch(() => {
        if (!cancelled) {
          setError("That case could not be loaded.");
          setStep("category");
        }
      });
    return () => { cancelled = true; };
  }, [resultCaseId, caseData?.case_id]);

  const neededDocs = useMemo(
    () => (resolved?.documents ?? []).filter((d) => d.effective !== "na"),
    [resolved]);

  const activeFields = useMemo(() => {
    const applies = new Set((resolved?.fields ?? [])
      .filter((f) => f.effective !== "na").map((f) => f.key));
    return (reqs?.fields ?? []).filter((f) => applies.has(f.key));
  }, [reqs, resolved]);

  const missingRequired = useMemo(() => {
    const out: string[] = [];
    for (const d of neededDocs) {
      // A prefilled field block counts as supplied — it is read by the same
      // reader an uploaded file goes through, so the requirement really is met.
      const supplied = !!files[d.key] || prefillDocs.some((p) => p.doc_type === d.key);
      if (d.effective === "required" && !supplied) out.push(d.label);
    }
    for (const f of activeFields) {
      const req = (resolved?.fields ?? []).find((r) => r.key === f.key);
      if (req?.effective !== "required") continue;
      const v = f.key in core ? core[f.key] : custom[f.key];
      if (v === undefined || String(v ?? "").trim() === "") out.push(f.label);
    }
    if (!core.legal_name.trim()) out.push("Registered legal name");
    if (!core.contact_email.trim()) out.push("Contact email");
    return out;
  }, [neededDocs, activeFields, files, prefillDocs, core, custom, resolved]);

  function attach(key: string, file: File | null) {
    if (!file) {
      setFiles((p) => { const n = { ...p }; delete n[key]; return n; });
      setPreflight((p) => ({ ...p, [key]: undefined }));
      return;
    }
    setFiles((p) => ({ ...p, [key]: file }));
    setPreflight((p) => ({ ...p, [key]: { checking: true } }));
    api.preflightDocument(file, key, country, core.legal_name)
      .then((r) => setPreflight((p) => ({ ...p, [key]: r })))
      .catch(() => setPreflight((p) => ({ ...p, [key]: undefined })));
  }

  async function submit() {
    // A second click while a run is in flight would start a duplicate case and
    // race two streams onto the same view.
    if (step === "running") return;
    setError(""); setResults([]); setCaseData(null); setStep("running");
    // Real uploads win over a prefill's field block for the same slot: if the
    // user attached an actual file, that is the evidence we should read.
    const uploadedTypes = new Set(Object.keys(files));
    const submission = {
      ...core, country, category,
      bank: Object.fromEntries(Object.entries(bank).filter(([, v]) => v)),
      custom_fields: custom,
      documents: [
        ...Object.entries(files).map(([doc_type, f]) => ({
          doc_type, filename: f.name,
        })),
        ...prefillDocs.filter((d) => !uploadedTypes.has(d.doc_type)),
      ],
    };
    try {
      const res = await api.submitForm(submission, Object.values(files));
      await consumeStream(res, (type, data) => {
        if (type === "plan") setPlan(data);
        else if (data?.type === "check") setResults((p) => [...p, data.result]);
        else if (data?.type === "done") {
          setCaseData(data.case);
          setStep("done");
          // Put the result in the URL. `replace` so Back returns to the form
          // the vendor came from rather than to a half-finished run.
          setParams({ case: data.case.case_id }, { replace: true });
        }
        else if (data?.type === "error") { setError(data.message); setStep("form"); }
      });
    } catch (e: any) {
      setError(e.message ?? String(e));
      setStep("form");
    }
  }

  // ---------------------------------------------------------------- render

  if (step === "category") {
    return (
      <div className="max-w-3xl space-y-6">
        <header>
          <h1 className="text-2xl font-bold text-slate-900">What do you supply?</h1>
          <p className="mt-1 text-sm text-slate-600">
            We only ask for what your kind of business actually needs. Picking
            the right category means fewer documents, not more.
          </p>
        </header>
        <div className="grid gap-3 sm:grid-cols-2">
          {categories.map((c) => (
            <button
              key={c.id}
              onClick={() => { clearScenario(); setCategory(c.id); setStep("form"); }}
              className="rounded-xl border border-slate-200 bg-white p-4 text-left transition hover:border-slate-900 hover:shadow-sm"
            >
              <div className="font-semibold text-slate-900">{c.label}</div>
              <div className="mt-1 text-xs text-slate-500">{c.blurb}</div>
              <div className="mt-2 text-[11px] text-slate-400">
                {c.extra_documents} category document(s) on top of the {country} baseline
              </div>
            </button>
          ))}
        </div>

        {!!scenarios.length && <ScenarioPicker scenarios={scenarios} onPick={loadScenario} />}
      </div>
    );
  }

  if (step === "running" || step === "done") {
    return (
      <div className="max-w-4xl space-y-5">
        <header className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">
              {step === "running" ? "Verifying your submission" : "Verification complete"}
            </h1>
            <p className="mt-1 text-sm text-slate-600">
              {core.legal_name} · {country} · {category}
            </p>
          </div>
          {caseData && <StatusPill status={caseData.status} />}
        </header>

        {caseData && <VerdictCard case_={caseData} />}

        <div className="rounded-xl border border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-800">
            Checks {step === "running" && (
              <span className="ml-2 text-xs font-normal text-slate-500">
                {results.length} of {plan.length || 9}…
              </span>)}
          </div>
          <ol className="divide-y divide-slate-100">
            {(plan.length ? plan : results.map((r) => ({ check: r.check, label: r.label, kind: "" })))
              .map((p, i) => {
                const r = results.find((x) => x.check === p.check);
                const active = !r && results.length === i && step === "running";
                return (
                  <li key={p.check} className={`px-4 py-3 ${!r && !active ? "opacity-40" : ""}`}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-slate-800">{p.label}</span>
                          {p.kind && <KindChip kind={p.kind} />}
                        </div>
                        <p className="mt-0.5 text-xs text-slate-600">
                          {r ? r.summary : active ? "running…" : "queued"}
                        </p>
                        {r?.findings?.map((f, k) => <FindingRow key={k} f={f} />)}
                      </div>
                      {r && <span className="shrink-0 font-mono text-[10px] text-slate-400">
                        {r.duration_ms}ms
                      </span>}
                    </div>
                  </li>
                );
              })}
          </ol>
        </div>
        <div ref={endRef} />

        {step === "done" && (
          <div className="flex gap-2">
            <button className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white"
                    onClick={() => {
                      // Leaving the result is an explicit choice — and it
                      // clears the URL so a refresh does not resurrect it.
                      setParams({}, { replace: true });
                      setStep("category"); setFiles({});
                      setResults([]); setCaseData(null);
                    }}>
              Submit another vendor
            </button>
            {caseData?.vendor_token && (
              <button className="rounded-lg border border-slate-300 px-4 py-2 text-sm"
                      onClick={() => nav(`/vendor/${caseData.vendor_token}`)}>
                Open the vendor's view
              </button>
            )}
          </div>
        )}
      </div>
    );
  }

  // ---- form
  const cat = categories.find((c) => c.id === category);
  return (
    <div className="max-w-3xl space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">{cat?.label ?? "Vendor details"}</h1>
          <p className="mt-1 text-sm text-slate-600">
            Only the fields and documents that apply to you are shown.
          </p>
        </div>
        <button className="text-xs text-slate-500 underline" onClick={() => setStep("category")}>
          change category
        </button>
      </header>

      {loadedScenario && (
        <ScenarioBanner s={loadedScenario} onClear={clearScenario} />
      )}

      <section className="rounded-xl border border-slate-200 bg-white p-4 space-y-3">
        <h2 className="text-sm font-semibold text-slate-800">Your business</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Registered legal name" required value={core.legal_name}
                 onChange={(v) => setCore({ ...core, legal_name: v })} />
          <label className="block">
            <span className="text-xs font-medium text-slate-700">Country</span>
            <select value={country} onChange={(e) => setCountry(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              {countries.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
            </select>
          </label>
          <Field label="Contact name" value={core.contact_name}
                 onChange={(v) => setCore({ ...core, contact_name: v })} />
          <Field label="Contact email" required value={core.contact_email}
                 onChange={(v) => setCore({ ...core, contact_email: v })} />
          <Field label="Registered address" value={core.address_line1}
                 onChange={(v) => setCore({ ...core, address_line1: v })} />
          <Field label="Registration number" value={core.registration_number}
                 onChange={(v) => setCore({ ...core, registration_number: v })} />
          <Field label="Tax registration (GSTIN / VAT / EIN)" value={core.tax_id}
                 onChange={(v) => setCore({ ...core, tax_id: v })} />
          {country === "IN" && (
            <Field label="PAN" value={core.pan} onChange={(v) => setCore({ ...core, pan: v })} />
          )}
        </div>
        <label className="block">
          <span className="text-xs font-medium text-slate-700">What do you supply?</span>
          <textarea value={core.business_description} rows={2}
                    onChange={(e) => setCore({ ...core, business_description: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                    placeholder="A sentence is enough — we check it against the category you picked." />
        </label>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-4 space-y-3">
        <h2 className="text-sm font-semibold text-slate-800">Where we should pay you</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name on the bank account" value={bank.account_name}
                 onChange={(v) => setBank({ ...bank, account_name: v })} />
          <Field label="Account number" value={bank.account_number}
                 onChange={(v) => setBank({ ...bank, account_number: v })} />
          {country === "IN" && <Field label="IFSC" value={bank.ifsc}
                 onChange={(v) => setBank({ ...bank, ifsc: v })} />}
          {["GB", "DE"].includes(country) && <Field label="IBAN" value={bank.iban}
                 onChange={(v) => setBank({ ...bank, iban: v })} />}
          {country === "US" && <Field label="ABA routing number" value={bank.routing_number}
                 onChange={(v) => setBank({ ...bank, routing_number: v })} />}
        </div>
      </section>

      {!!activeFields.length && (
        <section className="rounded-xl border border-slate-200 bg-white p-4 space-y-3">
          <h2 className="text-sm font-semibold text-slate-800">
            Because you are a {cat?.label.toLowerCase()} vendor
          </h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {activeFields.map((f) => {
              const r = (resolved?.fields ?? []).find((x) => x.key === f.key);
              return (
                <div key={f.key}>
                  {f.type === "select" ? (
                    <label className="block">
                      <span className="text-xs font-medium text-slate-700">
                        {f.label}{r?.effective === "required" && <Req />}
                      </span>
                      <select value={custom[f.key] ?? ""}
                              onChange={(e) => setCustom({ ...custom, [f.key]: e.target.value })}
                              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
                        <option value="">Select…</option>
                        {(f.options ?? []).map((o: string) => <option key={o} value={o}>{o}</option>)}
                      </select>
                    </label>
                  ) : (
                    <Field label={f.label} required={r?.effective === "required"}
                           type={f.type === "number" || f.type === "currency" ? "number" : "text"}
                           value={custom[f.key] ?? ""}
                           onChange={(v) => setCustom({ ...custom, [f.key]: v })} />
                  )}
                  {(f.why || r?.why) && (
                    <p className="mt-1 text-[11px] leading-snug text-slate-500">{f.why || r?.why}</p>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      <section className="rounded-xl border border-slate-200 bg-white p-4 space-y-3">
        <h2 className="text-sm font-semibold text-slate-800">Documents</h2>
        {neededDocs.map((d) => {
          const pf = preflight[d.key];
          return (
            <div key={d.key} className="rounded-lg border border-slate-200 p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-800">{d.label}</span>
                    <ReqChip r={d} />
                  </div>
                  {d.why && <p className="mt-0.5 text-[11px] text-slate-500">{d.why}</p>}
                  {d.declared === "conditional" && d.when_explained && (
                    <p className="mt-0.5 text-[11px] text-slate-400">
                      Asked because {d.when_explained}
                    </p>
                  )}
                </div>
                <label className="shrink-0 cursor-pointer rounded-lg border border-slate-300 px-3 py-1.5 text-xs">
                  {files[d.key] ? "Replace" : "Choose file"}
                  <input type="file" className="hidden"
                         accept="application/pdf,image/png,image/jpeg,image/webp"
                         onChange={(e) => attach(d.key, e.target.files?.[0] ?? null)} />
                </label>
              </div>
              {files[d.key] && (
                <p className="mt-1.5 truncate text-[11px] text-slate-500">{files[d.key].name}</p>
              )}
              {!files[d.key] && prefillDocs.some((p) => p.doc_type === d.key) && (
                <p className="mt-1.5 truncate text-[11px] text-slate-500">
                  <span className="font-medium text-slate-700">Supplied by this example</span>
                  {" · "}
                  {prefillDocs.find((p) => p.doc_type === d.key)?.filename}
                  {" — attach a file to override it."}
                </p>
              )}
              {pf?.checking && <p className="mt-1 text-[11px] text-slate-500">checking…</p>}
              {pf && !pf.checking && pf.message && (
                <p className={`mt-1 text-[11px] ${
                  pf.verdict === "VERIFIED" ? "text-emerald-700" : "text-amber-700"}`}>
                  {pf.message}
                </p>
              )}
            </div>
          );
        })}
      </section>

      {error && <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</div>}

      <div className="flex items-center justify-between gap-4 rounded-xl border border-slate-200 bg-white p-4">
        <p className="text-xs text-slate-600">
          {missingRequired.length
            ? `Still needed: ${missingRequired.slice(0, 3).join(", ")}${
                missingRequired.length > 3 ? ` +${missingRequired.length - 3} more` : ""}`
            : "Everything required is here."}
        </p>
        <button onClick={submit}
                className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
                disabled={!core.legal_name.trim() || !core.contact_email.trim()}>
          Submit for verification
        </button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- bits */

/**
 * The demo shortcut: fill the form with a case worth looking at.
 *
 * Split into the ordinary and the interesting, because the point of the edge
 * cases is lost if they sit in an undifferentiated list. Each one states the
 * verdict it should reach BEFORE it is run — a claim the visitor can then
 * watch succeed or fail, rather than a result narrated after the fact.
 */
function ScenarioPicker({ scenarios, onPick }: {
  scenarios: Scenario[];
  onPick: (s: Scenario) => void;
}) {
  const happy = scenarios.filter((s) => s.kind === "happy");
  const edge = scenarios.filter((s) => s.kind === "edge");
  return (
    <section className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
      <h2 className="text-sm font-semibold text-slate-800">
        Or load a prepared case
      </h2>
      <p className="mt-0.5 text-xs text-slate-600">
        Fills the form with real data. It then runs through exactly the same
        checks as anything typed by hand — the verdict is computed, not scripted.
      </p>

      <Group title="Straightforward" items={happy} onPick={onPick} />
      <Group
        title="Edge cases"
        hint="Where the obvious rule gives the wrong answer."
        items={edge}
        onPick={onPick}
      />
    </section>
  );
}

function Group({ title, hint, items, onPick }: {
  title: string; hint?: string; items: Scenario[];
  onPick: (s: Scenario) => void;
}) {
  if (!items.length) return null;
  return (
    <div className="mt-4">
      <div className="flex items-baseline gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
          {title}
        </span>
        {hint && <span className="text-[11px] text-slate-400">{hint}</span>}
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        {items.map((s) => (
          <button
            key={s.id}
            onClick={() => onPick(s)}
            title={s.teaches}
            className="rounded-lg border border-slate-200 bg-white p-3 text-left transition hover:border-slate-900"
          >
            <div className="flex items-start justify-between gap-2">
              <span className="text-sm font-medium text-slate-900">{s.label}</span>
              <StatusPill status={s.expect} />
            </div>
            <p className="mt-1 text-[11px] leading-snug text-slate-500">{s.blurb}</p>
          </button>
        ))}
      </div>
    </div>
  );
}

/** Shown above a prefilled form: what this case is for, and what to watch. */
function ScenarioBanner({ s, onClear }: { s: Scenario; onClear: () => void }) {
  return (
    <section className="rounded-xl border border-slate-300 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="rounded bg-slate-900 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-white">
              {s.kind === "edge" ? "Edge case" : "Example"}
            </span>
            <span className="text-sm font-semibold text-slate-900">{s.label}</span>
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-slate-600">{s.teaches}</p>
        </div>
        <button onClick={onClear} className="shrink-0 text-[11px] text-slate-500 underline">
          clear
        </button>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
        <span className="text-[11px] text-slate-500">Should come back as</span>
        <StatusPill status={s.expect} />
        <span className="text-[11px] text-slate-400">— {s.expect_why}</span>
      </div>
    </section>
  );
}

function Field({ label, value, onChange, required, type = "text" }: {
  label: string; value: string; onChange: (v: string) => void;
  required?: boolean; type?: string;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-700">{label}{required && <Req />}</span>
      <input type={type} value={value} onChange={(e) => onChange(e.target.value)}
             className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
    </label>
  );
}

const Req = () => <span className="ml-1 text-rose-500">*</span>;

function ReqChip({ r }: { r: ResolvedRequirement }) {
  const map: Record<string, string> = {
    required: "bg-rose-50 text-rose-700 ring-rose-200",
    optional: "bg-slate-100 text-slate-600 ring-slate-200",
    na: "bg-slate-50 text-slate-400 ring-slate-200",
  };
  const label = r.effective === "required"
    ? (r.declared === "conditional" ? "required for you" : "required")
    : r.effective;
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset ${map[r.effective]}`}>{label}</span>;
}

function KindChip({ kind }: { kind: string }) {
  const ai = kind === "ai";
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset ${
      ai ? "bg-violet-50 text-violet-700 ring-violet-200"
         : "bg-indigo-50 text-indigo-700 ring-indigo-200"}`}>
      {ai ? "AI" : "Rule"}
    </span>
  );
}

function FindingRow({ f }: { f: Finding }) {
  const m = sevMeta(f);
  return (
    <div className="mt-1.5 flex items-start gap-2">
      <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset ${m.cls}`}>
        {m.label}
      </span>
      <span className="text-[11px] leading-snug text-slate-700">
        {f.vendor_message || f.message}
      </span>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const m = statusMeta(status);
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold ring-1 ring-inset ${m.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${m.dot}`} />{m.label}
    </span>
  );
}

function VerdictCard({ case_ }: { case_: any }) {
  const conf = case_.confidence || {};
  const items: string[] = case_.vendor_items || [];
  const m = statusMeta(case_.status);
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Verdict</div>
      <div className="mt-1 text-lg font-bold text-slate-900">
        {conf.recommendation || m.label}
      </div>
      <p className="mt-1 text-sm text-slate-700">{conf.decision_reason || m.blurb}</p>
      {typeof conf.score === "number" && (
        <p className="mt-2 text-xs text-slate-500">Confidence {Math.round(conf.score * 100)}%</p>
      )}
      {!!items.length && (
        <div className="mt-3 rounded-lg bg-slate-50 p-3">
          <div className="text-xs font-semibold text-slate-700">What we need from you</div>
          <ul className="mt-1 space-y-1">
            {items.map((i, k) => <li key={k} className="text-xs text-slate-700">• {i}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}
