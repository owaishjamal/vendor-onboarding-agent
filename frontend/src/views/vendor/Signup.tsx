import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";

const MARKETS = ["SG", "ID", "MY", "TH", "VN", "PH", "KH", "MM"] as const;

export default function Signup() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [businessName, setBusinessName] = useState("");
  const [market, setMarket] = useState<(typeof MARKETS)[number]>("SG");
  const [error, setError] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: api.signup,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["me"] });
      nav("/m/onboard", { replace: true });
    },
    onError: (e: Error) => setError(e.message),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    mut.mutate({
      email: email.trim(),
      password,
      business_name: businessName.trim(),
      market,
    });
  }

  return (
    <div className="mx-auto max-w-md py-12">
      <div className="card p-8">
        <h1 className="text-2xl font-bold text-surface-900">
          Register as a vendor
        </h1>
        <p className="mt-2 text-sm text-surface-600">
          A single account gets you the wizard for KYC and menu upload. Our
          agents will do the rest.
        </p>

        <form onSubmit={submit} className="mt-6 space-y-4">
          <Field label="Business name">
            <input
              required
              className="input w-full"
              value={businessName}
              onChange={(e) => setBusinessName(e.target.value)}
              placeholder="Mei's Kopi Tiam"
            />
          </Field>

          <Field label="Market">
            <select
              className="input w-full"
              value={market}
              onChange={(e) => setMarket(e.target.value as typeof market)}
            >
              {MARKETS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Email">
            <input
              required
              type="email"
              autoComplete="email"
              className="input w-full"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="mei@kopitiam.sg"
            />
          </Field>

          <Field label="Password (min 8 chars)">
            <input
              required
              type="password"
              autoComplete="new-password"
              minLength={8}
              className="input w-full"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>

          {error && (
            <div className="rounded-xl border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={mut.isPending}
            className="btn-primary w-full"
          >
            {mut.isPending ? "Creating account…" : "Create account"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-surface-600">
          Already onboarded?{" "}
          <Link to="/m/login" className="font-medium text-black">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-surface-700 mb-1.5">
        {label}
      </span>
      {children}
    </label>
  );
}
