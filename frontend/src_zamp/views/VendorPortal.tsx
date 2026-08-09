import { useEffect, useState } from "react";
import { CheckPlan, CheckResult, api, streamForm } from "../api";
import VendorForm, { FormPayload } from "../components/VendorForm";

/**
 * The vendor's own portal — reached via their per-case link, no account.
 *
 * Deliberately information-poor: status in vendor-safe language, the list of
 * requested items (only what the disclosure gate allows), and fix-and-resubmit.
 * Internal findings, screening results and reviewer notes are structurally
 * absent — the endpoint this reads from cannot serialise them.
 */
export default function VendorPortal({ token }: { token: string }) {
  const [view, setView] = useState<any>(null);
  const [error, setError] = useState("");
  const [fixing, setFixing] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number }>({ done: 0, total: 0 });
  const [submitted, setSubmitted] = useState(false);

  const load = () =>
    api.vendorCase(token).then(setView).catch(() => setError("This link is invalid or has expired."));
  useEffect(() => { load(); }, [token]);

  const resubmit = async ({ submission, files }: FormPayload) => {
    setRunning(true);
    await streamForm(submission, files, {
      onPlan: (p: CheckPlan[]) => setProgress({ done: 0, total: p.length }),
      onCheck: (_r: CheckResult) => setProgress((x) => ({ ...x, done: x.done + 1 })),
      onDone: () => { setRunning(false); setSubmitted(true); setFixing(false); load(); },
      onError: () => { setRunning(false); },
    }, `/v1/vendor/${encodeURIComponent(token)}/resubmit`);
    setRunning(false);
  };

  if (error) {
    return (
      <Shell>
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-center text-sm text-rose-700">
          {error}
        </div>
      </Shell>
    );
  }
  if (!view) return <Shell><div className="py-16 text-center text-sm text-slate-400">Loading…</div></Shell>;

  const TONES: Record<string, string> = {
    sky: "bg-sky-50 text-sky-700 ring-sky-200",
    emerald: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    rose: "bg-rose-50 text-rose-700 ring-rose-200",
    amber: "bg-amber-50 text-amber-800 ring-amber-200",
  };
  const tone = view.action_needed ? "sky" : view.status_label === "Approved" ? "emerald"
    : view.status_label === "Not approved" ? "rose" : "amber";
  const toneCls = TONES[tone];

  return (
    <Shell>
      <div className="space-y-4">
        <div className="rounded-xl border border-slate-200 bg-white p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
                Onboarding status · ref {view.reference}
              </div>
              <h1 className="mt-1 text-lg font-semibold text-slate-900">{view.legal_name}</h1>
            </div>
            <span className={`rounded-full px-3 py-1.5 text-sm font-semibold ring-1 ring-inset ${toneCls}`}>
              {view.status_label}
            </span>
          </div>
          <p className="mt-3 text-sm leading-relaxed text-slate-700">{view.status_message}</p>
          {view.revision > 1 && (
            <p className="mt-1 text-[11px] text-slate-400">Submission #{view.revision}</p>
          )}
        </div>

        {submitted && (
          <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
            Thank you — your updated details were received and are being reviewed.
          </div>
        )}

        {view.action_needed && view.items.length > 0 && (
          <div className="rounded-xl border border-sky-200 bg-white p-5">
            <h2 className="text-sm font-semibold text-slate-800">What we need from you</h2>
            <ul className="mt-3 space-y-2">
              {view.items.map((it: string, i: number) => (
                <li key={i} className="flex gap-2 text-sm leading-relaxed text-slate-700">
                  <span className="mt-0.5 text-sky-500">•</span><span>{it}</span>
                </li>
              ))}
            </ul>
            {!fixing && (
              <button onClick={() => setFixing(true)}
                className="mt-4 w-full rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-800">
                Update my details & resubmit
              </button>
            )}
          </div>
        )}

        {fixing && (
          <div className="rounded-xl border border-slate-200 bg-white p-5">
            <h2 className="mb-3 text-sm font-semibold text-slate-800">Update your submission</h2>
            {running && (
              <div className="mb-3 rounded-lg bg-slate-100 px-3 py-2 text-xs text-slate-600">
                Verifying your submission… {progress.done}/{progress.total || "…"} checks complete
              </div>
            )}
            <VendorForm
              onSubmit={resubmit}
              running={running}
              profileId={view.profile_id}
              initial={view.submission}
              hideSamples
            />
          </div>
        )}
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-full bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-2xl px-6 py-4">
          <div className="text-sm font-semibold tracking-tight text-slate-900">Supplier Onboarding</div>
          <div className="text-[11px] text-slate-500">Secure vendor portal</div>
        </div>
      </header>
      <main className="mx-auto max-w-2xl px-6 py-6">{children}</main>
    </div>
  );
}
