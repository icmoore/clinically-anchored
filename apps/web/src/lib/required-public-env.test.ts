// Run with `pnpm test` (Node's built-in test runner; no extra dependencies).
import assert from "node:assert/strict";
import { test } from "node:test";
import { assertPublicEnvForDeploy, missingPublicEnv } from "./required-public-env.ts";

const FULL = {
  NEXT_PUBLIC_API_URL: "https://api-staging.example.test",
  NEXT_PUBLIC_SUPABASE_URL: "https://x.supabase.co",
  NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon",
};

test("a complete set has nothing missing", () => {
  assert.deepEqual(missingPublicEnv(FULL), []);
});

test("unset, empty and whitespace-only values count as missing", () => {
  assert.deepEqual(
    missingPublicEnv({ NEXT_PUBLIC_API_URL: "", NEXT_PUBLIC_SUPABASE_URL: "  " }),
    ["NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_ANON_KEY"],
  );
});

test("a Vercel build with a missing variable fails and names it", () => {
  assert.throws(
    () => assertPublicEnvForDeploy({ VERCEL: "1", VERCEL_ENV: "preview", ...FULL, NEXT_PUBLIC_SUPABASE_URL: "" }),
    /preview environment: NEXT_PUBLIC_SUPABASE_URL\./,
  );
});

test("a complete Vercel build passes", () => {
  assert.doesNotThrow(() => assertPublicEnvForDeploy({ VERCEL: "1", VERCEL_ENV: "production", ...FULL }));
});

test("a build outside Vercel (local, CI) never fails, even with nothing set", () => {
  assert.doesNotThrow(() => assertPublicEnvForDeploy({}));
});
