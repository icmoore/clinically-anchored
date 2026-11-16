// The clinician "Change password" flow, kept free of React and of the Supabase import so it can be
// tested on its own (change-password.test.ts). The page wires in the real client.
//
// The order matters: (1) check the form, (2) prove the current password by signing in with it again,
// (3) set the new one, (4) tell the api so it can write the audit event. No password goes to the api,
// and none is logged or kept: they live in the form's state and in these calls only.

export const MIN_PASSWORD_LENGTH = 12;

export interface PasswordForm {
  current: string;
  next: string;
  confirm: string;
}

/** What is wrong with the form, in words for the person, or null when it can be submitted. */
export function validatePasswordForm({ current, next, confirm }: PasswordForm): string | null {
  if (!current) return "Enter your current password.";
  if (next.length < MIN_PASSWORD_LENGTH) {
    return `The new password needs at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (next !== confirm) return "The two new passwords don't match.";
  if (next === current) return "Choose a password different from your current one.";
  return null;
}

/** The parts of the Supabase auth client this flow uses (and nothing else of it). */
export interface PasswordAuth {
  signInWithPassword(c: { email: string; password: string }): Promise<{
    error: { status?: number; message: string } | null;
  }>;
  updateUser(a: { password: string }): Promise<{ error: { message: string } | null }>;
}

export type ChangeResult =
  | { ok: true; recorded: boolean } // recorded: false = changed, but the audit record failed
  | { ok: false; message: string };

export async function changePassword(
  auth: PasswordAuth,
  email: string,
  form: PasswordForm,
  recordChange: () => Promise<void>,
): Promise<ChangeResult> {
  const invalid = validatePasswordForm(form);
  if (invalid) return { ok: false, message: invalid };

  const check = await auth.signInWithPassword({ email, password: form.current });
  if (check.error) {
    const wrong = check.error.status === 400 && /invalid login credentials/i.test(check.error.message);
    return {
      ok: false,
      message: wrong
        ? "That isn't your current password."
        : `Couldn't check your current password: ${check.error.message || "couldn't reach the sign-in service"}`,
    };
  }

  const update = await auth.updateUser({ password: form.next });
  if (update.error) {
    return { ok: false, message: `Your password was not changed: ${update.error.message}` };
  }

  try {
    await recordChange();
  } catch {
    return { ok: true, recorded: false };
  }
  return { ok: true, recorded: true };
}
