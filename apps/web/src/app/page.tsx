// COPY: DRAFT -- all wording on this page is a first draft; rewrite it here.
import type { Metadata } from "next";
import Link from "next/link";
import { SignedInRedirect } from "@/components/signed-in-redirect";

export const metadata: Metadata = {
  title: "Clinically Anchored",
  description: "Structured post-op follow-up for surgical clinics, with the surgeon in control.",
};

// Refresh the static page daily so the footer year doesn't go stale.
export const revalidate = 86400;

const focus =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";
const buttonBase = `inline-flex items-center justify-center rounded-md px-5 py-2.5 text-sm font-medium ${focus}`;
const primaryButton = `${buttonBase} bg-primary text-primary-foreground hover:opacity-90`;
const secondaryButton = `${buttonBase} border border-border bg-background text-foreground hover:bg-muted`;
const navLink = `rounded px-1 py-1 text-sm text-muted-foreground hover:text-foreground ${focus}`;

const steps = [
  {
    title: "A short check-in",
    body: "The patient completes a short, procedure-specific check-in from a secure link. No account or app to install.",
  },
  {
    title: "Red flags first",
    body: "Red flags surface first, so the surgeon sees who needs attention before anyone else.",
  },
  {
    title: "Review and close out",
    body: "The surgeon reviews, replies if needed, and closes out the patient with one click.",
  },
];

const features = [
  {
    title: "Built to minimize contact",
    body: "Structured check-ins instead of an open inbox, with a safety net for acute problems.",
    icon: <path d="M4 5h16v11H8l-4 4V5z" />,
  },
  {
    title: "Surgeon-owned clinical content",
    body: "Questions and red-flag rules follow the surgeon’s own post-op protocol.",
    icon: <path d="M6 4h9l3 3v13H6V4zm3 8h6m-6 4h6" />,
  },
  {
    title: "AI that assists, never decides",
    body: "Drafts and summaries are reviewed and approved by the clinician; nothing is sent automatically.",
    icon: <path d="M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2L12 3z" />,
  },
  {
    title: "A signed record of every action",
    body: "A tamper-evident record, with the clinician’s approvals attributed and time-stamped.",
    icon: <path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3zm-3 9l2.5 2.5L15.5 10" />,
  },
];

export default function Home() {
  return (
    <div className="landing min-h-screen">
      <SignedInRedirect />

      <header className="sticky top-0 z-10 border-b border-border bg-background/90 backdrop-blur">
        <nav
          aria-label="Main"
          className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-5 gap-y-1 px-4 py-3"
        >
          <Link
            href="/login"
            className={`rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground hover:bg-muted ${focus}`}
          >
            Login
          </Link>
          <Link href="/" className={`rounded text-sm font-semibold text-foreground ${focus}`}>
            Clinically Anchored
          </Link>
          <div className="ml-auto flex items-center gap-4">
            <a href="#how-it-works" className={navLink}>
              How it works
            </a>
            <a href="#why-different" className={navLink}>
              Why it&rsquo;s different
            </a>
          </div>
        </nav>
      </header>

      <main>
        <section className="mx-auto grid max-w-5xl items-center gap-10 px-4 py-16 sm:py-24 md:grid-cols-2">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
              Post-op follow-up that puts the patients who need you first
            </h1>
            <p className="mt-4 text-lg text-muted-foreground">
              Structured check-ins from a secure link, red flags at the top, and replies you
              approve before anything is sent.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/login" className={primaryButton}>
                Login
              </Link>
              <a href="#how-it-works" className={secondaryButton}>
                How it works
              </a>
            </div>
          </div>

          {/* Decorative check-in card, plain CSS: no real data or product screenshot. */}
          <div aria-hidden="true" className="rounded-xl border border-border bg-muted p-5">
            <div className="rounded-lg border border-border bg-background p-4">
              <div className="flex items-center justify-between">
                <div className="h-3 w-28 rounded bg-border" />
                <span className="rounded-full bg-primary px-2 py-0.5 text-xs font-medium text-primary-foreground">
                  Red flag
                </span>
              </div>
              <div className="mt-4 space-y-2.5">
                <div className="h-2.5 w-full rounded bg-border" />
                <div className="h-2.5 w-5/6 rounded bg-border" />
                <div className="h-2.5 w-2/3 rounded bg-border" />
              </div>
            </div>
            <div className="mt-3 space-y-3 rounded-lg border border-border bg-background p-4">
              <div className="h-3 w-24 rounded bg-border" />
              <div className="h-2.5 w-3/4 rounded bg-border" />
            </div>
          </div>
        </section>

        <section
          id="how-it-works"
          aria-labelledby="how-heading"
          className="scroll-mt-20 border-y border-border bg-muted"
        >
          <div className="mx-auto max-w-5xl px-4 py-16">
            <h2 id="how-heading" className="text-2xl font-semibold tracking-tight">
              How it works
            </h2>
            <ol className="mt-8 grid gap-4 md:grid-cols-3">
              {steps.map((step, i) => (
                <li key={step.title} className="rounded-lg border border-border bg-background p-5">
                  <span
                    aria-hidden="true"
                    className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-sm font-semibold text-primary-foreground"
                  >
                    {i + 1}
                  </span>
                  <h3 className="mt-4 font-semibold">{step.title}</h3>
                  <p className="mt-2 text-sm text-muted-foreground">{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section
          id="why-different"
          aria-labelledby="why-heading"
          className="mx-auto max-w-5xl scroll-mt-20 px-4 py-16"
        >
          <h2 id="why-heading" className="text-2xl font-semibold tracking-tight">
            Why it&rsquo;s different
          </h2>
          <ul className="mt-8 grid gap-8 sm:grid-cols-2">
            {features.map((f) => (
              <li key={f.title} className="flex gap-4">
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.75"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="mt-0.5 h-6 w-6 shrink-0 text-accent"
                >
                  {f.icon}
                </svg>
                <div>
                  <h3 className="font-semibold">{f.title}</h3>
                  <p className="mt-1 text-sm text-muted-foreground">{f.body}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="cta-heading" className="border-t border-border bg-muted">
          <div className="mx-auto max-w-5xl px-4 py-14 text-center">
            <h2 id="cta-heading" className="text-2xl font-semibold tracking-tight">
              Clinician sign-in
            </h2>
            <p className="mt-2 text-muted-foreground">Sign in to review your patients.</p>
            <div className="mt-6">
              <Link href="/login" className={primaryButton}>
                Login
              </Link>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-2 px-4 py-6 text-sm text-muted-foreground">
          <span className="font-semibold text-foreground">Clinically Anchored</span>
          <span>&copy; {new Date().getFullYear()}</span>
        </div>
      </footer>
    </div>
  );
}
