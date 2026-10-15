// Which clinic a user with more than one works in. The choice is remembered per user in this
// browser only (it is a convenience, not a permission: the api checks membership on every
// request). Storage can be missing, blocked or full (private windows, cleared site data), so every
// access is wrapped and the first clinic is the fallback.
import type { Clinic } from "./clinician-api";

const key = (userId: string) => `ca.clinic.${userId}`;

/** The remembered clinic if the user still belongs to it, otherwise the first one /me returned. */
export function chooseClinic(clinics: Clinic[], userId: string): Clinic {
  let stored: string | null = null;
  try {
    stored = window.localStorage.getItem(key(userId));
  } catch {
    // Storage unavailable: fall through to the first clinic.
  }
  return clinics.find((c) => c.id === stored) ?? clinics[0];
}

export function rememberClinic(userId: string, clinicId: string): void {
  try {
    window.localStorage.setItem(key(userId), clinicId);
  } catch {
    // Not remembered: the next visit opens the first clinic again.
  }
}

/** Option labels for the switcher: the name, plus the role only when two names are the same. */
export function clinicLabels(clinics: Clinic[]): Record<string, string> {
  const counts = new Map<string, number>();
  for (const c of clinics) counts.set(c.name, (counts.get(c.name) ?? 0) + 1);
  return Object.fromEntries(
    clinics.map((c) => [c.id, (counts.get(c.name) ?? 0) > 1 ? `${c.name} (${c.role})` : c.name]),
  );
}
