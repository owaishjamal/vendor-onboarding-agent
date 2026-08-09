import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";

const TERMINAL_STATES = new Set([
  "PROVISIONED",
  "REJECTED",
  "AUTO_APPROVED",
  "PENDING_REVIEW",
]);

export default function Status() {
  const { caseId } = useParams<{ caseId: string }>();

  const caseQ = useQuery({
    queryKey: ["case", caseId],
    queryFn: () => api.getCase(caseId!),
    enabled: !!caseId,
    refetchInterval: (q) => {
      const data = q.state.data as any;
      if (!data) return 1500;
      return TERMINAL_STATES.has(data.state) ? false : 1500;
    },
  });

  const summaryQ = useQuery({
    queryKey: ["reviewer-summary", caseId],
    queryFn: () => api.getCase(caseId!),
    enabled:
      !!caseId &&
      !!caseQ.data &&
      TERMINAL_STATES.has((caseQ.data as any).state),
  });

  if (!caseId) {
    return (
      <div className="mx-auto max-w-xl py-12 text-center text-surface-600">
        No case id in URL.
      </div>
    );
  }
  if (caseQ.isLoading) {
    return (
      <div className="mx-auto max-w-xl py-12 text-center text-surface-600">
        Loading case…
      </div>
    );
  }
  if (caseQ.error || !caseQ.data) {
    return (
      <div className="mx-auto max-w-xl py-12">
        <div className="card p-6 border-danger-100">
          <p className="text-danger-700 text-sm">
            Could not load case: {String(caseQ.error)}
          </p>
        </div>
      </div>
    );
  }

  const data: any = caseQ.data;
  const tier: string | null = data.decision_tier ?? null;
  const score: number | null = data.confidence_score ?? null;
  const isTerminal = TERMINAL_STATES.has(data.state);
  const summary = summaryQ.data ?? null;

  return (
    <div className="mx-auto max-w-3xl py-10 space-y-6">
      <header className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-black">
          Onboarding status
        </p>
        <h1 className="text-3xl font-bold text-surface-900">
          {data.declared_business_name}
        </h1>
        <p className="text-sm text-surface-600">
          Case <span className="font-mono">{data.case_id}</span> · market{" "}
          {data.declared_market} · state {" "}
          <span className="font-mono">{data.state}</span>
        </p>
      </header>

      {!isTerminal && (
        <RunningCard state={data.state} ledger={data.ledger ?? []} />
      )}

      {isTerminal && tier === "auto_accept" && (
        <SuccessCard
          score={score}
          vendorMessage={summary?.reviewer_summary ?? null}
        />
      )}
      {isTerminal && tier === "priority_review" && (
        <PriorityReviewCard
          vendorMessage={summary?.reviewer_summary ?? null}
          reasons={data.review_queue?.reasons ?? []}
        />
      )}
      {isTerminal && tier === "rejected_on_face" && (
        <RejectCard
          vendorMessage={summary?.reviewer_summary ?? null}
          reasons={data.review_queue?.reasons ?? []}
        />
      )}
      {isTerminal && !tier && (
        <PriorityReviewCard
          vendorMessage={summary?.reviewer_summary ?? null}
          reasons={data.review_queue?.reasons ?? []}
        />
      )}

      <div className="text-xs text-surface-500 flex items-center gap-3">
        <span>Need to re-submit?</span>
        <Link to="/m/onboard" className="font-medium text-black">
          Restart wizard
        </Link>
      </div>
    </div>
  );
}

