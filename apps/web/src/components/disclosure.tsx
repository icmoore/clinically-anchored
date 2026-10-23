import type { ReactNode } from "react";

/** A folded box: one line that opens to show its content. It is a native <details>, so a citation
 *  can open it by setting `closest("details").open = true` (the patient page's jumpTo does), and
 *  the open state is reported back so the parent always knows it. */
export function Disclosure({
  summary,
  open,
  onOpenChange,
  className = "mt-3",
  children,
}: {
  summary: ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <details
      open={open}
      onToggle={(e) => onOpenChange(e.currentTarget.open)}
      className={`rounded-lg border border-border bg-surface ${className}`}
    >
      <summary className="cursor-pointer px-4 py-3 text-sm text-muted">{summary}</summary>
      <div className="border-t border-border p-4">{children}</div>
    </details>
  );
}
