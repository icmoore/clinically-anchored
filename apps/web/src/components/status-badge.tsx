import type { ReactNode } from "react";

export type StatusVariant = "flag" | "watch" | "clear" | "info" | "ai";

// Each status is text on background, with a border and a solid dot (docs/planning/color-schema.md).
// Full class names are spelled out so Tailwind can see them.
const STYLES: Record<StatusVariant, { badge: string; dot: string }> = {
  flag: { badge: "border-flag-border bg-flag-bg text-flag-text", dot: "bg-flag-solid" },
  watch: { badge: "border-watch-border bg-watch-bg text-watch-text", dot: "bg-watch-solid" },
  clear: { badge: "border-clear-border bg-clear-bg text-clear-text", dot: "bg-clear-solid" },
  info: { badge: "border-info-border bg-info-bg text-info-text", dot: "bg-info-solid" },
  ai: { badge: "border-ai-border bg-ai-bg text-ai-text", dot: "bg-ai-solid" },
};

/** The one badge for clinical status (flag / watch / clear / info) and for AI-generated
 *  content (ai). It always carries its label, so status is never shown by color alone. */
export function StatusBadge({
  variant,
  children,
}: {
  variant: StatusVariant;
  children: ReactNode;
}) {
  const { badge, dot } = STYLES[variant];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${badge}`}
    >
      <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
      {children}
    </span>
  );
}
