import Link from "next/link";
import { PATIENT_TEXT } from "@/lib/patient-strings";

const T = PATIENT_TEXT.siteLinks;

const navLink = "rounded px-1 py-1 text-sm text-muted hover:text-ink";

/** The public top bar, shared by the landing page and both sign-in pages so each
 *  always has a way back. The anchors are root-relative so they work from /login too. */
export function SiteNav({ current }: { current?: "login" | "sign-in" | "guide" }) {
  return (
    <header className="site-nav sticky top-0 z-10 border-b border-border bg-canvas/90 backdrop-blur print:hidden">
      <nav
        aria-label="Main"
        className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-5 gap-y-1 px-4 py-3"
      >
        {/* The two ways in, side by side where "Login" used to be. The patient one is the filled
            button and the care team one the outlined one, so a patient picks the right one. */}
        <div className="flex items-center gap-2">
          <Link
            href="/sign-in"
            aria-current={current === "sign-in" ? "page" : undefined}
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-on-primary hover:bg-primary-hover active:bg-primary-active"
          >
            {T.patientSignIn}
          </Link>
          <Link
            href="/login"
            aria-current={current === "login" ? "page" : undefined}
            className={`rounded-md border border-link px-3 py-1.5 text-sm font-medium text-link hover:border-link-hover hover:text-link-hover hover:bg-primary-tint ${
              current === "login" ? "bg-primary-tint" : ""
            }`}
          >
            {T.careTeamLogin}
          </Link>
        </div>
        <Link
          href="/guide"
          aria-current={current === "guide" ? "page" : undefined}
          className={`${navLink} ${current === "guide" ? "font-medium text-ink" : ""}`}
        >
          {T.patientGuide}
        </Link>
        <Link href="/" className="rounded text-sm font-semibold text-link hover:text-link-hover">
          Clinically Anchored
        </Link>
        <div className="ml-auto flex items-center gap-4">
          <Link href="/#how-it-works" className={navLink}>
            How it works
          </Link>
          <Link href="/#why-different" className={navLink}>
            Why it&rsquo;s different
          </Link>
        </div>
      </nav>
    </header>
  );
}