function RunningCard({
  state,
  ledger = [],
}: {
  state: string;
  ledger?: { step?: string; started_at?: string; model_id?: string }[];
}) {
  const lastStep = ledger.length ? ledger[ledger.length - 1]?.step : null;
  const judgeDone = ledger.some((r) => r.step === "judge");
  const bothDonePhase =
    state === "BOTH_DONE" || (lastStep === "risk_arbiter" && !judgeDone);
  return (
    <div className="card p-6 border-warm-cream-border bg-warm-off-white/40">
      <div className="flex items-center gap-3">
        <span className="inline-block w-2 h-2 rounded-full bg-black animate-pulse" />
        <span className="text-sm font-semibold text-black">
          {bothDonePhase
            ? "Final compliance review (almost done)"
            : state === "DVA_MDA_RUNNING"
              ? "Vision agents are reading your uploads"
              : "Our agents are reviewing your documents"}
        </span>
      </div>
      <p className="mt-3 text-sm text-surface-700">
        {bothDonePhase
          ? "DVA and menu checks are done. The Judge is running on the verification pipeline — often 1–3 minutes. This page refreshes automatically."
          : state === "DVA_MDA_RUNNING"
            ? "Each file is classified and extracted. This is usually the longest phase."
            : "Classifying documents, extracting fields, and checking your menu."}
      </p>
      <p className="mt-2 text-xs text-surface-600">
        State: <span className="font-mono">{state}</span>
        {lastStep && (
          <>
            {" "}
            · last step: <span className="font-mono">{lastStep}</span>
            {judgeDone ? " ✓" : ""}
          </>
        )}
      </p>
      {ledger.length > 0 && (
        <details className="mt-3 text-xs text-surface-600">
          <summary className="cursor-pointer font-medium text-black">
            Agent steps ({ledger.length})
          </summary>
          <ul className="mt-2 space-y-0.5 font-mono max-h-32 overflow-y-auto">
            {ledger.slice(-8).map((row, i) => (
              <li key={`${row.started_at}-${row.step}-${i}`}>
                {row.step}
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="mt-3 text-xs text-surface-500">
        Logs: <code className="bg-white/80 px-1 rounded">tail -f .run/backend.log</code>
      </p>
    </div>
  );
}

function SuccessCard({
  score,
  vendorMessage,
}: {
  score: number | null;
  vendorMessage: string | null;
}) {
  return (
    <div className="card p-6 border-warm-cream-border bg-warm-off-white/60">
      <h2 className="text-xl font-bold text-black">
        You are approved ✓
      </h2>
      <p className="mt-2 text-sm text-surface-700">
        Your documents passed our checks
        {score !== null && (
          <>
            {" "}with a confidence of{" "}
            <span className="font-mono font-semibold text-black">
              {score.toFixed(2)}
            </span>
          </>
        )}
        . Your storefront is being provisioned now — expect it to appear in
        your vendor dashboard within a few minutes.
      </p>
      {vendorMessage && (
        <div className="mt-4 rounded-xl border border-warm-cream-border bg-white px-4 py-3 text-sm text-surface-700">
          {vendorMessage}
        </div>
      )}
      <p className="mt-3 text-xs text-surface-500">
        Note: every auto-accepted case is queued for an ops spot-check. You'll
        only hear from us if something unexpected comes up.
      </p>
    </div>
  );
}

function PriorityReviewCard({
  vendorMessage,
  reasons,
}: {
  vendorMessage: string | null;
  reasons: string[];
}) {
  return (
    <div className="card p-6 border-warn-100 bg-warn-50/60">
      <h2 className="text-xl font-bold text-warn-700">
        We're double-checking a few things
      </h2>
      <p className="mt-2 text-sm text-surface-700">
        Our agents flagged your case for a fast manual review. Onboarding is
        paused until someone on our ops team takes a look — usually within a
        few hours.
      </p>
      {vendorMessage && (
        <div className="mt-4 rounded-xl border border-warn-100 bg-white px-4 py-3 text-sm text-surface-700">
          {vendorMessage}
        </div>
      )}
      {reasons.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {reasons.map((r) => (
            <span
              key={r}
              className="chip bg-warn-50 border-warn-100 text-warn-700 font-mono"
            >
              {r}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function RejectCard({
  vendorMessage,
  reasons,
}: {
  vendorMessage: string | null;
  reasons: string[];
}) {
  return (
    <div className="card p-6 border-danger-100 bg-danger-50/60">
      <h2 className="text-xl font-bold text-danger-700">
        We can't proceed right now
      </h2>
      <p className="mt-2 text-sm text-surface-700">
        Based on the documents you uploaded, we can't onboard you at this time.
        See the notes below; if you think this is a mistake, please contact
        our procurement team.
      </p>
      {vendorMessage && (
        <div className="mt-4 rounded-xl border border-danger-100 bg-white px-4 py-3 text-sm text-surface-700">
          {vendorMessage}
        </div>
      )}
      {reasons.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {reasons.map((r) => (
            <span
              key={r}
              className="chip bg-danger-50 border-danger-100 text-danger-700 font-mono"
            >
              {r}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
