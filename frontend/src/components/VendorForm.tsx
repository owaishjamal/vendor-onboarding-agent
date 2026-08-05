import { useEffect, useMemo, useState } from "react";
import { api, Preflight } from "../api";

/**
 * A real vendor onboarding form — the surface a supplier would actually fill
 * in. It collects company details, contact, directors (with DOB/nationality so
 * screening can run two-factor), banking (fields adapt to the country's payment
 * scheme), and document uploads (driven by what the country requires).
 *
 * On submit it produces a genuine submission and streams it through the same
 * pipeline as everything else — so ANY data entered here, including uploaded
 * PDFs, is verified for real. This is the "new test case" path.
 */

export interface FormPayload {
  submission: any;
  files: Record<string, File>;
}

type Director = { name: string; dob: string; nationality: string };
type DocSlot = { doc_type: string; label: string; accepted: string[]; file?: File;
                 checking?: boolean; verdict?: Preflight };

const BLANK = {
  legal_name: "", trading_name: "", country: "", entity_type: "",
  registration_number: "", tax_id: "",
  address_line1: "", address_city: "", address_postcode: "", address_country: "",
  contact_name: "", contact_email: "", website: "",
  bank_account_name: "", bank_iban: "", bank_account_number: "",
  bank_routing_number: "", bank_ifsc: "", bank_swift_bic: "",
  bank_name: "", bank_country: "",
};

const CORE_KEYS = new Set(["legal_name", "registration_number", "tax_id", "bank.account_name"]);

