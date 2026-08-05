import { useEffect, useState } from "react";
import { api } from "../api";

/**
 * Onboarding template builder.
 *
 * Different Zamp clients need different things from their vendors, so the
 * admin builds a template: add the fields to collect, mark which are
 * mandatory, list the documents to require. The vendor form renders from it
 * and the verification engine reads from it — no code, no JSON.
 */

const FIELD_TYPES = [
  ["text", "Text"], ["id", "ID / reference"], ["number", "Number"],
  ["date", "Date"], ["email", "Email"], ["phone", "Phone"],
  ["select", "Dropdown"], ["url", "Website"],
] as const;

type Field = {
  key: string; label: string; type: string; required: boolean;
  regex?: string; hint?: string; options?: string[];
  validation_source?: string[];
};
type Doc = { key: string; label: string; required: boolean; expects?: string };

const slug = (s: string) =>
  s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");

export default function ProfileBuilder() {
  const [profiles, setProfiles] = useState<any[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [pid, setPid] = useState("");
  const [name, setName] = useState("");
  const [fields, setFields] = useState<Field[]>([]);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const reload = () => api.profiles().then(setProfiles).catch(() => {});
  useEffect(() => { reload(); }, []);

  const startNew = () => {
    setEditing("__new__"); setPid(""); setName("");
    setFields([]); setDocs([]); setMsg(null);
  };

  const open = async (id: string) => {
    const p = await api.profile(id);
    setEditing(id); setPid(p.profile_id); setName(p.name);
    // Only the client's OWN additions are editable; inherited country fields
    // are shown to the vendor automatically and aren't managed here.
    const core = new Set(["legal_name", "registration_number", "tax_id",
                          "bank.account_name", "pan"]);
    setFields((p.fields ?? []).filter((f: Field) => !core.has(f.key)));
    setDocs((p.documents ?? []).filter((d: Doc) => d.expects));
    setMsg(null);
  };

  const save = async () => {
    const id = (pid || slug(name)).trim();
    if (!id || id === "default") {
      setMsg({ ok: false, text: "Give the template a name first." }); return;
    }
    const bad = fields.find((f) => !f.label.trim());
    if (bad) { setMsg({ ok: false, text: "Every field needs a label." }); return; }
    try {
      const saved = await api.saveProfile(id, {
        name: name || id, extends: "country_defaults",
        fields: fields.map((f) => ({
          ...f, key: f.key || slug(f.label),
          options: f.type === "select" ? (f.options ?? []) : [],
        })),
        documents: docs.map((d) => ({ ...d, key: d.key || slug(d.label) })),
        rules: [],
      });
      setMsg({ ok: true, text: `Saved “${saved.name}” (v${saved.version}). It's now selectable on the onboarding form.` });
      setEditing(saved.profile_id); setPid(saved.profile_id); reload();
    } catch (e: any) {
      setMsg({ ok: false, text: `Save failed: ${e.message}` });
    }
  };

  const remove = async () => {
    if (!editing || editing === "__new__") return;
    await api.deleteProfile(editing);
    setEditing(null); reload();
  };

  const upField = (i: number, patch: Partial<Field>) =>
    setFields((p) => p.map((f, k) => (k === i ? { ...f, ...patch } : f)));
  const upDoc = (i: number, patch: Partial<Doc>) =>
    setDocs((p) => p.map((d, k) => (k === i ? { ...d, ...patch } : d)));

  return (
    <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
      {/* ---------------- templates list ---------------- */}
      <div className="space-y-3">
        <button onClick={startNew}
          className="w-full rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800">
          + New onboarding template
        </button>
        <div className="rounded-xl border border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Templates
          </div>
          <ul className="divide-y divide-slate-100">
            {profiles.map((p) => (
              <li key={p.profile_id}
                  onClick={() => !p.builtin && open(p.profile_id)}
                  className={`px-4 py-2.5 text-sm ${p.builtin ? "text-slate-400" : "cursor-pointer hover:bg-slate-50"} ${
                    editing === p.profile_id ? "bg-indigo-50" : ""}`}>
                <div className="font-medium text-slate-800">{p.name}</div>
                <div className="text-[10px] text-slate-400">
                  {p.builtin ? "built-in · country requirements" : `v${p.version}`}
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-[11px] leading-relaxed text-slate-400">
          Every template already includes the statutory requirements for the
          vendor's country (GSTIN, PAN and the usual documents for India). Add
          only what's specific to this client.
        </p>
      </div>

      {/* ---------------- builder ---------------- */}
      <div className="space-y-4">
        {!editing ? (
          <div className="flex h-full min-h-[400px] items-center justify-center rounded-xl border border-slate-200 bg-white">
            <div className="max-w-md px-6 text-center">
              <div className="text-sm font-semibold text-slate-700">Onboarding templates</div>
              <p className="mt-1 text-xs leading-relaxed text-slate-500">
                Different clients ask their vendors for different things. Build a
                template once — the onboarding form and the AI verification both
                follow it.
              </p>
            </div>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <input value={name} onChange={(e) => setName(e.target.value)}
                placeholder="Template name, e.g. Logistics vendors"
                className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              {editing !== "__new__" && (
                <button onClick={remove}
                  className="rounded-lg border border-rose-200 px-3 py-2 text-xs font-medium text-rose-600 hover:bg-rose-50">
                  Delete
                </button>
              )}
              <button onClick={save}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700">
                Save template
              </button>
            </div>

            {msg && (
              <div className={`rounded-lg px-3 py-2 text-xs ${
                msg.ok ? "bg-emerald-50 text-emerald-800 ring-1 ring-inset ring-emerald-200"
                       : "bg-rose-50 text-rose-800 ring-1 ring-inset ring-rose-200"}`}>
                {msg.text}
              </div>
            )}

            {/* --- fields --- */}
            <section className="rounded-xl border border-slate-200 bg-white p-4">
              <h3 className="text-sm font-semibold text-slate-800">Extra information to collect</h3>
              <p className="mb-3 text-[11px] text-slate-400">
                Each field is validated automatically according to its type.
              </p>

              {fields.length === 0 && (
                <p className="mb-3 text-xs text-slate-400">No extra fields yet.</p>
              )}

              <div className="space-y-2">
                {fields.map((f, i) => (
                  <div key={i} className="rounded-lg bg-slate-50 p-2.5 ring-1 ring-inset ring-slate-200">
                    <div className="flex flex-wrap items-center gap-2">
                      <input value={f.label} placeholder="Field label"
                        onChange={(e) => upField(i, { label: e.target.value })}
                        className="min-w-[180px] flex-1 rounded border border-slate-300 px-2 py-1 text-xs" />
                      <select value={f.type}
                        onChange={(e) => upField(i, { type: e.target.value })}
                        className="rounded border border-slate-300 px-2 py-1 text-xs">
                        {FIELD_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </select>
                      <label className="flex items-center gap-1 text-[11px] text-slate-600">
                        <input type="checkbox" checked={f.required}
                          onChange={(e) => upField(i, { required: e.target.checked })} />
                        Mandatory
                      </label>
                      <button onClick={() => setFields((p) => p.filter((_, k) => k !== i))}
                        className="px-1.5 text-slate-400 hover:text-rose-600" title="Remove">×</button>
                    </div>
                    {f.type === "select" && (
                      <input
                        value={(f.options ?? []).join(", ")}
                        placeholder="Dropdown options, comma separated"
                        onChange={(e) => upField(i, {
                          options: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })}
                        className="mt-1.5 w-full rounded border border-slate-300 px-2 py-1 text-xs" />
                    )}
                    {f.type === "id" && (
                      <input value={f.regex ?? ""} placeholder="Optional format, e.g. ^HC-\\d{6}$"
                        onChange={(e) => upField(i, { regex: e.target.value })}
                        className="mt-1.5 w-full rounded border border-slate-300 px-2 py-1 font-mono text-[11px]" />
                    )}
                  </div>
                ))}
              </div>

              <button
                onClick={() => setFields((p) => [...p, {
                  key: "", label: "", type: "text", required: false }])}
                className="mt-2 text-xs font-medium text-indigo-600 hover:text-indigo-800">
                + add field
              </button>
            </section>

            {/* --- documents --- */}
            <section className="rounded-xl border border-slate-200 bg-white p-4">
              <h3 className="text-sm font-semibold text-slate-800">Extra documents to require</h3>
              <p className="mb-3 text-[11px] text-slate-400">
                Describe what the document should be — the AI checks each upload
                against that description and rejects the wrong file.
              </p>

              {docs.length === 0 && (
                <p className="mb-3 text-xs text-slate-400">No extra documents yet.</p>
              )}

              <div className="space-y-2">
                {docs.map((d, i) => (
                  <div key={i} className="rounded-lg bg-slate-50 p-2.5 ring-1 ring-inset ring-slate-200">
                    <div className="flex items-center gap-2">
                      <input value={d.label} placeholder="Document name, e.g. Insurance certificate"
                        onChange={(e) => upDoc(i, { label: e.target.value })}
                        className="flex-1 rounded border border-slate-300 px-2 py-1 text-xs" />
                      <label className="flex items-center gap-1 text-[11px] text-slate-600">
                        <input type="checkbox" checked={d.required}
                          onChange={(e) => upDoc(i, { required: e.target.checked })} />
                        Mandatory
                      </label>
                      <button onClick={() => setDocs((p) => p.filter((_, k) => k !== i))}
                        className="px-1.5 text-slate-400 hover:text-rose-600" title="Remove">×</button>
                    </div>
                    <input value={d.expects ?? ""}
                      placeholder="What should it show? e.g. A valid insurance certificate naming the business, with a policy number and expiry"
                      onChange={(e) => upDoc(i, { expects: e.target.value })}
                      className="mt-1.5 w-full rounded border border-slate-300 px-2 py-1 text-xs" />
                  </div>
                ))}
              </div>

              <button
                onClick={() => setDocs((p) => [...p, {
                  key: "", label: "", required: true, expects: "" }])}
                className="mt-2 text-xs font-medium text-indigo-600 hover:text-indigo-800">
                + add document
              </button>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
