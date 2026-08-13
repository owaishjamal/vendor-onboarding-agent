import { Link, useNavigate } from "react-router-dom";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
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

/**
 * Account menu — one control for identity and everything you can do with it.
 *
 * This replaces a row of four separate pills (Home · live · ops@… · Sign out)
 * plus a second "Continue as …" bar underneath. Two problems with that:
 *
 *   1. An email address is not a navigation item. Rendered as a chip beside
 *      real controls it reads as something you can click, competes with them
 *      for attention, and pushes the actual actions to the edge of the screen.
 *   2. Sign out sat one pixel from Sign in's position and one slot from the
 *      buttons people use constantly. Destructive-ish actions belong behind a
 *      deliberate second step, not adjacent to routine ones.
 *
 * An avatar that opens a panel is the convention every product with accounts
 * has converged on, and the reason is that it collapses identity + account
 * actions into a single predictable place instead of spreading them along a
 * bar. Everything the two old bars offered is still here, one click deeper.
 */
export function AccountMenu({ me, onDark }: { me: Me | null; onDark?: boolean }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement | null>(null);

  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: async () => {
      qc.setQueryData(["me"], null);
      await qc.invalidateQueries();
      setOpen(false);
      nav("/home", { replace: true });
    },
  });

  // Close on outside click and on Escape. Both are expected of a menu, and a
  // panel that can only be dismissed by clicking the trigger again feels
  // broken in a way people notice without being able to name.
  useEffect(() => {
    if (!open) return;
    function onPointer(e: MouseEvent) {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!me) {
    return (
      <Link
        to="/home#login"
        className={
          "rounded-full px-4 py-1.5 text-sm font-medium transition-colors duration-200 " +
          (onDark
            ? "bg-white text-black hover:bg-white/90"
            : "bg-black text-white hover:bg-near-black")
        }
      >
        Sign in
      </Link>
    );
  }

  const isOps = me.role === "ops";
  const home = isOps ? "/queue" : "/m/onboard";
  const homeLabel = isOps ? "Ops dashboard" : "Vendor portal";
  const initials = (me.email.split("@")[0] || "?").slice(0, 2).toUpperCase();

  return (
    <div className="relative" ref={wrap}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Account menu for ${me.email}`}
        className={
          "flex items-center gap-2 rounded-full pl-1 pr-2 py-1 border " +
          "transition-colors duration-200 " +
          (onDark
            ? "border-white/20 hover:bg-white/10 " + (open ? "bg-white/10" : "")
            : "border-warm-cream-border hover:bg-warm-off-white " +
              (open ? "bg-warm-off-white" : "bg-white"))
        }
      >
        <span
          className={
            "flex h-7 w-7 items-center justify-center rounded-full text-[11px] " +
            "font-semibold tracking-[0.02em] " +
            (onDark ? "bg-white text-black" : "bg-black text-white")
          }
        >
          {initials}
        </span>
        {/* The email is the useful label on a wide screen and noise on a
            narrow one, where the avatar alone is unambiguous. */}
        <span
          className={
            "hidden lg:block max-w-[11rem] truncate text-[13px] font-medium " +
            (onDark ? "text-white/90" : "text-dark-charcoal")
          }
        >
          {me.email}
        </span>
        <svg
          viewBox="0 0 12 12"
          aria-hidden
          className={
            "h-3 w-3 shrink-0 transition-transform duration-200 " +
            (open ? "rotate-180 " : "") +
            (onDark ? "text-white/60" : "text-surface-500")
          }
        >
          <path
            d="M2.5 4.5 6 8l3.5-3.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="menu"
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
            className="absolute right-0 mt-2 w-64 origin-top-right overflow-hidden
                       rounded-[12px] border border-warm-cream-border bg-white
                       shadow-[0_8px_28px_rgba(0,0,0,0.10)]"
          >
            <div className="px-4 py-3 border-b border-warm-cream-border">
              <div className="truncate text-[13px] font-medium text-black">
                {me.email}
              </div>
              <div className="mt-0.5 text-[11px] font-medium uppercase tracking-[0.12em] text-surface-500">
                {isOps ? "Operations reviewer" : "Vendor"}
              </div>
            </div>

            <div className="p-1">
              <Link
                to={home}
                role="menuitem"
                onClick={() => setOpen(false)}
                className="flex items-center justify-between rounded-[8px] px-3 py-2
                           text-[13px] font-medium text-dark-charcoal
                           transition-colors hover:bg-warm-off-white hover:text-black"
              >
                {homeLabel}
                <span aria-hidden className="text-surface-400">
                  &rarr;
                </span>
              </Link>
              <Link
                to="/home#login"
                role="menuitem"
                onClick={() => setOpen(false)}
                className="block rounded-[8px] px-3 py-2 text-[13px] font-medium
                           text-dark-charcoal transition-colors
                           hover:bg-warm-off-white hover:text-black"
              >
                Switch account
              </Link>
            </div>

            <div className="border-t border-warm-cream-border p-1">
              <button
                type="button"
                role="menuitem"
                disabled={logout.isPending}
                onClick={() => logout.mutate()}
                className="w-full rounded-[8px] px-3 py-2 text-left text-[13px]
                           font-medium text-surface-600 transition-colors
                           hover:bg-warm-off-white hover:text-black
                           disabled:opacity-50"
              >
                {logout.isPending ? "Signing out…" : "Sign out"}
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
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