export default function VendorForm({ onSubmit, running, profileId = "default", initial, hideSamples }: {
  onSubmit: (p: FormPayload) => void; running: boolean;
  profileId?: string; initial?: any; hideSamples?: boolean;
}) {
  const [countries, setCountries] = useState<any[]>([]);
  const [samples, setSamples] = useState<any[]>([]);
  const [f, setF] = useState({ ...BLANK });
  const [directors, setDirectors] = useState<Director[]>([{ name: "", dob: "", nationality: "" }]);
  const [docs, setDocs] = useState<DocSlot[]>([]);
  const [profile, setProfile] = useState<any>(null);
  const [customVals, setCustomVals] = useState<Record<string, string>>({});

  useEffect(() => {
    api.countries().then(setCountries).catch(() => {});
    if (!hideSamples) api.samples().then(setSamples).catch(() => {});
  }, [hideSamples]);

  const country = useMemo(() => countries.find((c) => c.code === f.country), [countries, f.country]);
  const scheme: string = country?.bank_scheme ?? "iban";

  // The Requirement Profile drives the document slots AND the custom fields.
  // "default" resolves to the country packs, so behaviour without a chosen
  // profile is exactly what it always was.
  useEffect(() => {
    if (!f.country) { setDocs([]); setProfile(null); return; }
    api.profile(profileId || "default", f.country).then((p) => {
      setProfile(p);
      setDocs((p.documents ?? []).map((d: any) => ({
        doc_type: d.key, label: d.label, accepted: d.accepted ?? [],
      })));
    }).catch(() => setProfile(null));
  }, [f.country, profileId]);

  const customFields = useMemo(
    () => (profile?.fields ?? []).filter((s: any) => !CORE_KEYS.has(s.key)),
    [profile]);

  // Seed the form from an existing submission (vendor portal resubmission).
  useEffect(() => {
    if (!initial) return;
    setF({
      ...BLANK,
      legal_name: initial.legal_name ?? "", trading_name: initial.trading_name ?? "",
      country: initial.country ?? "", entity_type: initial.entity_type ?? "",
      registration_number: initial.registration_number ?? "", tax_id: initial.tax_id ?? "",
      address_line1: initial.address_line1 ?? "", address_city: initial.address_city ?? "",
      address_postcode: initial.address_postcode ?? "", address_country: initial.address_country ?? "",
      contact_name: initial.contact_name ?? "", contact_email: initial.contact_email ?? "",
      website: initial.website ?? "",
      bank_account_name: initial.bank?.account_name ?? "", bank_iban: initial.bank?.iban ?? "",
      bank_account_number: initial.bank?.account_number ?? "",
      bank_routing_number: initial.bank?.routing_number ?? "", bank_ifsc: initial.bank?.ifsc ?? "",
      bank_swift_bic: initial.bank?.swift_bic ?? "", bank_name: initial.bank?.bank_name ?? "",
      bank_country: initial.bank?.bank_country ?? "",
    });
    const dd = (initial.director_details?.length
      ? initial.director_details
      : (initial.directors ?? []).map((n: string) => ({ name: n, dob: "", nationality: "" }))
    ).map((d: any) => ({ name: d.name ?? "", dob: d.dob ?? "", nationality: d.nationality ?? "" }));
    setDirectors(dd.length ? dd : [{ name: "", dob: "", nationality: "" }]);
    setCustomVals(Object.fromEntries(
      Object.entries(initial.custom_fields ?? {}).map(([k, v]) => [k, String(v ?? "")])));
  }, [initial]);

  const set = (k: keyof typeof BLANK, v: string) => setF((p) => ({ ...p, [k]: v }));

  // On attach, verify the document immediately (preflight) so a wrong or
  // irrelevant file is flagged before the vendor ever submits.
  const onDocFile = async (i: number, file?: File) => {
    setDocs((p) => p.map((x, k) => k === i ? { ...x, file, verdict: undefined, checking: !!file } : x));
    if (!file) return;
    const slot = docs[i];
    try {
      const verdict = await api.preflight(file, slot.doc_type, f.country, f.legal_name);
      setDocs((p) => p.map((x, k) => k === i ? { ...x, checking: false, verdict } : x));
    } catch {
      setDocs((p) => p.map((x, k) => k === i ? { ...x, checking: false } : x));
    }
  };

  // --- prefill from a bundled example (fills text fields to save typing) ----
  const prefill = async (file: string) => {
    if (!file) return;
    const s = await api.sampleBody(file);
    setF({
      ...BLANK,
      legal_name: s.legal_name ?? "", trading_name: s.trading_name ?? "",
      country: s.country ?? "", entity_type: s.entity_type ?? "",
      registration_number: s.registration_number ?? "", tax_id: s.tax_id ?? "",
      address_line1: s.address_line1 ?? "", address_city: s.address_city ?? "",
      address_postcode: s.address_postcode ?? "", address_country: s.address_country ?? "",
      contact_name: s.contact_name ?? "", contact_email: s.contact_email ?? "",
      website: s.website ?? "",
      bank_account_name: s.bank?.account_name ?? "", bank_iban: s.bank?.iban ?? "",
      bank_account_number: s.bank?.account_number ?? "",
      bank_routing_number: s.bank?.routing_number ?? "", bank_ifsc: s.bank?.ifsc ?? "",
      bank_swift_bic: s.bank?.swift_bic ?? "", bank_name: s.bank?.bank_name ?? "",
      bank_country: s.bank?.bank_country ?? "",
    });
    const dd: Director[] = (s.director_details?.length
      ? s.director_details
      : (s.directors ?? []).map((n: string) => ({ name: n, dob: "", nationality: "" }))
    ).map((d: any) => ({ name: d.name ?? "", dob: d.dob ?? "", nationality: d.nationality ?? "" }));
    setDirectors(dd.length ? dd : [{ name: "", dob: "", nationality: "" }]);
  };

  const submit = () => {
    const bank: any = { account_name: f.bank_account_name || null, bank_name: f.bank_name || null,
      bank_country: f.bank_country || null, swift_bic: f.bank_swift_bic || null };
    if (scheme === "iban") bank.iban = f.bank_iban || null;
    if (scheme === "aba") { bank.routing_number = f.bank_routing_number || null; bank.account_number = f.bank_account_number || null; }
    if (scheme === "ifsc") { bank.ifsc = f.bank_ifsc || null; bank.account_number = f.bank_account_number || null; }
    if (scheme === "swift_account") bank.account_number = f.bank_account_number || null;

    const dirRows = directors.filter((d) => d.name.trim());
    const files: Record<string, File> = {};
    const documents = docs.filter((d) => d.file).map((d) => {
      files[d.file!.name] = d.file!;
      return { doc_type: d.doc_type, filename: d.file!.name };
    });

    const submission: any = {
      profile_id: profileId || "default",
      custom_fields: Object.fromEntries(
        Object.entries(customVals).filter(([, v]) => v !== "")),
      legal_name: f.legal_name, trading_name: f.trading_name || null,
      country: f.country, entity_type: f.entity_type || null,
      registration_number: f.registration_number || null, tax_id: f.tax_id || null,
      address_line1: f.address_line1 || null, address_city: f.address_city || null,
      address_postcode: f.address_postcode || null, address_country: f.address_country || null,
      contact_name: f.contact_name || null, contact_email: f.contact_email || null,
      website: f.website || null,
      directors: dirRows.map((d) => d.name),
      director_details: dirRows.filter((d) => d.dob || d.nationality),
      bank, documents,
    };
    onSubmit({ submission, files });
  };

  const canSubmit = f.legal_name.trim() && f.country && !running;

  return (
    <div className="space-y-4">
      {/* prefill */}
      {!hideSamples && (
      <div className="flex items-center gap-2 rounded-lg bg-slate-100 px-3 py-2">
        <span className="text-[11px] font-medium text-slate-500">Start from an example:</span>
        <select
          defaultValue=""
          onChange={(e) => prefill(e.target.value)}
          className="flex-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-xs"
        >
          <option value="">— blank form —</option>
          {samples.map((s) => (
            <option key={s.file} value={s.file}>{s.submission_id} · {s.legal_name}</option>
          ))}
        </select>
      </div>
      )}

      <Section title="Company details">
        <Field label="Registered legal name" req value={f.legal_name} onChange={(v) => set("legal_name", v)} />
        <Field label="Trading name (if different)" value={f.trading_name} onChange={(v) => set("trading_name", v)} />
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label text="Country of registration" req />
            <select value={f.country} onChange={(e) => set("country", e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm">
              <option value="">Select…</option>
              {countries.map((c) => <option key={c.code} value={c.code}>{c.code} — {c.name}</option>)}
            </select>
          </div>
          <Field label="Entity type" value={f.entity_type} onChange={(v) => set("entity_type", v)} placeholder="e.g. Private limited company" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label={country?.registration_number?.label ?? "Registration number"}
            value={f.registration_number} onChange={(v) => set("registration_number", v)}
            hint={country?.registration_number?.example ? `e.g. ${country.registration_number.example}` : undefined} />
          <Field label={country?.tax_id?.label ?? "Tax registration number"}
            value={f.tax_id} onChange={(v) => set("tax_id", v)}
            hint={country?.tax_id?.example ? `e.g. ${country.tax_id.example}` : undefined} />
        </div>
      </Section>

      <Section title="Registered address">
        <Field label="Address line" value={f.address_line1} onChange={(v) => set("address_line1", v)} />
        <div className="grid grid-cols-3 gap-3">
          <Field label="City" value={f.address_city} onChange={(v) => set("address_city", v)} />
          <Field label="Postcode" value={f.address_postcode} onChange={(v) => set("address_postcode", v)} />
          <Field label="Country" value={f.address_country} onChange={(v) => set("address_country", v)} placeholder="ISO, e.g. GB" />
        </div>
      </Section>

      <Section title="Contact">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Contact name" value={f.contact_name} onChange={(v) => set("contact_name", v)} />
          <Field label="Contact email" value={f.contact_email} onChange={(v) => set("contact_email", v)} />
        </div>
        <Field label="Website" value={f.website} onChange={(v) => set("website", v)} placeholder="https://" />
      </Section>

      <Section title="Directors / beneficial owners"
        note="Date of birth and nationality let screening tell a real match from an innocent namesake.">
        {directors.map((d, i) => (
          <div key={i} className="grid grid-cols-[1fr_130px_90px_auto] items-end gap-2">
            <Field label={i === 0 ? "Full name" : ""} value={d.name}
              onChange={(v) => setDirectors((p) => p.map((x, k) => k === i ? { ...x, name: v } : x))} />
            <div>
              {i === 0 && <Label text="Date of birth" />}
              <input type="date" value={d.dob}
                onChange={(e) => setDirectors((p) => p.map((x, k) => k === i ? { ...x, dob: e.target.value } : x))}
                className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
            </div>
            <Field label={i === 0 ? "Nat." : ""} value={d.nationality} placeholder="GB"
              onChange={(v) => setDirectors((p) => p.map((x, k) => k === i ? { ...x, nationality: v } : x))} />
            <button onClick={() => setDirectors((p) => p.filter((_, k) => k !== i))}
              className="mb-1 px-2 text-slate-400 hover:text-rose-600" title="Remove">×</button>
          </div>
        ))}
        <button onClick={() => setDirectors((p) => [...p, { name: "", dob: "", nationality: "" }])}
          className="text-xs font-medium text-indigo-600 hover:text-indigo-800">+ add director</button>
      </Section>

      <Section title="Banking"
        note={scheme === "iban" ? "This country uses IBAN." : scheme === "aba"
          ? "This country uses ABA routing + account number." : `Scheme: ${scheme}.`}>
        <Field label="Name on the bank account" value={f.bank_account_name}
          onChange={(v) => set("bank_account_name", v)}
          hint="Must match the registered legal name — a mismatch is the top fraud signal." />
        <div className="grid grid-cols-2 gap-3">
          {scheme === "iban" && <Field label="IBAN" value={f.bank_iban} onChange={(v) => set("bank_iban", v)} />}
          {scheme === "aba" && <>
            <Field label="Routing number (ABA)" value={f.bank_routing_number} onChange={(v) => set("bank_routing_number", v)} />
            <Field label="Account number" value={f.bank_account_number} onChange={(v) => set("bank_account_number", v)} />
          </>}
          {scheme === "ifsc" && <>
            <Field label="IFSC code" value={f.bank_ifsc} onChange={(v) => set("bank_ifsc", v)} />
            <Field label="Account number" value={f.bank_account_number} onChange={(v) => set("bank_account_number", v)} />
          </>}
          {scheme === "swift_account" && <Field label="Account number" value={f.bank_account_number} onChange={(v) => set("bank_account_number", v)} />}
          <Field label="SWIFT / BIC" value={f.bank_swift_bic} onChange={(v) => set("bank_swift_bic", v)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Bank name" value={f.bank_name} onChange={(v) => set("bank_name", v)} />
          <Field label="Bank country" value={f.bank_country} onChange={(v) => set("bank_country", v)} placeholder="ISO, e.g. GB" />
        </div>
      </Section>

      {customFields.length > 0 && (
        <Section title={profile?.name ?? "Additional requirements"}
          note="Requirements specific to this onboarding profile. Each is validated automatically.">
          {customFields.map((s: any) => (
            <div key={s.key}>
              {s.type === "select" ? (
                <div>
                  <Label text={s.label} req={s.required} />
                  <select
                    value={customVals[s.key] ?? ""}
                    onChange={(e) => setCustomVals((p) => ({ ...p, [s.key]: e.target.value }))}
                    className="w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm"
                  >
                    <option value="">Select…</option>
                    {(s.options ?? []).map((o: string) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
              ) : (
                <Field
                  label={s.label} req={s.required}
                  value={customVals[s.key] ?? ""}
                  onChange={(v) => setCustomVals((p) => ({ ...p, [s.key]: v }))}
                  placeholder={s.type === "date" ? "YYYY-MM-DD" : undefined}
                  hint={s.hint}
                />
              )}
            </div>
          ))}
        </Section>
      )}

      <Section title="Documents"
        note={country ? "Upload the documents this profile requires. They're read and cross-checked against the form."
          : "Select a country to see the required documents."}>
        {!docs.length && country && <p className="text-xs text-slate-400">No specific documents required.</p>}
        {docs.map((d, i) => {
          const vlevel = d.verdict?.level;
          const ring = vlevel === "error" ? "border-rose-300 bg-rose-50/40"
            : vlevel === "warn" ? "border-amber-300 bg-amber-50/40"
            : vlevel === "ok" ? "border-emerald-300 bg-emerald-50/40" : "border-slate-200";
          return (
          <div key={d.doc_type} className={`rounded-lg border p-2.5 ${ring}`}>
            <div className="flex items-center justify-between">
              <div className="text-xs font-medium text-slate-700">{d.label}</div>
              {d.file && <span className="text-[10px] font-medium text-slate-500 truncate max-w-[45%]">{d.file.name}</span>}
            </div>
            <div className="mt-0.5 text-[10px] text-slate-400">
              accepts: {d.accepted.join(", ") || "any"} · PDF or image
            </div>
            <input type="file" accept="application/pdf,image/*"
              onChange={(e) => onDocFile(i, e.target.files?.[0])}
              className="mt-1.5 block w-full text-[11px] file:mr-3 file:rounded file:border-0 file:bg-slate-100 file:px-2 file:py-1 file:text-[11px] file:font-medium" />
            {d.checking && (
              <div className="mt-1.5 text-[11px] text-slate-500">Checking the document…</div>
            )}
            {d.verdict && !d.checking && (
              <div className={`mt-1.5 flex items-start gap-1.5 text-[11px] leading-snug ${
                vlevel === "error" ? "text-rose-700" : vlevel === "warn" ? "text-amber-700" : "text-emerald-700"}`}>
                <span>{vlevel === "error" ? "✕" : vlevel === "warn" ? "⚠" : "✓"}</span>
                <span>{d.verdict.message}</span>
              </div>
            )}
          </div>
          );
        })}
      </Section>

      <div className="sticky bottom-0 -mx-1 bg-gradient-to-t from-white via-white to-transparent pt-3">
        <button
          onClick={submit}
          disabled={!canSubmit}
          className="w-full rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:opacity-40"
        >
          {running ? "Verifying…" : "Submit for verification"}
        </button>
        {!canSubmit && !running && (
          <p className="mt-1 text-center text-[11px] text-slate-400">
            Legal name and country are required to submit.
          </p>
        )}
      </div>
    </div>
  );
}

// --- small field primitives ------------------------------------------------

function Label({ text, req }: { text: string; req?: boolean }) {
  if (!text) return <div className="h-0" />;
  return (
    <label className="mb-1 block text-[11px] font-medium text-slate-500">
      {text}{req && <span className="text-rose-500"> *</span>}
    </label>
  );
}

function Field({ label, value, onChange, placeholder, hint, req }: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder?: string; hint?: string; req?: boolean;
}) {
  return (
    <div>
      <Label text={label} req={req} />
      <input
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100"
      />
      {hint && <p className="mt-0.5 text-[10px] leading-snug text-slate-400">{hint}</p>}
    </div>
  );
}

function Section({ title, note, children }: {
  title: string; note?: string; children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
      {note && <p className="mb-3 mt-0.5 text-[11px] leading-relaxed text-slate-400">{note}</p>}
      {!note && <div className="mb-3" />}
      <div className="space-y-3">{children}</div>
    </section>
  );
}
