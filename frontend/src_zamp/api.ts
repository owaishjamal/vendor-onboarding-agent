export type Status = "APPROVED" | "PENDING_INFO" | "PENDING_REVIEW" | "REJECTED";
export type SeverityName = "INFO" | "ADVISORY" | "NEEDS_INFO" | "NEEDS_REVIEW" | "REJECT";

export interface Finding {
  code: string;
  severity: number;
  severity_name: SeverityName;
  check: string;
  field: string | null;
  message: string;
  vendor_message: string | null;
  evidence: Record<string, any>;
}

export interface CheckResult {
  check: string;
  label: string;
  summary: string;
  findings: Finding[];
  duration_ms: number;
  data: Record<string, any>;
}

export interface CaseAction {
  action: string;
  reviewer: string | null;
  note: string | null;
  prev_status: string;
  new_status: string;
  created_at: string;
}

export interface ChangeSummary {
  prior_case: string;
  resolved: string[];
  new: string[];
  remaining: string[];
}

export interface Case {
  case_id: string;
  legal_name: string;
  trading_name: string | null;
  country: string;
  contact_email: string | null;
  status: string;                 // may be an automated Status or a reviewer-decided value
  reviewer_summary: string;
  vendor_email: string | null;
  created_at: string;
  completed_at: string | null;
  revision?: number;
  supersedes?: string | null;
  superseded_by?: string | null;
  resolution?: string | null;
  change_summary?: ChangeSummary | null;
  actions?: CaseAction[];
  submission?: Record<string, any>;
  checks?: CheckResult[];
  findings?: Finding[];
  finding_counts?: Record<string, number>;
  top_finding?: { code: string; message: string } | null;
}

export type ReviewerAction = "approve" | "reject" | "request_info" | "resolve" | "reopen";

export interface Sample {
  file: string;
  submission_id: string;
  legal_name: string;
  country: string;
  scenario: string;
  expected_status: Status;
}

export interface CheckPlan { check: string; label: string }

const j = async (r: Response) => {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
};

const customFetch = (url: string, options: RequestInit = {}) => {
  const headers = new Headers(options.headers || {});
  headers.set("X-API-Key", "dev_secret");
  return fetch(url, { ...options, headers });
};

export const api = {
  health: () => fetch("/health").then(j),
  policy: () => fetch("/v1/policy").then(j),
  countries: () => fetch("/v1/countries").then(j),
  samples: (): Promise<Sample[]> => customFetch("/v1/samples").then(j),
  sampleBody: (n: string) => customFetch(`/v1/samples/${encodeURIComponent(n)}`).then(j),
  cases: (): Promise<Case[]> => customFetch("/v1/cases").then(j),
  case: (id: string): Promise<Case> => customFetch(`/v1/cases/${id}`).then(j),
  stats: () => customFetch("/v1/stats").then(j),
  vendorMaster: () => customFetch("/v1/reference/vendor-master").then(j),
  deniedParties: () => customFetch("/v1/reference/denied-parties").then(j),
  overrides: () => customFetch("/v1/overrides").then(j),
  reset: () => customFetch("/v1/reset", { method: "POST" }).then(j),
  action: (id: string, action: ReviewerAction, note?: string) =>
    customFetch(`/v1/cases/${id}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, note, reviewer: "reviewer" }),
    }).then(j),
  profiles: () => fetch("/v1/profiles").then(j),
  profileTemplates: () => fetch("/v1/profile-templates").then(j),
  lookups: () => fetch("/v1/lookups").then(j),
  profile: (id: string, country = "") =>
    fetch(`/v1/profiles/${encodeURIComponent(id)}?country=${encodeURIComponent(country)}`).then(j),
  saveProfile: (id: string, body: any) =>
    fetch(`/v1/profiles/${encodeURIComponent(id)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),
  deleteProfile: (id: string) =>
    fetch(`/v1/profiles/${encodeURIComponent(id)}`, { method: "DELETE" }).then(j),
  vendorCase: (token: string) => fetch(`/v1/vendor/${encodeURIComponent(token)}`).then(j),
  preflight: (file: File, doc_type: string, country: string, legal_name: string) => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    fd.append("doc_type", doc_type);
    fd.append("country", country);
    fd.append("legal_name", legal_name);
    return fetch("/v1/documents/preflight", { method: "POST", body: fd }).then(j);
  },
};

export interface Preflight {
  status: string;
  level: "ok" | "warn" | "error";
  message: string;
  detected_type: string | null;
  filename: string;
}

export interface StreamHandlers {
  onPlan?: (p: CheckPlan[]) => void;
  onCheck?: (r: CheckResult) => void;
  onDone?: (c: Case) => void;
  onError?: (m: string) => void;
}

/** Read an SSE stream off a POST body. EventSource can't POST a payload. */
async function readSSE(res: Response, h: StreamHandlers): Promise<void> {
  if (!res.ok || !res.body) {
    let detail = `${res.status} ${res.statusText}`;
    try { const e = await res.json(); if (e?.detail) detail = e.detail; } catch { /* ignore */ }
    h.onError?.(`Request failed: ${detail}`);
    return;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";
    for (const frame of frames) {
      let event = "message";
      const lines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) lines.push(line.slice(5).trim());
      }
      if (!lines.length) continue;
      let p: any;
      try { p = JSON.parse(lines.join("\n")); } catch { continue; }
      if (event === "plan") h.onPlan?.(p);
      else if (event === "check") h.onCheck?.(p.result);
      else if (event === "done") h.onDone?.(p.case);
      else if (event === "error") h.onError?.(p.message);
    }
  }
}

