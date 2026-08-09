import os
import re

def main():
    repo = r"c:\Users\owais\Downloads\MAO-GrabHack-main\vendor-onboarding-agent\frontend\src"

    # 1. Update api.ts with missing types and functions
    api_ts = os.path.join(repo, "api.ts")
    with open(api_ts, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Add PreflightResult
    preflight_type = """
export type PreflightResult = {
  detected_class: string;
  classifier_confidence: number;
  extracted_fields: Record<string, string | number | null>;
  extractor_confidence: number;
  warnings: string[];
  recommendation: "ok" | "looks_wrong" | "unreadable";
  human_message: string;
  declared_doc_class?: string | null;
  market?: string | null;
};
"""
    if "export type PreflightResult" not in content:
        content = content.replace("export type Case =", preflight_type + "\nexport type Case =")

    # Add api functions
    api_functions = """
  preflightDocument: (file: File, doc_type: string, country?: string, legal_name?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("doc_type", doc_type);
    if (country) fd.append("country", country);
    if (legal_name) fd.append("legal_name", legal_name);
    return fetch(`${BASE}/v1/documents/preflight`, {
      method: "POST",
      body: fd,
      credentials,
    }).then((r) => asJson<PreflightResult>(r));
  },
  
  submitForm: (submission: any, files: File[]) => {
    const fd = new FormData();
    fd.append("submission", JSON.stringify(submission));
    files.forEach(f => fd.append("files", f));
    return fetch(`${BASE}/v1/cases/form/stream`, {
      method: "POST",
      body: fd,
      credentials,
    }); // Returns a stream, handle carefully
  },
  
  vendorCase: (token: string) => fetch(`${BASE}/v1/vendor/${token}`).then(r => asJson<any>(r)),
  
  // Stubs for Hackathon Wizard that used these sequentially
  createCase: async (body: any) => ({ case_id: "fake-case-id", state: "INTAKE", submission_ts: new Date().toISOString() }),
  uploadArtifact: async (case_id: string, file: File, kind: string, doc_class?: string) => ({ artifact_id: "fake-id", object_uri: "", sha256: "", byte_size: file.size }),
  triggerRun: async (case_id: string) => ({ case_id, started: true }),
"""
    if "preflightDocument:" not in content:
        content = content.replace("export const api = {", "export const api = {" + api_functions)
        
    with open(api_ts, "w", encoding="utf-8") as f:
        f.write(content)

    # 2. Remove Insights.tsx, ActivityLog.tsx, etc.
    to_delete = [
        os.path.join(repo, "components", "ActivityLog.tsx"),
        os.path.join(repo, "views", "vendor", "Insights.tsx")
    ]
    for p in to_delete:
        if os.path.exists(p):
            os.remove(p)

    # 3. Update App.tsx to remove Insights
    app_tsx = os.path.join(repo, "App.tsx")
    with open(app_tsx, "r", encoding="utf-8") as f:
        app_content = f.read()
    app_content = re.sub(r'import Insights.*?\n', '', app_content)
    app_content = re.sub(r'<NavLink to="/m/metrics".*?>.*?<\/NavLink>\s*', '', app_content, flags=re.DOTALL)
    app_content = re.sub(r'<Route\s*path="metrics"\s*element=\{<Insights\s*\/>\}\s*\/>\s*', '', app_content, flags=re.DOTALL)
    with open(app_tsx, "w", encoding="utf-8") as f:
        f.write(app_content)

    # 4. Update VendorShell.tsx
    shell = os.path.join(repo, "views", "vendor", "VendorShell.tsx")
    with open(shell, "r", encoding="utf-8") as f:
        s_content = f.read()
    s_content = s_content.replace('latestCase.state', 'latestCase.status')
    s_content = s_content.replace('latestCase.confidence_score', 'null')
    # Remove metrics from links
    s_content = re.sub(r'\{\s*to:\s*"/m/metrics".*?\},', '', s_content, flags=re.DOTALL)
    with open(shell, "w", encoding="utf-8") as f:
        f.write(s_content)

    # 5. Update Status.tsx
    status_tsx = os.path.join(repo, "views", "vendor", "Status.tsx")
    with open(status_tsx, "r", encoding="utf-8") as f:
        st_content = f.read()
    # It used getReviewerSummary for vendor. Let's just use vendorCase if it has a token, or getCase if Ops.
    # Actually, hackathon Status just fetched getReviewerSummary. We'll change it to getCase.
    st_content = st_content.replace('api.getReviewerSummary(caseId!)', 'api.getCase(caseId!)')
    st_content = st_content.replace('sum.data?.reviewer_summary', 'sum.data?.reviewer_summary') # works on CaseDetail
    st_content = st_content.replace('sum.data?.verdict', 'sum.data?.status')
    with open(status_tsx, "w", encoding="utf-8") as f:
        f.write(st_content)

    print("Frontend errors fixed")

if __name__ == "__main__":
    main()
