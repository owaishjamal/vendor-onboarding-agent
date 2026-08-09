import { useQuery } from "@tanstack/react-query";
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
          <div className="text-xs uppercase tracking-[0.18em] text-black font-semibold">
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
          <span className="chip bg-warm-off-white border-warm-cream-border text-black">live · 3s poll</span>
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
                className="border-t border-surface-200 hover:bg-warm-off-white/40 transition cursor-pointer"
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
