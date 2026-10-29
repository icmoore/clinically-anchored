// Shared by the patient sign-in screens: the one button, the one input, the one error line.
import type { ReactNode } from "react";

export const buttonClass =
  "w-full rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2.5 text-base font-medium text-on-primary disabled:opacity-60";
export const linkButtonClass = "text-sm text-link underline hover:text-link-hover disabled:opacity-60";
export const inputClass =
  "mt-1 block w-full rounded-md border border-border-strong bg-surface px-3 py-2 text-base text-ink";

/** A plain error line that screen readers announce. */
export function FormError({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="mt-3 text-sm text-flag-text">
      {children}
    </p>
  );
}
