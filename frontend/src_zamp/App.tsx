import { useEffect, useState } from "react";
import { api } from "./api";
import Intake from "./views/Intake";
import Queue from "./views/Queue";
import CaseDetail from "./views/CaseDetail";
import Rules from "./views/Rules";
import ProfileBuilder from "./views/ProfileBuilder";
import VendorPortal from "./views/VendorPortal";

type Tab = "intake" | "queue" | "profiles" | "rules";

/** Read a vendor-portal token from the URL hash: #/vendor/<token> */
function vendorToken(): string | null {
  const m = window.location.hash.match(/^#\/vendor\/([A-Za-z0-9_\-]+)/);
  return m ? m[1] : null;
}

export default function App() {
  const [tab, setTab] = useState<Tab>("intake");
  const [openCase, setOpenCase] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [health, setHealth] = useState<any>(null);
  const [vToken, setVToken] = useState<string | null>(vendorToken());

  useEffect(() => {
    const onHash = () => setVToken(vendorToken());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => { api.health().then(setHealth).catch(() => setHealth(null)); }, []);
  const go = (t: Tab) => { setTab(t); setOpenCase(null); };

  // Vendor mode: an entirely separate, minimal shell. No approver nav, no
  // queue, no internal anything — the token is the only credential.
  if (vToken) return <VendorPortal token={vToken} />;

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between gap-4 px-6 py-3">
          <div className="flex items-center gap-6">
            <div>
              <div className="text-sm font-semibold tracking-tight text-slate-900">
                {health?.app_title ?? "Vendor Onboarding"}
              </div>
              <div className="text-[11px] text-slate-500">
                {health?.app_subtitle ?? "Submission in, decided status out"}
              </div>
            </div>
            <nav className="flex gap-1">
              {([["intake", "New vendor"], ["queue", "Review queue"],
                 ["profiles", "Templates"], ["rules", "Rules"]] as [Tab, string][])
                .map(([k, label]) => (
                  <button key={k} onClick={() => go(k)}
                    className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                      tab === k ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}>
                    {label}
                  </button>
                ))}
            </nav>
          </div>

          <div className="flex items-center gap-3 text-[11px]">
            {health ? (
              <>
                <span className="flex items-center gap-1.5 text-slate-500">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  API online
                </span>
                <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-slate-600">
                  llm: {health.llm_provider}
                </span>
                <span className="hidden rounded bg-slate-100 px-2 py-0.5 font-mono text-slate-600 sm:inline">
                  {health.countries?.join(" ")}
                </span>
              </>
            ) : (
              <span className="flex items-center gap-1.5 text-rose-600">
                <span className="h-1.5 w-1.5 rounded-full bg-rose-500" />
                API unreachable
              </span>
            )}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-6 py-6">
        {tab === "intake" && <Intake onFinished={() => setNonce((n) => n + 1)} />}
        {tab === "queue" && (openCase
          ? <CaseDetail caseId={openCase} onBack={() => setOpenCase(null)} />
          : <Queue nonce={nonce} onOpen={setOpenCase} />)}
        {tab === "profiles" && <ProfileBuilder />}
        {tab === "rules" && <Rules nonce={nonce} />}
      </main>
    </div>
  );
}
