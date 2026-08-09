import os

def main():
    repo = r"c:\Users\owais\Downloads\MAO-GrabHack-main\vendor-onboarding-agent\frontend\src"

    # 1. Update api.ts
    api_ts = os.path.join(repo, "api.ts")
    with open(api_ts, "w", encoding="utf-8") as f:
        f.write("""/** Thin API client over the Zamp FastAPI backend. */

export type Case = {
  case_id: string;
  legal_name: string;
  trading_name: string;
  country: string;
  status: string;
  reviewer_summary: string | null;
  created_at: string;
  completed_at: string | null;
  top_finding?: { code: string; message: string } | null;
  finding_counts?: Record<string, number>;
};

export type CaseDetail = Case & {
  submission: any;
  change_summary: any;
  actions: any[];
  checks: any[];
  findings: any[];
};

export type Me = {
  user_id: string;
  email: string;
  role: "vendor" | "ops";
  vendor_id: string | null;
  business_name: string | null;
  market: string | null;
};

const BASE = import.meta.env.VITE_API_BASE ?? "";

async function asJson<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      detail = body.detail || JSON.stringify(body);
    } catch {}
    throw new Error(detail);
  }
  return r.json();
}

const credentials: RequestCredentials = "include";

export const api = {
  health: () => fetch(`${BASE}/health`, { credentials }).then((r) => asJson<any>(r)),
  
  me: () => fetch(`${BASE}/v1/auth/me`, { credentials }).then(async (r) => {
    if (r.status === 401) return null;
    return asJson<Me>(r);
  }),
  
  listCases: () => fetch(`${BASE}/v1/cases`, { credentials }).then((r) => asJson<Case[]>(r)),
  
  listMyCases: () => fetch(`${BASE}/v1/me/cases`, { credentials }).then((r) => asJson<Case[]>(r)),
  
  getCase: (id: string) => fetch(`${BASE}/v1/cases/${id}`, { credentials }).then((r) => asJson<CaseDetail>(r)),
  
  stats: () => fetch(`${BASE}/v1/stats`, { credentials }).then((r) => asJson<any>(r)),
  
  decide: (id: string, body: { action: string; reviewer?: string; note?: string }) =>
    fetch(`${BASE}/v1/cases/${id}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials,
    }).then((r) => asJson<any>(r)),
    
  // --- auth ---
  signup: (body: { email: string; password: string; business_name: string; market: string; }) =>
    fetch(`${BASE}/v1/auth/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials,
    }).then((r) => asJson<Me>(r)),
    
  login: (body: { email: string; password: string }) =>
    fetch(`${BASE}/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials,
    }).then((r) => asJson<Me>(r)),
    
  logout: () =>
    fetch(`${BASE}/v1/auth/logout`, { method: "POST", credentials }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error("logout failed");
    }),
};
""")

    # 2. Update ReviewerQueue.tsx
    queue_tsx = os.path.join(repo, "views", "ReviewerQueue.tsx")
    with open(queue_tsx, "w", encoding="utf-8") as f:
        f.write("""import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api, Case } from "../api";
import { StatePill } from "../components/StatePill";

export default function ReviewerQueue() {
  const nav = useNavigate();

  const q = useQuery({
    queryKey: ["ops-dashboard"],
    queryFn: () => api.listCases(),
    refetchInterval: 3000,
  });

  const rows: Case[] = q.data ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="text-xs uppercase tracking-[0.18em] text-brand-600 font-semibold">
            Ops team dashboard
          </div>
          <h1 className="text-3xl font-bold text-surface-900 mt-1 leading-tight">
            Vendor Onboarding Queue
          </h1>
          <p className="text-surface-700 text-sm mt-2 max-w-2xl">
            View and manage vendor submissions. Cases requiring manual review will show as PENDING_REVIEW.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="chip bg-surface-50 border-surface-200 text-surface-700 font-mono">
            {rows.length} cases
          </span>
          <span className="chip bg-brand-50 border-brand-100 text-brand-700">live · 3s poll</span>
        </div>
      </div>

      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-surface-50 text-surface-700 text-xs uppercase tracking-wide">
            <tr>
              <th className="px-4 py-3 text-left font-semibold">Case</th>
              <th className="px-4 py-3 text-left font-semibold">Country</th>
              <th className="px-4 py-3 text-left font-semibold">Business</th>
              <th className="px-4 py-3 text-left font-semibold">State</th>
              <th className="px-4 py-3 text-left font-semibold">Top Finding</th>
              <th className="px-4 py-3 text-left font-semibold">Created</th>
              <th className="px-4 py-3 text-right font-semibold" />
            </tr>
          </thead>
          <tbody>
            {q.isLoading && (
              <tr>
                <td colSpan={7} className="px-4 py-16 text-center text-surface-500">
                  Loading queue...
                </td>
              </tr>
            )}
            {!q.isLoading && rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-16 text-center">
                  <div className="text-surface-700 font-medium">Queue is empty</div>
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr
                key={r.case_id}
                onClick={() => nav(`/case/${r.case_id}`)}
                className="border-t border-surface-200 hover:bg-brand-50/40 transition cursor-pointer"
              >
                <td className="px-4 py-3 font-mono text-xs text-surface-700">{r.case_id}</td>
                <td className="px-4 py-3 text-surface-900">{r.country}</td>
                <td className="px-4 py-3 text-surface-900 font-medium">{r.legal_name}</td>
                <td className="px-4 py-3"><StatePill state={r.status} /></td>
                <td className="px-4 py-3">
                  {r.top_finding ? (
                    <span className="chip bg-warn-50 text-warn-700 border-warn-100 font-mono">
                      {r.top_finding.code}
                    </span>
                  ) : "—"}
                </td>
                <td className="px-4 py-3 text-surface-500 text-xs">{r.created_at}</td>
                <td className="px-4 py-3 text-right" onClick={(e) => e.stopPropagation()}>
                  <Link to={`/case/${r.case_id}`} className="btn-primary">Open</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
""")

    # 3. Update CaseDetail.tsx
    casedetail_tsx = os.path.join(repo, "views", "CaseDetail.tsx")
    with open(casedetail_tsx, "w", encoding="utf-8") as f:
        f.write("""import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate, Link } from "react-router-dom";
import { api, CaseDetail } from "../api";
import { StatePill } from "../components/StatePill";

export default function CaseView() {
  const { caseId } = useParams<{ caseId: string }>();
  const nav = useNavigate();
  const qc = useQueryClient();

  const { data: c, isLoading, error } = useQuery({
    queryKey: ["case", caseId],
    queryFn: () => api.getCase(caseId!),
    enabled: !!caseId,
    refetchInterval: 5000,
  });

  const [notes, setNotes] = useState("");

  const decide = useMutation({
    mutationFn: (action: "approve" | "reject" | "resolve" | "request_info") =>
      api.decide(caseId!, { action, note: notes }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["case", caseId] });
      qc.invalidateQueries({ queryKey: ["ops-dashboard"] });
      nav("/queue");
    },
  });

  if (isLoading) return <div className="text-surface-500 p-8">Loading...</div>;
  if (error || !c) return <div className="text-danger-500 p-8">Failed to load case.</div>;

  return (
    <div className="space-y-6">
      <div className="text-xs text-surface-500">
        <Link to="/queue" className="hover:text-brand-600 transition">
          ← Back to Queue
        </Link>
      </div>

      <header className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="text-xs text-surface-500 font-mono">{c.case_id}</div>
          <h1 className="text-3xl font-bold text-surface-900 mt-1">{c.legal_name}</h1>
          <div className="text-sm text-surface-700 mt-2 flex items-center gap-3 flex-wrap">
            <span>Market: <span className="text-surface-900 font-medium">{c.country}</span></span>
            <StatePill state={c.status} />
          </div>
        </div>
        <div className="flex gap-2">
          <button className="btn-primary" disabled={decide.isPending} onClick={() => decide.mutate("approve")}>Approve</button>
          <button className="btn-danger" disabled={decide.isPending} onClick={() => decide.mutate("reject")}>Reject</button>
          <button className="btn-secondary" disabled={decide.isPending} onClick={() => decide.mutate("request_info")}>Request Info</button>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <section className="card p-5">
            <h2 className="text-base font-semibold text-surface-900 mb-3">Submission Details</h2>
            <pre className="text-xs bg-surface-50 p-3 rounded-lg overflow-x-auto text-surface-800">
              {JSON.stringify(c.submission, null, 2)}
            </pre>
          </section>

          <section className="card p-5">
            <h2 className="text-base font-semibold text-surface-900 mb-3">Findings</h2>
            {c.findings?.length === 0 ? (
              <div className="text-surface-500 italic text-sm">No findings.</div>
            ) : (
              <ul className="space-y-3">
                {c.findings?.map((f: any, i: number) => (
                  <li key={i} className="border border-surface-200 rounded-lg p-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-mono text-xs font-semibold text-surface-900">{f.code}</span>
                      <span className={`chip text-[10px] ${f.severity >= 2 ? 'bg-danger-50 text-danger-700' : 'bg-warn-50 text-warn-700'}`}>
                        {f.severity_name}
                      </span>
                    </div>
                    <div className="text-sm text-surface-800">{f.message}</div>
                    {f.vendor_message && (
                      <div className="mt-2 text-xs text-surface-500 border-l-2 border-surface-300 pl-2">
                        Vendor note: {f.vendor_message}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card p-5">
            <h2 className="text-base font-semibold text-surface-900 mb-3">Checks Run</h2>
            <ul className="divide-y divide-surface-200 text-sm">
              {c.checks?.map((chk: any, i: number) => (
                <li key={i} className="py-2 flex justify-between items-center">
                  <span>{chk.check} <span className="text-surface-500 text-xs">({chk.duration_ms}ms)</span></span>
                  <span className="text-xs font-mono bg-surface-50 px-2 py-0.5 rounded border border-surface-200">{chk.label}</span>
                </li>
              ))}
            </ul>
          </section>
        </div>

        <div className="space-y-6">
          <section className="card p-5">
            <h2 className="text-base font-semibold text-surface-900 mb-2">Reviewer Notes</h2>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={4}
              placeholder="Add notes for decision..."
              className="input w-full text-sm"
            />
          </section>

          <section className="card p-5">
            <h2 className="text-base font-semibold text-surface-900 mb-3">Action History</h2>
            {c.actions?.length === 0 ? (
              <div className="text-surface-500 italic text-sm">No actions recorded.</div>
            ) : (
              <ul className="space-y-3">
                {c.actions?.map((a: any, i: number) => (
                  <li key={i} className="text-sm">
                    <div className="flex items-center justify-between text-surface-900 font-medium">
                      <span>{a.action}</span>
                      <span className="text-xs text-surface-500">{new Date(a.created_at).toLocaleString()}</span>
                    </div>
                    {a.note && <div className="text-xs text-surface-600 mt-1">{a.note}</div>}
                    <div className="text-[10px] text-surface-500 mt-1">{a.prev_status} &rarr; {a.new_status}</div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
""")
    
    # 4. Remove OpsIntelligence.tsx
    ops_intel = os.path.join(repo, "views", "OpsIntelligence.tsx")
    if os.path.exists(ops_intel):
        os.remove(ops_intel)

    # 5. Remove DemoConsole.tsx and DecisionCard.tsx since they're not fully compatible with Zamp or adapt App.tsx to remove them
    app_tsx = os.path.join(repo, "App.tsx")
    with open(app_tsx, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Update App.tsx routing
    import re
    # Remove imports for DemoConsole, DecisionCard, OpsIntelligence
    content = re.sub(r'import DemoConsole.*?\n', '', content)
    content = re.sub(r'import DecisionCard.*?\n', '', content)
    content = re.sub(r'import OpsIntelligence.*?\n', '', content)
    
    # Remove NavLinks
    content = re.sub(r'<NavLink to="/demo".*?>.*?<\/NavLink>\s*', '', content, flags=re.DOTALL)
    content = re.sub(r'<NavLink\s*to="/ops/intel".*?>.*?<\/NavLink>\s*', '', content, flags=re.DOTALL)
    
    # Remove Routes
    content = re.sub(r'<Route\s*path="/demo".*?<\/OpsGate>\s*\}?\s*\/>\s*', '', content, flags=re.DOTALL)
    content = re.sub(r'<Route\s*path="/ops/intel".*?<\/OpsGate>\s*\}?\s*\/>\s*', '', content, flags=re.DOTALL)
    content = re.sub(r'<Route\s*path="/decision-card.*?<\/OpsGate>\s*\}?\s*\/>\s*', '', content, flags=re.DOTALL)
    
    with open(app_tsx, "w", encoding="utf-8") as f:
        f.write(content)
        
    print("Files updated")

if __name__ == "__main__":
    main()
