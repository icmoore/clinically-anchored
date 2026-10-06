import Link from "next/link";

// Focus ring shared with the landing page's buttons (colors come from the .landing tokens).
export const focus =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

const navLink = `rounded px-1 py-1 text-sm text-muted-foreground hover:text-foreground ${focus}`;

/** The public top bar, shared by the landing page and /login so the sign-in page
 *  always has a way back. The anchors are root-relative so they work from /login too. */
export function SiteNav({ current }: { current?: "login" }) {
  return (
    <header className="site-nav sticky top-0 z-10 border-b border-border bg-background/90 backdrop-blur">
      <nav
        aria-label="Main"
        className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-5 gap-y-1 px-4 py-3"
      >
        <Link
          href="/login"
          aria-current={current === "login" ? "page" : undefined}
          className={`rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground hover:bg-muted ${focus} ${
            current === "login" ? "bg-muted" : ""
          }`}
        >
          Login
        </Link>
        <Link href="/" className={`rounded text-sm font-semibold text-foreground ${focus}`}>
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
