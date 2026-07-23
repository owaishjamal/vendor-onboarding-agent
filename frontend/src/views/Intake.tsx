import { useEffect, useRef, useState } from "react";
import {
  Case, CheckPlan, CheckResult, Sample, STATUS_META, statusMeta, api,
  streamCase, streamForm, flag,
} from "../api";
import CheckTimeline from "../components/CheckTimeline";
import VendorForm, { FormPayload } from "../components/VendorForm";
import { StatusBadge } from "../components/Badges";

export default function Intake({ onFinished }: { onFinished: () => void }) {
  const [samples, setSamples] = useState<Sample[]>([]);
  const [plan, setPlan] = useState<CheckPlan[]>([]);
  const [results, setResults] = useState<CheckResult[]>([]);
  const [kase, setCase] = useState<Case | null>(null);
  const [running, setRunning] = useState(false);
  const [current, setCurrent] = useState("");
  const [error, setError] = useState("");
  const [raw, setRaw] = useState<any>(null);
  const [showRaw, setShowRaw] = useState(false);
  const [pasted, setPasted] = useState("");
  const [mode, setMode] = useState<"form" | "samples" | "paste">("form");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { api.samples().then(setSamples).catch(() => setSamples([])); }, []);
  useEffect(() => {
    if (running) endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [results.length, running]);

  const begin = (label: string) => {
    setRunning(true); setResults([]); setCase(null); setError(""); setCurrent(label);
  };
  const handlers = {
    onPlan: setPlan,
    onCheck: (r: CheckResult) => setResults((p) => [...p, r]),
    onDone: (c: Case) => { setCase(c); setRunning(false); onFinished(); },
    onError: (m: string) => { setError(m); setRunning(false); },
  };

  const start = async (body: Parameters<typeof streamCase>[0], label: string) => {
    begin(label);
    await streamCase(body, handlers);
    setRunning(false);
  };

  const runForm = async ({ submission, files }: FormPayload) => {
    setRaw(submission);
    begin(submission.legal_name || "new submission");
    await streamForm(submission, files, handlers);
    setRunning(false);
  };

  const runSample = async (s: Sample) => {
    try { setRaw(await api.sampleBody(s.file)); } catch { setRaw(null); }
    start({ kind: "sample", name: s.file }, s.legal_name);
  };

  const runPasted = () => {
    try {
      const data = JSON.parse(pasted);
      setRaw(data);
      start({ kind: "submission", data }, data.legal_name || "pasted submission");
    } catch (e: any) {
      setError(`Could not parse JSON: ${e.message}`);
    }
  };

  const matched = kase && samples.find((s) => s.legal_name === current);
  const asExpected = matched ? matched.expected_status === kase!.status : null;

  return (
    <div className="grid gap-6 lg:grid-cols-[440px_1fr]">
      {/* ---------------- left ---------------- */}
      <div className="space-y-4">
        <div className="flex gap-1 rounded-lg bg-slate-100 p-1">
          {(["form", "samples", "paste"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition ${
                mode === m ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"
              }`}
            >
              {m === "form" ? "Vendor form" : m === "samples" ? "Sample vendors" : "Paste JSON"}
            </button>
          ))}
        </div>

        {mode === "form" ? (
          <VendorForm onSubmit={runForm} running={running} />
        ) : mode === "samples" ? (
          <div className="space-y-2">
            {samples.map((s) => {
              const m = STATUS_META[s.expected_status];
              const active = current === s.legal_name;
              return (
                <button
                  key={s.file}
                  disabled={running}
                  onClick={() => runSample(s)}
                  className={`w-full rounded-xl border bg-white p-3 text-left transition hover:border-slate-400 disabled:opacity-50 ${
                    active ? "border-indigo-400 ring-2 ring-indigo-100" : "border-slate-200"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-slate-800">{s.legal_name}</div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-500">
                        <span className="font-mono">{s.submission_id}</span>
                        <span>·</span>
                        <span>{flag(s.country)}</span>
                      </div>
                    </div>
                  </div>
                  <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500">{s.scenario}</p>
                  <div className={`mt-2 inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset ${m.cls}`}>
                    <span className={`h-1 w-1 rounded-full ${m.dot}`} />
                    expects {m.label}
                  </div>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="space-y-2">
            <textarea
              value={pasted}
              onChange={(e) => setPasted(e.target.value)}
              placeholder={'{\n  "legal_name": "Example Ltd",\n  "country": "GB",\n  ...\n}'}
              className="scroll-thin h-72 w-full resize-none rounded-xl border border-slate-300 bg-white p-3 font-mono text-[11px] leading-relaxed text-slate-800 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100"
            />
            <button
              onClick={runPasted}
              disabled={running || !pasted.trim()}
              className="w-full rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-40"
            >
              Run submission
            </button>
            <p className="text-[11px] leading-relaxed text-slate-400">
              Edit a test submission and paste it here to see how the outcome changes —
              useful for showing that the rules do the work, not the fixtures.
            </p>
          </div>
        )}

        <button
          onClick={async () => { await api.reset(); setResults([]); setCase(null); setCurrent(""); onFinished(); }}
          disabled={running}
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
        >
          Clear case history
        </button>
      </div>

      {/* ---------------- right ---------------- */}
      <div className="min-w-0 space-y-4">
        {!results.length && !running && (
          <div className="flex h-full min-h-[440px] items-center justify-center rounded-xl border border-slate-200 bg-white">
            <div className="max-w-sm px-6 text-center">
              <div className="text-sm font-semibold text-slate-700">No submission in progress</div>
              <p className="mt-1 text-xs leading-relaxed text-slate-500">
                Fill in the vendor form and submit — or prefill it from an example. Every
                check runs on every submission (nothing stops early), and each one appears
                here as it completes.
              </p>
            </div>
          </div>
        )}

        {kase && (
          <div className="animate-slidein rounded-xl border border-slate-200 bg-white p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge s={kase.status} size="lg" />
                  <span className="text-xs text-slate-500">{statusMeta(kase.status).who}</span>
                  {asExpected !== null && (
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                      asExpected ? "bg-slate-100 text-slate-500" : "bg-rose-100 text-rose-700"}`}>
                      {asExpected ? "matches expected" : "differs from expected"}
                    </span>
                  )}
                </div>
                <h2 className="mt-2 text-base font-semibold text-slate-900">{kase.legal_name}</h2>
                <p className="mt-1 max-w-2xl text-sm leading-relaxed text-slate-700">
                  {kase.reviewer_summary}
                </p>
              </div>
              <div className="shrink-0 text-right text-xs text-slate-500">
                <div>{flag(kase.country)}</div>
                <div className="font-mono text-[10px] text-slate-400">{kase.case_id}</div>
              </div>
            </div>

            {kase.vendor_email ? (
              <div className="mt-4 overflow-hidden rounded-lg ring-1 ring-inset ring-sky-200">
                <div className="flex items-center justify-between bg-sky-50 px-3 py-1.5">
                  <span className="text-[10px] font-bold uppercase tracking-wide text-sky-800">
                    Drafted reply to vendor
                  </span>
                  <span className="text-[10px] text-sky-600">{kase.contact_email}</span>
                </div>
                <pre className="whitespace-pre-wrap bg-white px-3 py-2.5 font-sans text-[12px] leading-relaxed text-slate-700">
                  {kase.vendor_email}
                </pre>
              </div>
            ) : kase.status !== "APPROVED" && (
              <div className="mt-4 rounded-lg bg-slate-900 px-3 py-2 text-[11px] leading-relaxed text-slate-200">
                <span className="font-semibold">No vendor email generated.</span>{" "}
                {kase.status === "REJECTED"
                  ? "This case was rejected on screening. Contacting the vendor would disclose which control caught them, so nothing is sent."
                  : "This case is under internal review. Contacting the vendor now could tip off a fraudster and taint the review — resolve internally first."}
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>
        )}

        {(results.length > 0 || running) && (
          <div className="rounded-xl border border-slate-200 bg-white p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-800">
                {running ? "Running checks" : "Check results"}
              </h2>
              {raw && (
                <button
                  onClick={() => setShowRaw((v) => !v)}
                  className="text-[11px] font-medium text-slate-400 hover:text-slate-700"
                >
                  {showRaw ? "Hide" : "Show"} submitted JSON
                </button>
              )}
            </div>

            {showRaw && raw && (
              <pre className="scroll-thin mb-4 max-h-60 overflow-auto rounded-lg bg-slate-900 p-3 font-mono text-[10.5px] leading-relaxed text-slate-200">
                {JSON.stringify(raw, null, 2)}
              </pre>
            )}

            <CheckTimeline plan={plan} results={results} running={running} />
            <div ref={endRef} />
          </div>
        )}
      </div>
    </div>
  );
}
