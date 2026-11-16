// Run with `pnpm test` (Node's built-in test runner; no extra dependencies).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MIN_PASSWORD_LENGTH,
  changePassword,
  validatePasswordForm,
  type PasswordAuth,
} from "./change-password.ts";

const GOOD = { current: "old-password-123", next: "a-brand-new-passphrase", confirm: "a-brand-new-passphrase" };

function fakeAuth(over: Partial<PasswordAuth> = {}) {
  const calls: string[] = [];
  const auth: PasswordAuth = {
    signInWithPassword: async ({ email, password }) => {
      calls.push(`signIn:${email}:${password}`);
      return { error: null };
    },
    updateUser: async ({ password }) => {
      calls.push(`update:${password}`);
      return { error: null };
    },
    ...over,
  };
  return { auth, calls };
}

test("the minimum is 12 characters", () => {
  assert.equal(MIN_PASSWORD_LENGTH, 12);
  const eleven = "a".repeat(11);
  assert.match(validatePasswordForm({ ...GOOD, next: eleven, confirm: eleven })!, /at least 12/);
  const twelve = "a".repeat(12);
  assert.equal(validatePasswordForm({ ...GOOD, next: twelve, confirm: twelve }), null);
});

test("the form needs the current password, matching new passwords, and a different one", () => {
  assert.match(validatePasswordForm({ ...GOOD, current: "" })!, /current password/);
  assert.match(validatePasswordForm({ ...GOOD, confirm: "something-else-entirely" })!, /don't match/);
  const same = "the-very-same-password";
  assert.match(validatePasswordForm({ current: same, next: same, confirm: same })!, /different/);
  assert.equal(validatePasswordForm(GOOD), null);
});

test("an invalid form touches nothing", async () => {
  const { auth, calls } = fakeAuth();
  const r = await changePassword(auth, "a@b.c", { ...GOOD, next: "short", confirm: "short" }, async () => {
    calls.push("record");
  });
  assert.equal(r.ok, false);
  assert.deepEqual(calls, []);
});

test("it re-authenticates with the current password, then sets the new one, then records it", async () => {
  const { auth, calls } = fakeAuth();
  const r = await changePassword(auth, "doc@example.com", GOOD, async () => {
    calls.push("record");
  });
  assert.deepEqual(r, { ok: true, recorded: true });
  assert.deepEqual(calls, [
    "signIn:doc@example.com:old-password-123",
    "update:a-brand-new-passphrase",
    "record",
  ]);
});

test("a wrong current password stops before anything is changed or recorded", async () => {
  const { auth, calls } = fakeAuth({
    signInWithPassword: async () => ({ error: { status: 400, message: "Invalid login credentials" } }),
  });
  const r = await changePassword(auth, "doc@example.com", GOOD, async () => {
    calls.push("record");
  });
  assert.deepEqual(r, { ok: false, message: "That isn't your current password." });
  assert.deepEqual(calls, []);
});

test("another sign-in failure shows its real reason and still changes nothing", async () => {
  const { auth, calls } = fakeAuth({
    signInWithPassword: async () => ({ error: { status: 429, message: "Too many requests" } }),
  });
  const r = await changePassword(auth, "doc@example.com", GOOD, async () => {
    calls.push("record");
  });
  assert.equal(r.ok, false);
  assert.match((r as { message: string }).message, /Too many requests/);
  assert.deepEqual(calls, []);
});

test("if Supabase refuses the new password nothing is recorded", async () => {
  const calls: string[] = [];
  const { auth } = fakeAuth({
    updateUser: async () => ({ error: { message: "Password is too weak" } }),
  });
  const r = await changePassword(auth, "doc@example.com", GOOD, async () => {
    calls.push("record");
  });
  assert.equal(r.ok, false);
  assert.match((r as { message: string }).message, /not changed: Password is too weak/);
  assert.deepEqual(calls, []);
});

test("a failed audit record is reported, not hidden, and the change stands", async () => {
  const { auth } = fakeAuth();
  const r = await changePassword(auth, "doc@example.com", GOOD, async () => {
    throw new Error("api down");
  });
  assert.deepEqual(r, { ok: true, recorded: false });
});

test("the api call carries no password: recordChange takes no arguments", async () => {
  const { auth } = fakeAuth();
  let args: unknown[] = ["unset"];
  await changePassword(auth, "doc@example.com", GOOD, async (...a: unknown[]) => {
    args = a;
  });
  assert.deepEqual(args, []);
});
