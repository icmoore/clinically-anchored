import Link from "next/link";

const navLink = "rounded px-1 py-1 text-sm text-muted hover:text-ink";

/** The public top bar, shared by the landing page and /login so the sign-in page
 *  always has a way back. The anchors are root-relative so they work from /login too. */
export function SiteNav({ current }: { current?: "login" }) {
  return (
    <header className="site-nav sticky top-0 z-10 border-b border-border bg-canvas/90 backdrop-blur">
      <nav
        aria-label="Main"
        className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-5 gap-y-1 px-4 py-3"
      >
        <Link
          href="/login"
          aria-current={current === "login" ? "page" : undefined}
          className={`rounded-md border border-primary px-3 py-1.5 text-sm font-medium text-primary hover:bg-primary-tint ${
            current === "login" ? "bg-primary-tint" : ""
          }`}
        >
          Login
        </Link>
        <Link href="/" className="rounded text-sm font-semibold text-primary">
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
