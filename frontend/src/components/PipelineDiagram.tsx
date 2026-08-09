/**
 * Visual reflection of the real orchestrator FSM in
 * backend/app/orchestrator/fsm.py — one stage per chip.
 *
 * Stages (in order):
 *   1. Intake               <- INTAKE, READY_TO_DISPATCH
 *   2. Verify (DVA)         <- DVA_MDA_RUNNING, DVA_DONE
 *   3. Digitize (MDA)       <- DVA_MDA_RUNNING, MDA_DONE
 *   4. Decide               <- BOTH_DONE
 *   5a. Provision           <- AUTO_APPROVED, APPROVED_BY_REVIEWER, PROVISIONING, PROVISIONED
 *   5b. Reviewer Queue      <- PENDING_REVIEW (and REJECTED branches off here)
 *
 * The diagram is driven by `latestState` (the most recent STATE_TRANSITION
 * event from the SSE stream the Activity Log already consumes). Stages before
 * `latestState` are marked complete, the matching stage is active, and stages
 * after are idle.
 */

export type PipelineStageId =
  | "intake"
  | "verify"
  | "digitize"
  | "decide"
  | "provision"
  | "review";

const STAGE_ORDER: PipelineStageId[] = [
  "intake",
  "verify",
  "digitize",
  "decide",
  "provision",
];

const STAGE_META: Record<
  PipelineStageId,
  { title: string; agents: string }
> = {
  intake: { title: "Intake", agents: "Form & artifacts" },
  verify: { title: "DVA — Verify", agents: "Classifier · Registry · Sanctions" },
  digitize: { title: "MDA — Digitize", agents: "Vision · Reasoner · Validator" },
  decide: { title: "Decide", agents: "Composer · Rules" },
  provision: { title: "Provision", agents: "Trust Score · GXS receipt" },
  review: { title: "Reviewer", agents: "Human-in-the-loop" },
};

const STATE_TO_STAGE: Record<string, PipelineStageId> = {
  INTAKE: "intake",
  READY_TO_DISPATCH: "intake",
  DVA_MDA_RUNNING: "verify",
  DVA_DONE: "verify",
  MDA_DONE: "digitize",
  BOTH_DONE: "decide",
  AUTO_APPROVED: "provision",
  APPROVED_BY_REVIEWER: "provision",
  PROVISIONING: "provision",
  PROVISIONED: "provision",
  PENDING_REVIEW: "review",
  REJECTED: "review",
};

type StageState = "idle" | "active" | "complete";

function computeStageStates(
  latestState: string | undefined,
): Record<PipelineStageId, StageState> {
  const stageStates: Record<PipelineStageId, StageState> = {
    intake: "idle",
    verify: "idle",
    digitize: "idle",
    decide: "idle",
    provision: "idle",
    review: "idle",
  };
  if (!latestState) return stageStates;

  const stage = STATE_TO_STAGE[latestState];
  if (!stage) return stageStates;

  if (stage === "review") {
    stageStates.intake = "complete";
    stageStates.verify = "complete";
    stageStates.digitize = "complete";
    stageStates.decide = "complete";
    stageStates.review = "active";
    return stageStates;
  }

  if (stage === "provision" && latestState === "PROVISIONED") {
    for (const s of STAGE_ORDER) stageStates[s] = "complete";
    return stageStates;
  }

  const idx = STAGE_ORDER.indexOf(stage);
  for (let i = 0; i < STAGE_ORDER.length; i++) {
    if (i < idx) stageStates[STAGE_ORDER[i]] = "complete";
    else if (i === idx) stageStates[STAGE_ORDER[i]] = "active";
    else stageStates[STAGE_ORDER[i]] = "idle";
  }
  return stageStates;
}

export function PipelineDiagram({ latestState }: { latestState?: string }) {
  const states = computeStageStates(latestState);
  const showReviewBranch = states.review !== "idle";
  return (
    <div className="card p-5">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-surface-900">How it works</h3>
          <p className="text-xs text-surface-500 mt-0.5">
            The pipeline below mirrors the orchestrator FSM. Each event in the log corresponds to a stage here.
          </p>
        </div>
        <div className="text-[11px] font-mono text-surface-500 hidden sm:flex items-center gap-3">
          <LegendDot tone="active" label="active" />
          <LegendDot tone="complete" label="done" />
          <LegendDot tone="idle" label="idle" />
        </div>
      </div>

      <div className="mt-4 flex items-stretch gap-2 overflow-x-auto pb-1">
        {STAGE_ORDER.map((id, i) => (
          <div key={id} className="flex items-stretch gap-2 flex-1 min-w-0">
            <StageChip
              id={id}
              meta={STAGE_META[id]}
              state={states[id]}
              index={i + 1}
            />
            {i < STAGE_ORDER.length - 1 && (
              <Arrow done={states[id] === "complete"} />
            )}
          </div>
        ))}
      </div>

      {showReviewBranch && (
        <div className="mt-3 flex items-center gap-2">
          <div className="text-[11px] text-surface-500 font-mono pl-1">
            branch from Decide ↘
          </div>
          <StageChip id="review" meta={STAGE_META.review} state={states.review} index={null} />
        </div>
      )}
    </div>
  );
}

function StageChip({
  id,
  meta,
  state,
  index,
}: {
  id: PipelineStageId;
  meta: { title: string; agents: string };
  state: StageState;
  index: number | null;
}) {
  const cls =
    state === "active"
      ? "border-black bg-warm-off-white ring-2 ring-black/20"
      : state === "complete"
      ? "border-warm-cream-border bg-white"
      : "border-surface-200 bg-surface-50";
  const numCls =
    state === "active"
      ? "bg-black text-white"
      : state === "complete"
      ? "bg-surface-100 text-black"
      : "bg-surface-200 text-surface-500";
  const titleCls =
    state === "idle" ? "text-surface-500" : "text-surface-900";
  const agentsCls =
    state === "idle" ? "text-surface-500" : "text-surface-700";
  return (
    <div
      data-stage={id}
      className={
        "shrink-0 min-w-[150px] flex-1 rounded-2xl border px-3 py-2.5 transition " +
        cls
      }
    >
      <div className="flex items-center gap-2">
        <span
          className={
            "w-5 h-5 rounded-full text-[10px] font-semibold inline-flex items-center justify-center " +
            numCls
          }
        >
          {state === "complete" ? "✓" : index ?? "•"}
        </span>
        <span className={"text-sm font-semibold leading-none " + titleCls}>{meta.title}</span>
        {state === "active" && (
          <span className="ml-auto inline-block w-1.5 h-1.5 rounded-full bg-black animate-pulse" />
        )}
      </div>
      <div className={"text-[11px] mt-1 " + agentsCls}>{meta.agents}</div>
    </div>
  );
}

function Arrow({ done }: { done: boolean }) {
  return (
    <div className="flex items-center" aria-hidden="true">
      <svg width="18" height="14" viewBox="0 0 18 14" fill="none">
        <path
          d="M1 7 H14 M10 3 L14 7 L10 11"
          stroke={done ? "#000000" : "#d8d4d1"}
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          fill="none"
        />
      </svg>
    </div>
  );
}

function LegendDot({
  tone,
  label,
}: {
  tone: "active" | "complete" | "idle";
  label: string;
}) {
  const dot =
    tone === "active"
      ? "bg-black"
      : tone === "complete"
      ? "bg-surface-100 border border-black"
      : "bg-surface-200";
  return (
    <span className="inline-flex items-center gap-1">
      <span className={"inline-block w-2 h-2 rounded-full " + dot} />
      {label}
    </span>
  );
}
