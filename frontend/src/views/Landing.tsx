import { FormEvent, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { api, Me } from "../api";

/**
 * One-page premium landing.
 *
 * Sections (in order):
 *   1. Hero — Zamp branding, gradient mesh, animated agent flow.
 *   2. Dual login — Vendor / Ops side-by-side, real /v1/auth/login.
 *   3. "What we solve" — pain points for vendors and for ops.
 *   4. "How it works" — plain-English agent journey, 4 cards.
 *   5. Stats — real, simple architecture numbers.
 *   6. Final CTA.
 *
 * Nothing on this page is hardcoded business data; all credentials hit the
 * live FastAPI auth endpoints, so a working backend is required for the
 * login forms to function.
 */
export default function Landing() {
  const meQ = useQuery({ queryKey: ["me"], queryFn: api.me });
  const me = meQ.data ?? null;

  return (
    <motion.div className="-mx-4 sm:-mx-6 -my-8">
      {/* No "Continue as …" bar. It restated the email the header already
          shows and added a second row of account actions directly beneath the
          first, so a signed-in visitor met two stacked identity bars saying
          the same thing before reaching any content. The header's account
          menu carries all of it — including the way into your workspace. */}
      <Hero me={me} />
      <DualLoginSection />
      <SolutionSection />
      <HowItWorksSection />
      <StatsSection />
      <FinalCta />
    </motion.div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Hero
// ─────────────────────────────────────────────────────────────────────────────

function Hero({ me }: { me: Me | null }) {
  const ref = useRef<HTMLDivElement | null>(null);
  // Subtle parallax: move the mesh based on pointer.
  const [tilt, setTilt] = useState({ x: 0, y: 0 });
  function onMove(e: React.MouseEvent<HTMLDivElement>) {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    const px = (e.clientX - rect.left) / rect.width - 0.5;
    const py = (e.clientY - rect.top) / rect.height - 0.5;
    setTilt({ x: px * 12, y: py * 8 });
  }
  return (
    <section
      ref={ref}
      onMouseMove={onMove}
      onMouseLeave={() => setTilt({ x: 0, y: 0 })}
      className="hero-mesh text-white relative overflow-hidden"
    >
      {/* Floating orbs */}
      <div
        className="pointer-events-none absolute -top-32 -left-20 w-[34rem] h-[34rem] rounded-full blur-3xl opacity-60 glow-drift"
        style={{
          background: "radial-gradient(circle, rgba(255,255,255,0.10), transparent 60%)",
          transform: `translate3d(${tilt.x}px, ${tilt.y}px, 0)`,
          transition: "transform 200ms ease-out",
        }}
      />
      <div
        className="pointer-events-none absolute -bottom-40 -right-20 w-[34rem] h-[34rem] rounded-full blur-3xl opacity-50"
        style={{
          background: "radial-gradient(circle, rgba(255,255,255,0.06), transparent 60%)",
          transform: `translate3d(${-tilt.x}px, ${-tilt.y}px, 0)`,
          transition: "transform 200ms ease-out",
        }}
      />

      <div className="relative mx-auto max-w-7xl px-6 pt-16 pb-24 lg:pt-24 lg:pb-32">
        <div className="grid lg:grid-cols-12 gap-12 items-center">
          <div className="lg:col-span-7 space-y-7">
            <div className="reveal-on-mount inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold border border-white/20 bg-white/5 text-white/85">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-black pulse-ring" />
              Zamp · Vendor Onboarding & Verification
            </div>
            <h1 className="reveal-on-mount reveal-delay-1 text-5xl sm:text-6xl lg:text-7xl font-extrabold leading-[1.05] tracking-tight">
              Onboarding that
              <br />
              <span className="gradient-text-light">thinks for itself.</span>
            </h1>
            <p className="reveal-on-mount reveal-delay-2 text-lg lg:text-xl text-white/75 max-w-2xl leading-relaxed">
              A team of AI agents reads every document, cross-checks the
              details, judges the risk, and either launches the vendor or
              sends a short, human-readable report to ops.
              <span className="block mt-2 text-white/55 text-base">
                What used to take 5 days now takes ~15 seconds.
              </span>
            </p>
            {/* Signed in, the primary action is "get to your work", not
                "sign in again". This is where the removed banner's one useful
                control now lives — in the page's main call to action rather
                than in a strip above it. */}
            <div className="reveal-on-mount reveal-delay-3 flex flex-wrap items-center gap-3">
              {me ? (
                <Link
                  to={me.role === "ops" ? "/queue" : "/m/onboard"}
                  className="btn-hero"
                >
                  {me.role === "ops" ? "Ops dashboard" : "Vendor portal"}
                  <span aria-hidden>→</span>
                </Link>
              ) : (
                <a href="#login" className="btn-hero">
                  Try the live demo
                  <span aria-hidden>→</span>
                </a>
              )}
              <a href="#how" className="btn-ghost-light">
                How it works
              </a>
            </div>
            <div className="reveal-on-mount reveal-delay-4 flex flex-wrap gap-6 pt-2 text-sm text-white/70">
              <Pillar label="~15s" sub="time to decision" />
              <Pillar label="9 checks" sub="rules + AI, with a human in the loop" />
              <Pillar label="100%" sub="audit-logged, every step" />
            </div>
          </div>
          <div className="lg:col-span-5">
            <AgentFlowOrb />
          </div>
        </div>
      </div>
    </section>
  );
}

function Pillar({ label, sub }: { label: string; sub: string }) {
  return (
    <div>
      <div className="text-2xl font-bold text-white">{label}</div>
      <div className="text-xs uppercase tracking-wider text-white/55">{sub}</div>
    </div>
  );
}

/**
 * Living "orb" diagram: documents in, rules and AI across, one verdict out.
 * Pure CSS / SVG, no external lib.
 */
function AgentFlowOrb() {
  return (
    <div className="relative w-full aspect-square max-w-md mx-auto">
      {/* Outer ring */}
      <div className="absolute inset-0 rounded-full border border-white/15 animate-[spin_18s_linear_infinite]" />
      <div
        className="absolute inset-6 rounded-full border border-dashed border-white/10 animate-[spin_30s_linear_infinite_reverse]"
      />
      {/* Center: judge */}
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="float-soft glass-dark rounded-3xl px-6 py-5 text-center min-w-[170px] ring-brand-glow">
          <div className="text-[10px] uppercase tracking-widest text-black">
            Judge
          </div>
          <div className="text-lg font-semibold text-white">
            Final verdict
          </div>
          <div className="text-xs text-white/60 mt-0.5">
            grounded · auditable
          </div>
        </div>
      </div>
      {/* Top: DVA */}
      <OrbCard
        label="DVA"
        title="Reads documents"
        sub="ID · License · Bank"
        className="top-0 left-1/2 -translate-x-1/2"
        delay={0.2}
      />
      {/* Right: the deterministic layer */}
      <OrbCard
        label="RULES"
        title="Validates formats"
        sub="PAN · GSTIN · IBAN"
        className="right-0 top-1/2 -translate-y-1/2"
        delay={0.4}
      />
      {/* Bottom: Cross-check */}
      <OrbCard
        label="Cross-check"
        title="Names match?"
        sub="ACRA · sanctions · trust"
        className="bottom-0 left-1/2 -translate-x-1/2"
        delay={0.6}
      />
      {/* Left: Ops */}
      <OrbCard
        label="Ops"
        title="One-tap action"
        sub="approve · reject · ask"
        className="left-0 top-1/2 -translate-y-1/2"
        delay={0.8}
      />
    </div>
  );
}

function OrbCard({
  label,
  title,
  sub,
  className,
  delay,
}: {
  label: string;
  title: string;
  sub: string;
  className?: string;
  delay: number;
}) {
  return (
    <div
      className={`absolute ${className} float-soft`}
      style={{ animationDelay: `${delay}s` }}
    >
      <div className="glass-dark rounded-2xl px-4 py-3 min-w-[150px] text-left">
        <div className="text-[10px] uppercase tracking-widest text-black font-semibold">
          {label}
        </div>
        <div className="text-sm font-semibold text-white mt-0.5">{title}</div>
        <div className="text-[11px] text-white/55">{sub}</div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Dual login
// ─────────────────────────────────────────────────────────────────────────────

function DualLoginSection() {
  return (
    // scroll-mt clears the sticky 64px header. Without it the browser aligns
    // the section's top edge with the viewport's, and the header sits over the
    // heading you were sent here to read.
    <section id="login" className="section-soft border-y border-surface-200 scroll-mt-20">
      <div className="mx-auto max-w-7xl px-6 py-20 lg:py-24">
        <div className="text-center mb-12">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-black">
            Pick your side
          </p>
          <h2 className="mt-2 text-3xl lg:text-4xl font-bold text-surface-900">
            Two products, one platform.
          </h2>
          <p className="mt-3 text-surface-600 max-w-2xl mx-auto">
            Vendors get a calm, helpful onboarding. Ops get an
            intelligence console that explains every AI decision.
          </p>
        </div>
        <div className="grid lg:grid-cols-2 gap-6">
          <LoginCard
            kind="vendor"
            title="I'm a Vendor"
            subtitle="Set up your account in minutes."
            demoEmail="vendor@demo.com"
            demoPassword="demo12345"
            bullets={[
              "Upload your ID and business license",
              "Get a real-time verdict from the AI agents",
              "Track status in real-time",
            ]}
          />
          <LoginCard
            kind="ops"
            title="Ops Console"
            subtitle="Review only what the agents could not."
            demoEmail="ops@demo.com"
            demoPassword="demo12345"
            bullets={[
              "Priority queue · sorted by risk and SLA",
              "Traceable action history",
              "Resolve edge cases accurately",
            ]}
          />
        </div>
      </div>
    </section>
  );
}

function LoginCard({
  kind,
  title,
  subtitle,
  demoEmail,
  demoPassword,
  bullets,
}: {
  kind: "vendor" | "ops";
  title: string;
  subtitle: string;
  demoEmail: string;
  demoPassword: string;
  bullets: string[];
}) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [email, setEmail] = useState(demoEmail);
  const [password, setPassword] = useState(demoPassword);
  const [err, setErr] = useState<string | null>(null);
  const isOps = kind === "ops";

  const m = useMutation({
    mutationFn: () => api.login({ email, password }),
    onSuccess: async (me) => {
      await qc.invalidateQueries({ queryKey: ["me"] });
      if (me.role === "ops") {
        nav("/queue", { replace: true });
      } else {
        nav("/m/onboard", { replace: true });
      }
    },
    onError: (e: any) => setErr(e.message || String(e)),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    m.mutate();
  }

  return (
    <div
      className={`premium-card overflow-hidden flex flex-col ${
        isOps ? "border-surface-300" : "border-warm-cream-border"
      }`}
    >
      <div
        className={`relative px-6 pt-6 pb-5 ${
          isOps
            ? "bg-gradient-to-br from-surface-900 to-accent-900 text-white"
            : "bg-gradient-to-br from-warm-off-white to-white"
        }`}
      >
        <div
          className={`text-[11px] uppercase tracking-[0.16em] font-semibold ${
            isOps ? "text-black" : "text-black"
          }`}
        >
          {isOps ? "Ops console" : "Vendor portal"}
        </div>
        <h3
          className={`mt-1 text-2xl font-bold ${
            isOps ? "text-white" : "text-surface-900"
          }`}
        >
          {title}
        </h3>
        <p
          className={`mt-1 text-sm ${
            isOps ? "text-white/70" : "text-surface-600"
          }`}
        >
          {subtitle}
        </p>
      </div>
      <div className="px-6 pt-5 pb-6 grid sm:grid-cols-2 gap-6 flex-1">
        <ul className="space-y-2.5 text-sm text-surface-700">
          {bullets.map((b) => (
            <li key={b} className="flex gap-2.5">
              <span className="mt-1 inline-block w-1.5 h-1.5 rounded-full bg-black shrink-0" />
              <span>{b}</span>
            </li>
          ))}
        </ul>
        <form onSubmit={onSubmit} className="space-y-2.5">
          <label className="block text-xs font-medium text-surface-700">
            Email
            <input
              type="email"
              className="input mt-1 w-full"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label className="block text-xs font-medium text-surface-700">
            Password
            <input
              type="password"
              className="input mt-1 w-full"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          {err && (
            <div className="text-xs text-danger-700 bg-danger-50 border border-danger-100 rounded-lg px-2 py-1.5">
              {err}
            </div>
          )}
          <button
            type="submit"
            disabled={m.isPending}
            className="btn-primary w-full mt-1"
          >
            {m.isPending ? "Signing in…" : `Sign in as ${kind}`}
          </button>
          <p className="text-[11px] text-surface-500 text-center">
            Demo creds prefilled · password{" "}
            <span className="font-mono">{demoPassword}</span>
          </p>
        </form>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// "What we solve"
// ─────────────────────────────────────────────────────────────────────────────

function SolutionSection() {
  const items = useMemo(
    () => [
      {
        side: "Vendors",
        accent: "brand" as const,
        eyebrow: "For Vendors",
        title: "Stop waiting days to go live.",
        body: "Most vendors do not know if their docs are right, what is missing, or when they will get paid. We give an instant, plain-English verdict — and if something is wrong, we show what to fix.",
        wins: [
          "Pre-flight chip on every upload",
          "Instant decision card · trust score",
          "Every finding carries the evidence behind it",
        ],
      },
      {
        side: "Ops",
        accent: "dark" as const,
        eyebrow: "For procurement ops",
        title: "Review the 10% that actually matter.",
        body: "Onboarding queues drown reviewers in low-risk paperwork. Agents auto-approve clean cases, reject obvious fraud, and only escalate the cases a human should see — with the AI's reasoning attached.",
        wins: [
          "Priority queue with reasons + SLA",
          "Full agent timeline per case · cost + latency",
          "Reviewer Copilot: ask the case anything",
        ],
      },
    ],
    [],
  );
  return (
    <section className="bg-white">
      <div className="mx-auto max-w-7xl px-6 py-20 lg:py-28">
        <div className="text-center mb-14">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-black">
            What we solve
          </p>
          <h2 className="mt-2 text-3xl lg:text-4xl font-bold text-surface-900">
            Onboarding is broken on both sides.
          </h2>
        </div>
        <div className="grid lg:grid-cols-2 gap-8">
          {items.map((it) => (
            <SolutionCard key={it.side} {...it} />
          ))}
        </div>
      </div>
    </section>
  );
}

function SolutionCard({
  accent,
  eyebrow,
  title,
  body,
  wins,
}: {
  accent: "brand" | "dark";
  eyebrow: string;
  title: string;
  body: string;
  wins: string[];
  side?: string;
}) {
  const dark = accent === "dark";
  return (
    <div
      className={`relative rounded-3xl overflow-hidden p-8 lg:p-10 ${
        dark
          ? "bg-gradient-to-br from-surface-900 via-accent-900 to-black text-white border border-white/10"
          : "premium-card border-warm-cream-border bg-gradient-to-br from-white to-warm-off-white/40"
      }`}
    >
      <div
        aria-hidden
        className={`absolute -top-20 -right-16 w-72 h-72 rounded-full blur-3xl ${
          dark ? "opacity-40" : "opacity-60"
        }`}
        style={{
          background: dark
            ? "radial-gradient(circle, rgba(255,255,255,0.06), transparent 60%)"
            : "radial-gradient(circle, rgba(255,255,255,0.10), transparent 60%)",
        }}
      />
      <div className="relative">
        <div
          className={`text-[11px] uppercase tracking-[0.18em] font-semibold ${
            dark ? "text-black" : "text-black"
          }`}
        >
          {eyebrow}
        </div>
        <h3
          className={`mt-2 text-2xl lg:text-3xl font-bold ${
            dark ? "text-white" : "text-surface-900"
          }`}
        >
          {title}
        </h3>
        <p
          className={`mt-3 text-base leading-relaxed ${
            dark ? "text-white/75" : "text-surface-700"
          }`}
        >
          {body}
        </p>
        <div className="mt-6 grid sm:grid-cols-2 gap-2.5">
          {wins.map((w) => (
            <div
              key={w}
              className={`flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm ${
                dark
                  ? "bg-white/5 text-white border border-white/10"
                  : "bg-white text-surface-800 border border-surface-200"
              }`}
            >
              <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-black text-white text-[10px] font-bold">
                ✓
              </span>
              {w}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// "How it works"
// ─────────────────────────────────────────────────────────────────────────────

function HowItWorksSection() {
  const steps = [
    {
      n: "01",
      title: "We read what you uploaded",
      body: "The document agent reads every page — ID, licence, bank proof — and pulls out the fields, the way a careful reviewer would. It tells you how sure it is.",
      tag: "Vision · Gemini",
    },
    {
      n: "02",
      title: "We only ask for what applies to you",
      body: "Your category decides the checklist. A freelancer is never asked to incorporate; a contractor with people on site is asked for workers' cover. Every request says why it is being made.",
      tag: "Category profiles",
    },
    {
      n: "03",
      title: "We cross-check the story",
      body: "Do the names match across every document? Does the registration exist in the company registry? Is anyone on a sanctions list? Each check has a clear pass or fail and a reason code.",
      tag: "Rules + retrieval",
    },
    {
      n: "04",
      title: "A Judge writes the verdict",
      body: "Everything is combined into one verdict — approved, approved with conditions, needs review, or rejected — with the exact blockers and what to do next.",
      tag: "Reasoning · Gemini",
    },
  ];
  return (
    <section id="how" className="section-soft scroll-mt-20">
      <div className="mx-auto max-w-7xl px-6 py-20 lg:py-28">
        <div className="text-center mb-14">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-black">
            How it works
          </p>
          <h2 className="mt-2 text-3xl lg:text-4xl font-bold text-surface-900">
            Four agents, one calm experience.
          </h2>
          <p className="mt-3 text-surface-600 max-w-2xl mx-auto">
            No magic, no jargon. Every step is logged with the model used, the
            cost, and the result.
          </p>
        </div>
        <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-5">
          {steps.map((s, i) => (
            <StepCard key={s.n} {...s} index={i} />
          ))}
        </div>
      </div>
    </section>
  );
}

function StepCard({
  n,
  title,
  body,
  tag,
  index,
}: {
  n: string;
  title: string;
  body: string;
  tag: string;
  index: number;
}) {
  return (
    <div
      className={`premium-card p-6 reveal-on-mount reveal-delay-${
        Math.min(index + 1, 5)
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="text-3xl font-extrabold gradient-text">{n}</span>
        <span className="chip bg-warm-off-white border-warm-cream-border text-black">
          {tag}
        </span>
      </div>
      <h3 className="mt-4 text-lg font-semibold text-surface-900">{title}</h3>
      <p className="mt-2 text-sm text-surface-700 leading-relaxed">{body}</p>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Stats (real architecture characteristics, not fake KPIs)
// ─────────────────────────────────────────────────────────────────────────────

function StatsSection() {
  const stats = [
    { k: "~15s", v: "Upload to verdict" },
    { k: "9", v: "Checks per submission" },
    { k: "100%", v: "Findings carry evidence" },
    { k: "0", v: "Hardcoded decisions" },
  ];
  return (
    <section className="bg-white">
      <div className="mx-auto max-w-7xl px-6 py-[72px] border-y border-warm-cream-border">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-12">
          {stats.map((s, i) => (
            <motion.div
              key={s.k}
              className="text-center"
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.1, duration: 0.5 }}
            >
              <div className="font-geist text-[45px] lg:text-[56px] font-semibold leading-none tracking-[-0.04em] text-black">
                {s.k}
              </div>
              <div className="mt-3 text-[15px] font-medium tracking-[-0.02em] text-dark-charcoal">
                {s.v}
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Final CTA
// ─────────────────────────────────────────────────────────────────────────────

function FinalCta() {
  return (
    <section className="bg-surface-50">
      <div className="mx-auto max-w-5xl px-6 py-20 lg:py-24 text-center">
        <h2 className="text-3xl lg:text-4xl font-bold text-surface-900">
          Try it with your own vendor.
        </h2>
        <p className="mt-3 text-surface-600 max-w-2xl mx-auto">
          Sign in above, upload real documents, and watch the agents reason
          through your case in real time.
        </p>
        <div className="mt-6 flex justify-center gap-3">
          <a href="#login" className="btn-primary">
            Go to sign in
          </a>
          <a href="#how" className="btn-secondary border border-surface-300">
            See how it works
          </a>
        </div>
      </div>
    </section>
  );
}
