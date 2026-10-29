export function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

export function clock(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Date only ("8 Sep"), for the contact card where the time of day is noise. */
export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** A time as the clinic's own clock shows it ("Sat, Oct 3, 7:00 AM EDT"), whatever zone the
 *  device is in: message windows open at a wall-clock time in the clinic's timezone. */
export function inZone(iso: string, timeZone: string | null): string {
  return new Date(iso).toLocaleString("en-CA", {
    timeZone: timeZone ?? "America/Toronto",
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

/** A time written plainly in the clinic's own zone, no zone name: "Wed, Oct 21, 7:00 a.m." For
 *  patient screens, where "EDT" is clutter. */
export function plainTime(iso: string, timeZone: string | null): string {
  const text = new Date(iso).toLocaleString("en-CA", {
    timeZone: timeZone ?? "America/Toronto",
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  // Keep "7:00" and "a.m." on one line.
  return text.replace(/\s+(a\.m\.|p\.m\.)/i, "\u00a0$1");
}

/** Just the day, plainly, in the clinic's zone: "Wed, Oct 21". */
export function plainDay(iso: string, timeZone: string | null): string {
  return new Date(iso).toLocaleDateString("en-CA", {
    timeZone: timeZone ?? "America/Toronto",
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

/** "Oct 21", for under a timeline marker. */
export function shortDay(iso: string, timeZone: string | null): string {
  return new Date(iso).toLocaleDateString("en-CA", {
    timeZone: timeZone ?? "America/Toronto",
    month: "short",
    day: "numeric",
  });
}

/** The line under a patient's name: the sign-in email first, then the phone. `contact` is the
 *  older free-text field (some rows hold an email there), so it only shows when it isn't an
 *  email, otherwise it would sit beside, or in front of, the address the clinic just changed. */
export function contactLine(patient: { email: string | null; contact: string | null }): string | null {
  const phone = patient.contact && !patient.contact.includes("@") ? patient.contact : null;
  return [patient.email, phone].filter(Boolean).join(" · ") || null;
}