export async function streamCase(
  body: { kind: "sample"; name: string } | { kind: "submission"; data: any },
  h: StreamHandlers,
): Promise<void> {
  const res = body.kind === "sample"
    ? await customFetch(`/v1/cases/sample/${encodeURIComponent(body.name)}/stream`, { method: "POST" })
    : await customFetch("/v1/cases/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body.data),
      });
  await readSSE(res, h);
}

/** Submit the vendor form: fields as JSON + real uploaded document files. */
export async function streamForm(
  submission: any, filesByName: Record<string, File>, h: StreamHandlers,
  endpoint = "/v1/cases/form/stream",
): Promise<void> {
  const fd = new FormData();
  fd.append("submission", JSON.stringify(submission));
  for (const f of Object.values(filesByName)) fd.append("files", f, f.name);
  const res = await fetch(endpoint, { method: "POST", body: fd });
  await readSSE(res, h);
}

// ---------------------------------------------------------------------------
// Presentation
// ---------------------------------------------------------------------------

export const STATUS_META: Record<Status, {
  label: string; cls: string; dot: string; blurb: string; who: string;
}> = {
  APPROVED: {
    label: "Approved",
    cls: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    dot: "bg-emerald-500",
    blurb: "All checks passed",
    who: "No action needed",
  },
  PENDING_INFO: {
    label: "Pending — vendor",
    cls: "bg-sky-50 text-sky-700 ring-sky-600/20",
    dot: "bg-sky-500",
    blurb: "Something is missing or malformed",
    who: "Waiting on the vendor — email drafted",
  },
  PENDING_REVIEW: {
    label: "Pending — internal review",
    cls: "bg-amber-50 text-amber-800 ring-amber-600/20",
    dot: "bg-amber-500",
    blurb: "Needs a human judgement call",
    who: "Waiting on us — do not contact the vendor yet",
  },
  REJECTED: {
    label: "Rejected",
    cls: "bg-rose-50 text-rose-700 ring-rose-600/20",
    dot: "bg-rose-500",
    blurb: "Cannot be onboarded",
    who: "Refer to compliance",
  },
};

// Statuses that only exist after a human acts. Mapped to a base look + a label.
const REVIEWER_STATUS: Record<string, { base: Status; label: string }> = {
  APPROVED_BY_REVIEWER: { base: "APPROVED", label: "Approved by reviewer" },
  REJECTED_BY_REVIEWER: { base: "REJECTED", label: "Rejected by reviewer" },
};

// A run that never reached a decision — the connection dropped mid-run, or a
// check hit an unrecoverable error. Not a verdict, so it gets its own look
// rather than borrowing "rejected" and implying we judged the vendor.
const ERROR_META = {
  label: "Interrupted",
  cls: "bg-slate-100 text-slate-700 ring-slate-400/30",
  dot: "bg-slate-500",
  blurb: "The run stopped before a decision",
  who: "Re-submit to run the checks again",
};

export function statusMeta(status: string) {
  if (status in STATUS_META) return STATUS_META[status as Status];
  const r = REVIEWER_STATUS[status];
  if (r) return { ...STATUS_META[r.base], label: r.label };
  if (status === "ERROR") return ERROR_META;
  return { label: status, cls: "bg-slate-100 text-slate-600 ring-slate-300",
           dot: "bg-slate-400", blurb: "", who: "" };
}

export const SEVERITY_META: Record<SeverityName, { label: string; cls: string; bar: string }> = {
  INFO: { label: "Info", cls: "bg-slate-100 text-slate-600 ring-slate-300", bar: "bg-slate-300" },
  ADVISORY: { label: "Advisory", cls: "bg-slate-100 text-slate-700 ring-slate-300", bar: "bg-slate-400" },
  NEEDS_INFO: { label: "Ask vendor", cls: "bg-sky-50 text-sky-700 ring-sky-300", bar: "bg-sky-500" },
  NEEDS_REVIEW: { label: "Needs review", cls: "bg-amber-50 text-amber-800 ring-amber-300", bar: "bg-amber-500" },
  REJECT: { label: "Reject", cls: "bg-rose-50 text-rose-700 ring-rose-300", bar: "bg-rose-500" },
};

// Map the int severity to a name, so a finding that (for any reason) arrives
// without severity_name still resolves instead of crashing the render.
const SEVERITY_BY_INT: Record<number, SeverityName> = {
  0: "INFO", 1: "ADVISORY", 2: "NEEDS_INFO", 3: "NEEDS_REVIEW", 4: "REJECT",
};

export function sevMeta(f: { severity_name?: string; severity?: number }) {
  const name = (f.severity_name as SeverityName) ?? SEVERITY_BY_INT[f.severity ?? 0];
  return SEVERITY_META[name] ?? SEVERITY_META.INFO;
}

export function sevName(f: { severity_name?: string; severity?: number }): SeverityName {
  return (f.severity_name as SeverityName) ?? SEVERITY_BY_INT[f.severity ?? 0] ?? "INFO";
}

export const shortTime = (iso?: string | null) =>
  iso ? new Date(iso + "Z").toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  }) : "—";

/** Compact relative age, e.g. "3d", "5h", "just now". */
export const ageOf = (iso?: string | null): string => {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso + "Z").getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
};

export const ageDays = (iso?: string | null): number =>
  iso ? (Date.now() - new Date(iso + "Z").getTime()) / 86400000 : 0;

export const flag = (cc: string) =>
  ({ US: "United States", GB: "United Kingdom", DE: "Germany",
     IN: "India", SG: "Singapore" } as Record<string, string>)[cc] ?? cc;
