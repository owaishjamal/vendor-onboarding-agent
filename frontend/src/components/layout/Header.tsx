import { Link, useNavigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, Me } from "../../api";

export function Wordmark({
  variant,
  onDark,
}: {
  variant: "vendor" | "ops";
  onDark?: boolean;
}) {
  return (
    <Link
      to="/home"
      className="flex items-center gap-3 group shrink-0"
      title="Back to home"
    >
      <span
        className={
          "font-geist text-[26px] font-semibold lowercase leading-none " +
          "tracking-[-0.04em] transition-opacity duration-200 group-hover:opacity-70 " +
          (onDark ? "text-white" : "text-black")
        }
      >
        zamp
      </span>
      <span
        className={"h-6 w-px " + (onDark ? "bg-white/20" : "bg-warm-cream-border")}
      />
      <span className="flex flex-col leading-tight hidden sm:flex">
        <span
          className={
            "text-[11px] font-semibold uppercase tracking-[0.14em] " +
            (onDark ? "text-white/90" : "text-dark-charcoal")
          }
        >
          {variant === "vendor" ? "Vendor Portal" : "Vendor Onboarding"}
        </span>
        <span
          className={
            "text-[11px] font-medium uppercase tracking-[0.14em] " +
            (onDark ? "text-white/50" : "text-surface-500")
          }
        >
          {variant === "vendor" ? "Verification" : "Review & decisions"}
        </span>
      </span>
    </Link>
  );
}

export function HomeButton({ onDark }: { onDark?: boolean }) {
  return (
    <Link
      to="/home"
      className={
        "chip transition-all duration-200 hover:scale-105 " +
        (onDark
          ? "bg-white/10 border-white/20 text-white hover:bg-white/20"
          : "bg-surface-50 border-warm-cream-border text-dark-charcoal hover:bg-warm-off-white hover:text-black")
      }
    >
      Home
    </Link>
  );
}

export function NavLink({
  to,
  active,
  children,
}: {
  to: string;
  active: boolean;
  children: ReactNode;
}) {
  return (
    <Link
      to={to}
      className={
        "rounded-full px-3.5 py-1.5 transition-all duration-200 font-medium " +
        (active
          ? "bg-black text-white"
          : "text-surface-700 hover:bg-surface-100 hover:text-surface-900")
      }
    >
      {children}
    </Link>
  );
}

export function UserChip({ me, onDark }: { me: Me | null; onDark?: boolean }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: async () => {
      qc.setQueryData(["me"], null);
      await qc.invalidateQueries();
      nav("/home", { replace: true });
    },
  });

  const chipMuted = onDark
    ? "bg-white/10 border-white/20 text-white hover:bg-white/20"
    : "bg-surface-50 border-surface-200 text-surface-700 hover:bg-surface-100";

  if (!me) {
    return (
      <Link to="/home#login" className={"chip " + chipMuted}>
        Sign in
      </Link>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <span
        className={
          "chip border hidden lg:inline-flex max-w-[14rem] truncate " +
          (me.role === "ops"
            ? onDark
              ? "bg-white/10 border-white/20 text-white"
              : "bg-accent-500/10 border-accent-500/20 text-accent-900"
            : onDark
              ? "bg-white/10 border-white/20 text-white"
              : "bg-warm-off-white border-warm-cream-border text-dark-charcoal")
        }
        title={me.email}
      >
        {me.role === "ops" ? "ops" : "vendor"} · {me.email}
      </span>
      <button
        type="button"
        className={"chip " + chipMuted}
        disabled={logout.isPending}
        onClick={() => logout.mutate()}
      >
        {logout.isPending ? "…" : "Sign out"}
      </button>
    </div>
  );
}

export function EnvChip({
  health,
  loading,
  error,
  onDark,
}: {
  health: { llm_mode?: string; data_profile?: string } | undefined;
  loading: boolean;
  error: boolean;
  onDark?: boolean;
}) {
  const muted = onDark
    ? "bg-white/10 border-white/20 text-white/80"
    : "bg-surface-50 border-surface-200 text-surface-600";

  if (loading) {
    return (
      <span className={"chip " + muted}>
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
        connecting…
      </span>
    );
  }
  if (error || !health) {
    return (
      <span
        className={
          onDark
            ? "chip bg-red-500/20 border-red-400/30 text-red-100"
            : "chip bg-danger-50 border-danger-100 text-danger-700"
        }
      >
        offline
      </span>
    );
  }
  const isOffline = health.llm_mode === "offline";
  const liveCls = onDark
    ? "bg-white/10 border-white/20 text-white"
    : "bg-warm-off-white border-warm-cream-border text-dark-charcoal";
  const warnCls = onDark
    ? "bg-amber-500/20 border-amber-400/30 text-amber-100"
    : "bg-warn-50 border-warn-100 text-warn-700";

  return (
    <span className={"chip " + (isOffline ? warnCls : liveCls)}>
      <span
        className={
          "inline-block w-1.5 h-1.5 rounded-full " +
          (isOffline ? "bg-warn-400" : "bg-black animate-pulse")
        }
      />
      {isOffline ? "mock" : "live"}
    </span>
  );
}
