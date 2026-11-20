// Variables the browser bundle needs. Next.js bakes NEXT_PUBLIC_* values in at BUILD time, and the
// app falls back to localhost placeholders when one is empty (src/lib/supabase.ts, api.ts). On a
// deployed build that fallback is always wrong, so a Vercel build stops instead of shipping it.

export const REQUIRED_PUBLIC_ENV = [
  "NEXT_PUBLIC_API_URL",
  "NEXT_PUBLIC_SUPABASE_URL",
  "NEXT_PUBLIC_SUPABASE_ANON_KEY",
] as const;

/** Names (from REQUIRED_PUBLIC_ENV) that are empty or unset in `env`. */
export function missingPublicEnv(env: Record<string, string | undefined>): string[] {
  return REQUIRED_PUBLIC_ENV.filter((name) => !env[name]?.trim());
}

/** Throws on a Vercel build (VERCEL is set there) when any required variable is missing.
 *  Local development and CI builds, which have no VERCEL, are unaffected. */
export function assertPublicEnvForDeploy(env: Record<string, string | undefined>): void {
  if (!env.VERCEL) return;
  const missing = missingPublicEnv(env);
  if (missing.length === 0) return;
  const where = env.VERCEL_ENV ? ` for the ${env.VERCEL_ENV} environment` : "";
  throw new Error(
    `Missing build-time variable(s)${where}: ${missing.join(", ")}. Set them in Vercel ` +
      "(Project -> Settings -> Environment Variables) for this environment, then redeploy. " +
      "Without them the site would call http://localhost:54321 and fail to sign in.",
  );
}
