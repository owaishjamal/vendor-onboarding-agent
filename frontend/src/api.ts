/** Thin API client over the Zamp FastAPI backend.
 *
 * Everything here hits the real backend. There are deliberately no stubbed
 * responses: a submission that returns a fake case id looks identical to a
 * working one in the UI, right up until someone asks to see the case.
 */

export type PreflightResult = {
  verdict: string | null;
  detected_type: string | null;
  message: string;
  confidence: number | null;
  findings?: any[];
};

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
  confidence?: any;
  vendor_email?: string | null;
  revision?: number;
  superseded_by?: string | null;
};

export type CaseDetail = Case & {
  submission: any;
  change_summary: any;
  actions: any[];
  checks: CheckResult[];
  findings: Finding[];
  vendor_token?: string;
};

export type Finding = {
  code: string;
  severity: number;
  severity_name: SeverityName;
  check: string;
  field: string | null;
  message: string;
  vendor_message: string | null;
  evidence: Record<string, any>;
};

export type CheckResult = {
  check: string;
  label: string;
  kind?: CheckKind;
  summary: string;
  duration_ms: number;
  data: Record<string, any>;
  findings?: Finding[];
};

export type CheckKind = "deterministic" | "ai";

export type SeverityName =
  | "INFO" | "ADVISORY" | "CONDITION" | "NEEDS_INFO" | "NEEDS_REVIEW" | "REJECT";

export type VendorCategory = {
  id: string;
  label: string;
  blurb: string;
  extra_fields: number;
  extra_documents: number;
};

/** A one-click demonstrable case. `expect` is a prediction the test suite
 *  enforces against the real pipeline, not a scripted outcome. */
export type Scenario = {
  id: string;
  kind: "happy" | "edge";
  label: string;
  blurb: string;
  expect: string;
  expect_why: string;
  teaches: string;
  category: string;
};

export type ScenarioDetail = Scenario & {
  form: Record<string, any>;
  bank: Record<string, string>;
  custom_fields: Record<string, any>;
  documents: { doc_type: string; filename: string; extracted: Record<string, any> }[];
  payload: Record<string, any>;
};

export type ResolvedRequirement = {
  key: string;
  label: string;
  declared: "required" | "conditional" | "optional" | "na";
  effective: "required" | "optional" | "na";
  applies: boolean;
  when: string | null;
  when_explained: string;
  why: string | null;
};

export type Requirements = {
  profile_id: string;
  profile_name: string;
  fields: any[];
  documents: any[];
  resolved: { fields: ResolvedRequirement[]; documents: ResolvedRequirement[] };
};

const BASE = import.meta.env.VITE_API_BASE ?? "";

// The backend guards write and reporting endpoints with X-API-Key. In a real
// deployment this comes from a session, not a build-time constant — but a
// header that is actually sent beats an auth scheme the UI silently ignores.
const API_KEY = import.meta.env.VITE_API_KEY ?? "dev_secret";

function headers(extra: Record<string, string> = {}): Record<string, string> {
  return { "X-API-Key": API_KEY, ...extra };
}

async function asJson<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* keep the status line */
    }
    throw new Error(detail);
  }
  return r.json();
}

const credentials: RequestCredentials = "include";

