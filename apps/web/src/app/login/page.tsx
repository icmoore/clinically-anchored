"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthPage } from "@/components/auth-page";
import { FormError, buttonClass, inputClass, labelClass } from "@/components/auth-form";
import { PATIENT_TEXT } from "@/lib/patient-strings";
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
    <AuthPage current="login" subtitle="Clinician sign-in">
      <form onSubmit={handleSubmit}>
        <div>
          <label htmlFor="email" className={labelClass}>
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={inputClass}
          />
        </div>
        <div className="mt-4">
          <label htmlFor="password" className={labelClass}>
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputClass}
          />
        </div>
        {error && <FormError>{error}</FormError>}
        <button type="submit" disabled={busy} className={`${buttonClass} mt-4`}>
          {busy ? "Signing in..." : "Sign in"}
        </button>
      </form>
      <p className="mt-4 text-sm text-muted">
        {PATIENT_TEXT.siteLinks.patientPrompt}{" "}
        <Link href="/sign-in" className="text-link underline hover:text-link-hover">
          {PATIENT_TEXT.siteLinks.patientLink}
        </Link>
      </p>
    </AuthPage>
  );
}
