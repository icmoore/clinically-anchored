import Link from "next/link";
import { PATIENT_TEXT } from "@/lib/patient-strings";

const T = PATIENT_TEXT.siteLinks;

const navLink = "rounded px-1 py-1 text-sm text-muted hover:text-ink";

/** The public top bar, shared by the landing page and both sign-in pages so each
 *  always has a way back. The anchors are root-relative so they work from /login too. */
export function SiteNav({ current }: { current?: "guide" }) {
  return (
    <header className="site-nav sticky top-0 z-10 border-b border-border bg-canvas/90 backdrop-blur print:hidden">
      <nav
        aria-label="Main"
        className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-5 gap-y-1 px-4 py-3"
      >
        <Link href="/" className="rounded text-sm font-semibold text-link hover:text-link-hover">
          Clinically Anchored
        </Link>
        <div className="ml-auto flex items-center gap-4">
          <Link
            href="/guide"
            aria-current={current === "guide" ? "page" : undefined}
            className={`${navLink} ${current === "guide" ? "text-ink" : ""}`}
          >
            {T.patientGuide}
          </Link>
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