/** Parse an SSE body, invoking `onEvent` per event. Resolves when the stream ends. */
export async function consumeStream(
  res: Response,
  onEvent: (type: string, data: any) => void,
): Promise<void> {
  if (!res.ok || !res.body) {
    throw new Error(`stream failed: ${res.status} ${res.statusText}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Events are separated by a blank line.
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      let event = "message";
      const dataLines: string[] = [];
      for (const line of part.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        // lines starting with ':' are keep-alive comments — ignore
      }
      if (!dataLines.length) continue;
      try {
        onEvent(event, JSON.parse(dataLines.join("\n")));
      } catch {
        /* a malformed frame must not kill the run */
      }
    }
  }
}

export const api = {
  // --- meta -----------------------------------------------------------
  health: () => fetch(`${BASE}/health`, { credentials }).then((r) => asJson<any>(r)),

  categories: () =>
    fetch(`${BASE}/v1/categories`, { credentials }).then((r) =>
      asJson<VendorCategory[]>(r)),

  countries: () =>
    fetch(`${BASE}/v1/countries`, { credentials }).then((r) => asJson<any[]>(r)),

  checks: () =>
    fetch(`${BASE}/v1/checks`, { credentials }).then((r) =>
      asJson<{ check: string; label: string; kind: CheckKind }[]>(r)),

  /** What this vendor must supply, given country + category. */
  scenarios: () =>
    fetch(`${BASE}/v1/scenarios`, { credentials }).then((r) =>
      asJson<Scenario[]>(r)),

  scenario: (id: string) =>
    fetch(`${BASE}/v1/scenarios/${encodeURIComponent(id)}`, { credentials })
      .then((r) => asJson<ScenarioDetail>(r)),

  requirements: (country: string, category: string, profileId = "") =>
    fetch(
      `${BASE}/v1/requirements?country=${encodeURIComponent(country)}` +
        `&category=${encodeURIComponent(category)}` +
        `&profile_id=${encodeURIComponent(profileId)}`,
      { credentials },
    ).then((r) => asJson<Requirements>(r)),

  /** Re-resolve conditional requirements against a part-filled form. */
  previewRequirements: (partial: any) =>
    fetch(`${BASE}/v1/requirements/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(partial),
      credentials,
    }).then((r) => asJson<{ resolved: Requirements["resolved"] }>(r)),

  // --- intake ---------------------------------------------------------
  preflightDocument: (file: File, doc_type: string, country?: string, legal_name?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("doc_type", doc_type);
    if (country) fd.append("country", country);
    if (legal_name) fd.append("legal_name", legal_name);
    return fetch(`${BASE}/v1/documents/preflight`, {
      method: "POST", body: fd, credentials,
    }).then((r) => asJson<PreflightResult>(r));
  },

  /** Submit the form with real files. Returns the raw SSE response. */
  submitForm: (submission: any, files: File[]) => {
    const fd = new FormData();
    fd.append("submission", JSON.stringify(submission));
    files.forEach((f) => fd.append("files", f));
    return fetch(`${BASE}/v1/cases/form/stream`, {
      method: "POST", body: fd, headers: headers(), credentials,
    });
  },

  /** Submit a JSON-only submission (no attachments). Returns the SSE response. */
  submitJson: (submission: any) =>
    fetch(`${BASE}/v1/cases/stream`, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(submission),
      credentials,
    }),

  samples: () =>
    fetch(`${BASE}/v1/samples`, { headers: headers(), credentials })
      .then((r) => asJson<any[]>(r)),

  // --- cases ----------------------------------------------------------
  listCases: () =>
    fetch(`${BASE}/v1/cases`, { headers: headers(), credentials })
      .then((r) => asJson<Case[]>(r)),

  getCase: (id: string) =>
    fetch(`${BASE}/v1/cases/${id}`, { headers: headers(), credentials })
      .then((r) => asJson<CaseDetail>(r)),

  /** Cases belonging to the signed-in vendor.
   *
   * There is no per-vendor endpoint on this backend — a vendor's real access
   * path is their tokened portal link, not a list. The shell only uses this to
   * show "your current case", so it reads the same list and fails soft: a
   * sidebar that cannot load must never take the page down with it.
   */
  listMyCases: () =>
    fetch(`${BASE}/v1/cases`, { headers: headers(), credentials })
      .then((r) => asJson<Case[]>(r))
      .catch(() => [] as Case[]),

  stats: () =>
    fetch(`${BASE}/v1/stats`, { headers: headers(), credentials })
      .then((r) => asJson<any>(r)),

  decide: (id: string, body: { action: string; reviewer?: string; note?: string }) =>
    fetch(`${BASE}/v1/cases/${id}/action`, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
      credentials,
    }).then((r) => asJson<any>(r)),

  /** Ops copilot. `source` says whether the answer came from the record or a model. */
  chat: (id: string, messages: { role: string; content: string }[]) =>
    fetch(`${BASE}/v1/cases/${id}/chat`, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ messages }),
      credentials,
    }).then((r) =>
      asJson<{ reply: string; source: string; grounded_in: string }>(r)),

  // --- vendor portal (token is the credential) -------------------------
  vendorCase: (token: string) =>
    fetch(`${BASE}/v1/vendor/${token}`).then((r) => asJson<any>(r)),

  // --- demo role switch ------------------------------------------------
  // NOT authentication. It picks which UI you see, nothing more, and the
  // backend does not trust it. Real deployments put SSO in front of the ops
  // routes; the API key on write endpoints is the actual control today. This
  // is called out in the docs rather than dressed up as a login.
  me: async (): Promise<Me | null> => {
    const role = localStorage.getItem("role");
    if (!role) return null;
    const country = localStorage.getItem("country") || "IN";
    return {
      user_id: "demo", role: role as Me["role"],
      email: role === "ops" ? "ops@zamp.demo" : "vendor@zamp.demo",
      business_name: localStorage.getItem("business_name"),
      country, market: country,
    };
  },
  // The callers pass whole form objects, password included. Narrowing the
  // parameter type to only the fields this shim happens to read broke the
  // build — the shape has to match what the forms actually send.
  signup: async (body: {
    email?: string; password?: string;
    business_name?: string; country?: string; market?: string;
  }) => {
    localStorage.setItem("role", "vendor");
    if (body.business_name) localStorage.setItem("business_name", body.business_name);
    const country = body.country ?? body.market;
    if (country) localStorage.setItem("country", country);
    return (await api.me())!;
  },
  login: async (body: { email: string; password?: string }) => {
    localStorage.setItem("role", body.email.includes("ops") ? "ops" : "vendor");
    return (await api.me())!;
  },
  logout: async () => {
    localStorage.removeItem("role");
  },
};

