import type { ReactNode } from "react";
import { SiteNav } from "./site-nav";

/** The page both sign-in pages (/sign-in for patients, /login for the care team) sit in, so they
 *  mirror each other: the site's top bar, then one centred column (the brand name, a grey line
 *  saying which sign-in this is, then the form), and an optional footer under it (the patient
 *  page's urgent notice). */
export function AuthPage({
  subtitle,
  footer,
  children,
}: {
  subtitle: string;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteNav />
      <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-6 py-12">
        <h1 className="text-xl font-semibold text-ink">Clinically Anchored</h1>
        <p className="mb-6 mt-1 text-sm text-muted">{subtitle}</p>
        {children}
      </div>
      {footer && <div className="mx-auto w-full max-w-sm px-6 pb-6">{footer}</div>}
    </div>
  );
}
