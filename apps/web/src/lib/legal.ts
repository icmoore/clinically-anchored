import config from "../../content/legal/config.json";

// The current Terms, Privacy Notice and consent wording: what version each is, the two consent
// sentences, and the effective date. The one place to change them is content/legal/config.json
// (see content/legal/README.md); `scripts/sync_legal` then updates the api's copy, which is how the
// api knows what "current" is. This file only reads the config. Everything a patient reads on the
// "Before you start" screen comes from here through lib/patient-strings.ts.

export type LegalKind = "terms" | "privacy";
export type ConsentKey = "messaging" | "ai_assisted_replies";

export const LEGAL = {
  effectiveDate: config.effective_date,
  terms: config.terms,
  privacy: config.privacy,
  consent: config.consent,
} as const;

/** The consent keys, in the order the screen shows them (and the order the api requires them). */
export const CONSENT_KEYS: ConsentKey[] = ["messaging", "ai_assisted_replies"];

/** "<key>:<version>" for each consent sentence, ordered by key, joined with ";": the same string
 *  the api builds (scripts/sync_legal). Sent with an answer so the api can tell which wording the
 *  screen showed. */
export function consentVersion(): string {
  return Object.keys(LEGAL.consent)
    .sort()
    .map((key) => `${key}:${LEGAL.consent[key as ConsentKey].version}`)
    .join(";");
}

/** The consent sentences as shown, ordered by key and joined with a newline: what is hashed. */
export function consentText(): string {
  return Object.keys(LEGAL.consent)
    .sort()
    .map((key) => LEGAL.consent[key as ConsentKey].text)
    .join("\n");
}

/** SHA-256 of a string (UTF-8), as lower-case hex. */
export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** "December 1, 2026" for an ISO date, in English, without moving it across a time zone. */
export function plainDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-CA", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}
