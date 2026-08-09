import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { motion } from "framer-motion";
import { api, Case } from "../../api";

/**
 * Persistent shell around the vendor area: collapsible sidebar with the four
 * top-level destinations (Onboarding, Menu, Storefront, Metrics) plus a
 * compact business header that reflects the current case state.
 *
 * Every read goes through the real backend. Nothing on this page bakes in
 * mock counts or hardcoded menu data.
 */
export default function VendorShell() {
  const nav = useNavigate();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const cases = useQuery({
    queryKey: ["me-cases"],
    queryFn: api.listMyCases,
    enabled: !!me.data && me.data.role === "vendor",
    refetchInterval: 3000,
  });

  useEffect(() => {
    // Redirect non-vendors away from the vendor shell.
    if (me.isSuccess && me.data === null) {
      nav("/home", { replace: true });
    } else if (me.isSuccess && me.data && me.data.role !== "vendor") {
      nav("/queue", { replace: true });
    }
  }, [me.data, me.isSuccess, nav]);

  const latestCase: Case | null = useMemo(() => {
    const list = cases.data ?? [];
    return list.length ? list[0] : null;
  }, [cases.data]);

  // Only block on the FIRST load. Returning a placeholder whenever `me.data`
  // is momentarily falsy unmounts the <Outlet/> — and unmounting the outlet
  // destroys the child's state. That is what threw a vendor back to step one
  // of onboarding seconds after their result rendered: the `me` query refetches
  // (on window focus, and alongside the 3s case poll), `me.data` blinked
  // undefined for one render, the wizard remounted, and its `step` state reset
  // to the beginning. The shell must stay mounted once it has loaded.
  if (me.isLoading && !me.data) {
    return (
      <div className="py-16 text-center text-surface-600">Loading…</div>
    );
  }

  return (
    <motion.div
      className="-mx-4 sm:-mx-6 -my-8 min-h-[calc(100vh-4rem)] grid lg:grid-cols-[18rem_1fr]"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.4 }}
    >
      <Sidebar
        businessName={me.data.business_name ?? "Your business"}
        market={me.data.market ?? "SG"}
        latestCase={latestCase}
      />
      <motion.main
        className="px-6 lg:px-10 py-10"
        initial={{ opacity: 0, x: 16 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.45, delay: 0.08 }}
      >
        <Outlet context={{ latestCase, me: me.data }} />
      </motion.main>
    </motion.div>
  );
}

function Sidebar({
  businessName,
  market,
  latestCase,
}: {
  businessName: string;
  market: string;
  latestCase: Case | null;
}) {
  const loc = useLocation();
  const onboardingDone = !!latestCase && TERMINAL.has(latestCase.status);
  const links = [
    {
      to: "/m/onboard",
      label: "Onboarding",
      hint: latestCase ? niceState(latestCase.status) : "Get started",
      icon: "ID",
      lock: false,
    },
    
  ];
  return (
    <motion.aside
      className="border-r border-surface-200 bg-white px-4 py-6 lg:py-8 lg:sticky lg:top-16 lg:h-[calc(100vh-4rem)]"
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="px-2 mb-6">
        <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-black">
          Vendor Portal
        </div>
        <div className="mt-1 text-base font-semibold text-surface-900 truncate">
          {businessName}
        </div>
        <div className="mt-1 text-xs text-surface-500 flex items-center gap-2">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-black" />
          Market · {market}
        </div>
      </div>

      <nav className="space-y-1">
        {links.map((l, i) => {
          const isActive = loc.pathname === l.to;
          const cls = isActive
            ? "nav-pill nav-pill-active w-full justify-between"
            : "nav-pill w-full justify-between";
          if (l.lock) {
            return (
              <motion.div
                key={l.to}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 0.5, x: 0 }}
                transition={{ delay: 0.05 * i }}
                className={`${cls} opacity-50 cursor-not-allowed`}
                aria-disabled
                title="Finish onboarding to unlock"
              >
                <span className="flex items-center gap-3">
                  <IconBadge tag={l.icon} muted />
                  <span className="flex flex-col text-left">
                    <span>{l.label}</span>
                    <span className="text-[11px] font-normal text-surface-500">
                      Locked
                    </span>
                  </span>
                </span>
                <span aria-hidden>🔒</span>
              </motion.div>
            );
          }
          return (
            <motion.div
              key={l.to}
              initial={{ opacity: 0, x: -12 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.06 * i, duration: 0.35 }}
              whileHover={{ x: 4 }}
            >
              <NavLink to={l.to} className={cls}>
                <span className="flex items-center gap-3">
                  <IconBadge tag={l.icon} active={isActive} />
                  <span className="flex flex-col text-left">
                    <span>{l.label}</span>
                    <span className="text-[11px] font-normal text-surface-500">
                      {l.hint}
                    </span>
                  </span>
                </span>
                {isActive && (
                  <span aria-hidden className="text-black">
                    →
                  </span>
                )}
              </NavLink>
            </motion.div>
          );
        })}
      </nav>

      {latestCase && (
        <div className="mt-8 mx-1 premium-card p-4 bg-gradient-to-br from-warm-off-white/60 to-white">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-black">
            Current case
          </div>
          <div className="mt-1 text-xs font-mono text-surface-700 truncate">
            {latestCase.case_id}
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs">
            <StateDot state={latestCase.status} />
            <span className="text-surface-700">{niceState(latestCase.status)}</span>
          </div>
        </div>
      )}
    </motion.aside>
  );
}

function IconBadge({
  tag,
  active,
  muted,
}: {
  tag: string;
  active?: boolean;
  muted?: boolean;
}) {
  const cls = active
    ? "bg-black text-white"
    : muted
      ? "bg-surface-100 text-surface-500"
      : "bg-surface-100 text-surface-700";
  return (
    <span
      className={`inline-flex items-center justify-center w-7 h-7 rounded-lg text-[11px] font-bold ${cls}`}
    >
      {tag}
    </span>
  );
}

const TERMINAL = new Set([
  "PROVISIONED",
  "REJECTED",
  "AUTO_APPROVED",
  "PENDING_REVIEW",
]);

function niceState(state: string): string {
  switch (state) {
    case "INTAKE":
      return "Awaiting documents";
    case "PENDING_REVIEW":
      return "Manual review";
    case "AUTO_APPROVED":
    case "PROVISIONED":
      return "Approved · live";
    case "REJECTED":
      return "Rejected";
    default:
      return state.replaceAll("_", " ").toLowerCase();
  }
}

function StateDot({ state }: { state: string }) {
  const cls =
    state === "AUTO_APPROVED" || state === "PROVISIONED"
      ? "bg-black"
      : state === "REJECTED"
        ? "bg-danger-500"
        : state === "PENDING_REVIEW"
          ? "bg-warn-500"
          : "bg-surface-500 animate-pulse";
  return <span className={`inline-block w-2 h-2 rounded-full ${cls}`} />;
}

export type VendorOutletContext = {
  latestCase: Case | null;
  me: NonNullable<Awaited<ReturnType<typeof api.me>>>;
};
