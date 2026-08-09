import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";

export default function Login() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: api.login,
    onSuccess: (me) => {
      qc.invalidateQueries({ queryKey: ["me"] });
      // Ops users land on the internal demo console; vendors on their wizard.
      nav(me.role === "ops" ? "/queue" : "/m/onboard", { replace: true });
    },
    onError: (e: Error) => setError(e.message),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    mut.mutate({ email: email.trim(), password });
  }

  return (
    <div className="mx-auto max-w-md py-12">
      <div className="card p-8">
        <h1 className="text-2xl font-bold text-surface-900">Sign in</h1>
        <p className="mt-2 text-sm text-surface-600">
          Vendors land back on their onboarding wizard. Ops users land on the
          internal demo console.
        </p>

        <form onSubmit={submit} className="mt-6 space-y-4">
          <label className="block">
            <span className="block text-sm font-medium text-surface-700 mb-1.5">
              Email
            </span>
            <input
              required
              type="email"
              autoComplete="email"
              className="input w-full"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="mei@kopitiam.sg"
            />
          </label>

          <label className="block">
            <span className="block text-sm font-medium text-surface-700 mb-1.5">
              Password
            </span>
            <input
              required
              type="password"
              autoComplete="current-password"
              className="input w-full"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>

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
            {mut.isPending ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-surface-600">
          New here?{" "}
          <Link to="/m/signup" className="font-medium text-black">
            Create a vendor account
          </Link>
          <span className="text-surface-300 mx-2">·</span>
          <Link to="/home" className="font-medium text-black">
            Journey overview
          </Link>
        </p>
      </div>
    </div>
  );
}
