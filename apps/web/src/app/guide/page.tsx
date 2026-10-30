import type { Metadata } from "next";
import Link from "next/link";
import { Callout, GuideSections, GuideToc } from "@/components/guide";
import { SiteNav } from "@/components/site-nav";
import { GUIDE_FACTS } from "@/lib/guide-data";
import { GUIDE_TEXT, SHOW_DRAFT_NOTICE, guideSections } from "@/lib/guide-strings";

// NOINDEX until the surgeon approves the wording: search engines are asked to skip this page and its
// links. To publish it, delete the `robots` line below and set SHOW_DRAFT_NOTICE to false in
// lib/guide-strings.ts. (The page stays reachable by link either way; noindex only asks search
// engines not to list it.)
export const metadata: Metadata = {
  title: "Patient guide",
  description: "How to sign in, do your check-in and write to your care team after surgery.",
  robots: { index: false, follow: false },
};

const T = GUIDE_TEXT;

export default function GuidePage() {
  const sections = guideSections(GUIDE_FACTS);
  return (
    <div className="landing">
      <a
        href="#guide-content"
        className="sr-only z-20 rounded-md bg-surface px-4 py-2 text-sm font-medium text-link focus:not-sr-only focus:absolute focus:left-4 focus:top-4 print:hidden"
      >
        {T.skipLink}
      </a>
      <SiteNav current="guide" />
      <main
        id="guide-content"
        tabIndex={-1}
        className="mx-auto w-full max-w-5xl px-4 py-10 outline-none print:max-w-none print:p-0 lg:grid lg:grid-cols-[14rem_minmax(0,1fr)] lg:gap-12 print:lg:block"
      >
        <GuideToc sections={sections} />
        <div className="max-w-3xl">
          <header className="border-b border-border pb-8">
            <p className="text-xs font-semibold uppercase tracking-widest text-link">{T.eyebrow}</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">{T.title}</h1>
            <p className="mt-3 text-lg text-muted">{T.subtitle}</p>
            {SHOW_DRAFT_NOTICE && <p className="mt-3 text-sm text-muted">{T.draftNotice}</p>}
          </header>
          <div className="mt-8">
            <Callout tone="flag" title={T.urgent.title}>
              {T.urgent.text}
            </Callout>
          </div>
          <div className="mt-10">
            <GuideSections sections={sections} />
          </div>
          <footer className="mt-14 border-t border-border pt-6 text-sm text-muted print:hidden">
            <p>{T.footer.help}</p>
            <p className="mt-2">
              <Link href="/sign-in" className="text-link underline hover:text-link-hover">
                {T.footer.signIn}
              </Link>
            </p>
          </footer>
        </div>
      </main>
    </div>
  );
}
