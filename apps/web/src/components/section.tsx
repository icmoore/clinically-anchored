import type { ReactNode } from "react";

/** The patient page's boxes: a small uppercase heading above a bordered box. */
export function Section({
  title,
  children,
  box = true,
}: {
  title: string;
  children: ReactNode;
  /** False when the content brings its own box (for example a dashed empty-state one). */
  box?: boolean;
}) {
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">{title}</h2>
      {box ? <div className="rounded-lg border border-border bg-surface p-4">{children}</div> : children}
    </section>
  );
}
