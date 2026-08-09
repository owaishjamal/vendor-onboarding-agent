type Tone = "brand" | "warn" | "danger" | "surface" | "accent";

const TONE_CLASSES: Record<Tone, string> = {
  brand: "bg-warm-off-white text-black border-warm-cream-border",
  warn: "bg-warn-50 text-warn-700 border-warn-100",
  danger: "bg-danger-50 text-danger-700 border-danger-100",
  surface: "bg-surface-100 text-surface-700 border-surface-200",
  accent: "bg-surface-900 text-white border-surface-900",
};

const TONE_DOT: Record<Tone, string> = {
  brand: "bg-black",
  warn: "bg-warn-500",
  danger: "bg-danger-500",
  surface: "bg-surface-300",
  accent: "bg-white",
};

function toneForState(state: string): Tone {
  const s = state.toUpperCase();
  if (
    s === "PROVISIONED" ||
    s === "AUTO_APPROVED" ||
    s === "APPROVED_BY_REVIEWER"
  )
    return "brand";
  if (s === "REJECTED") return "danger";
  if (s === "PENDING_REVIEW") return "warn";
  return "surface";
}

export function StatePill({ state }: { state: string | null | undefined }) {
  if (!state) return null;
  const tone = toneForState(state);
  return (
    <span className={`chip font-mono ${TONE_CLASSES[tone]}`}>
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${TONE_DOT[tone]}`} />
      {state}
    </span>
  );
}

export function DecisionPill({ decision }: { decision: string | null | undefined }) {
  if (!decision) return <span className="text-surface-500 text-sm">—</span>;
  const tone: Tone =
    decision === "Auto_Approved" || decision === "Approved"
      ? "brand"
      : decision === "Rejected"
      ? "danger"
      : "warn";
  return (
    <span className={`chip ${TONE_CLASSES[tone]}`}>
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${TONE_DOT[tone]}`} />
      {decision}
    </span>
  );
}