export type Me = {
  user_id: string;
  email: string;
  role: "vendor" | "ops";
  business_name: string | null;
  country: string;
  /** Alias for `country`. The shell renders it as "Market"; kept so both
   *  names resolve rather than making every caller pick one. */
  market: string;
};

// ---------------------------------------------------------------------------
// Presentation helpers
// ---------------------------------------------------------------------------

export const STATUS_META: Record<string, {
  label: string; cls: string; dot: string; blurb: string; who: string;
}> = {
  APPROVED: {
    label: "Approved",
    cls: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    dot: "bg-emerald-500",
    blurb: "All checks passed",
    who: "No action needed",
  },
  APPROVED_WITH_CONDITIONS: {
    label: "Approved — with conditions",
    cls: "bg-teal-50 text-teal-700 ring-teal-600/20",
    dot: "bg-teal-500",
    blurb: "Onboarded, with items to resolve",
    who: "Track the conditions to closure",
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
    blurb: "Needs a human judgement",
    who: "Waiting on us",
  },
  REJECTED: {
    label: "Rejected",
    cls: "bg-rose-50 text-rose-700 ring-rose-600/20",
    dot: "bg-rose-500",
    blurb: "Cannot be onboarded",
    who: "Refer to compliance",
  },
  ERROR: {
    label: "Interrupted",
    cls: "bg-slate-100 text-slate-700 ring-slate-400/30",
    dot: "bg-slate-500",
    blurb: "The run stopped before a decision",
    who: "Re-submit to run the checks again",
  },
};

export function statusMeta(status: string | null | undefined) {
  if (status && status in STATUS_META) return STATUS_META[status];
  const base = (status || "").replace("_BY_REVIEWER", "");
  if (base in STATUS_META) {
    return { ...STATUS_META[base], label: `${STATUS_META[base].label} (by reviewer)` };
  }
  return {
    label: status || "Running",
    cls: "bg-slate-100 text-slate-600 ring-slate-300",
    dot: "bg-slate-400", blurb: "", who: "",
  };
}

export const SEVERITY_META: Record<SeverityName, { label: string; cls: string }> = {
  INFO: { label: "Info", cls: "bg-slate-100 text-slate-600 ring-slate-300" },
  ADVISORY: { label: "Advisory", cls: "bg-slate-100 text-slate-700 ring-slate-300" },
  CONDITION: { label: "Condition", cls: "bg-teal-50 text-teal-700 ring-teal-300" },
  NEEDS_INFO: { label: "Ask vendor", cls: "bg-sky-50 text-sky-700 ring-sky-300" },
  NEEDS_REVIEW: { label: "Needs review", cls: "bg-amber-50 text-amber-800 ring-amber-300" },
  REJECT: { label: "Reject", cls: "bg-rose-50 text-rose-700 ring-rose-300" },
};

/** Severity lookup that tolerates an int-only finding from the live stream. */
export function sevName(f: { severity_name?: string; severity?: number }): SeverityName {
  if (f.severity_name && f.severity_name in SEVERITY_META) {
    return f.severity_name as SeverityName;
  }
  const byInt: SeverityName[] =
    ["INFO", "ADVISORY", "CONDITION", "NEEDS_INFO", "NEEDS_REVIEW", "REJECT"];
  return byInt[f.severity ?? 0] ?? "INFO";
}

export function sevMeta(f: { severity_name?: string; severity?: number }) {
  return SEVERITY_META[sevName(f)];
}

export const CHECK_KIND_META: Record<CheckKind, { label: string; cls: string; blurb: string }> = {
  deterministic: {
    label: "Rule",
    cls: "bg-indigo-50 text-indigo-700 ring-indigo-300",
    blurb: "Checksum, format rule or registry lookup — same answer every time",
  },
  ai: {
    label: "AI",
    cls: "bg-violet-50 text-violet-700 ring-violet-300",
    blurb: "Model judgement over unstructured content — carries confidence",
  },
};

export function flag(country: string): string {
  if (!country || country.length !== 2) return "";
  return String.fromCodePoint(
    ...country.toUpperCase().split("").map((c) => 127397 + c.charCodeAt(0)));
}

export function shortTime(iso?: string | null): string {
  if (!iso) return "";
  return iso.slice(11, 16);
}

export function ageDays(iso?: string | null): number {
  if (!iso) return 0;
  return (Date.now() - new Date(iso).getTime()) / 86_400_000;
}

export function ageOf(iso?: string | null): string {
  const d = ageDays(iso);
  if (d < 1 / 24) return "just now";
  if (d < 1) return `${Math.floor(d * 24)}h ago`;
  return `${Math.floor(d)}d ago`;
}
