"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { SiteNav } from "@/components/site-nav";
import { supabase } from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      // Wrong credentials are the common case; anything else (network, config,
      // rate limit) should show its real reason so it can be diagnosed.
      const wrongLogin = error.status === 400 && /invalid login credentials/i.test(error.message);
      setError(
        wrongLogin
          ? "That email and password didn't work."
          : `Sign-in failed: ${error.message || "couldn't reach the sign-in service"}`,
      );
      setBusy(false);
      return;
    }
    router.replace("/dashboard");
  }

  return (
    <>
      <SiteNav current="login" />
      <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-6 py-12">
        <h1 className="text-xl font-semibold text-ink">Clinically Anchored</h1>
        <p className="mb-6 mt-1 text-sm text-muted">Clinician sign-in</p>
        <form onSubmit={handleSubmit} className="space-y-4">
          <label className="block text-sm text-muted">
            Email
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 block w-full rounded-md border border-border-strong bg-surface px-3 py-2 text-base"
            />
          </label>
          <label className="block text-sm text-muted">
            Password
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 block w-full rounded-md border border-border-strong bg-surface px-3 py-2 text-base"
            />
          </label>
          {error && <p className="text-sm text-flag-text">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2.5 text-base font-medium text-white disabled:opacity-60"
          >
            {busy ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </div>
    </>
  );
}
