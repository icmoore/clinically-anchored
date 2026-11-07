import Link from "next/link";
import type { ReactNode } from "react";
import { LEGAL, LegalKind, plainDate } from "@/lib/legal";
import { PATIENT_TEXT } from "@/lib/patient-strings";
import { SiteNav } from "./site-nav";

// A small markdown reader for the legal documents (content/legal/*.md): headings, paragraphs,
// bullet and numbered lists, quotes, rules, **bold**, *italic* and [links](https://...). It builds
// React elements and never inserts HTML, so a document cannot add markup. [Square-bracket
// placeholders] are shown as they are. Anything it does not know (tables, images) shows as plain
// text, which is the cue to extend it.

const LINK = /^(https?:\/\/|mailto:|tel:|\/)/;

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /\*\*(.+?)\*\*|\*(.+?)\*|\[([^\]]+)\]\(([^)\s]+)\)/g;
  let last = 0;
  let n = 0;
  for (let m = pattern.exec(text); m; m = pattern.exec(text)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = `${keyBase}-${n++}`;
    if (m[1] !== undefined) out.push(<strong key={key}>{inline(m[1], key)}</strong>);
    else if (m[2] !== undefined) out.push(<em key={key}>{inline(m[2], key)}</em>);
    else if (LINK.test(m[4]))
      out.push(
        <a key={key} href={m[4]} className="text-link underline hover:text-link-hover">
          {m[3]}
        </a>,
      );
    else out.push(m[0]); // a link we do not trust (javascript:, data:) stays as text
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function LegalMarkdown({ source }: { source: string }) {
  const lines = source.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;
  const next = () => `b${key++}`;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const k = next();
      const body = inline(heading[2], k);
      if (level === 1) blocks.push(<h2 key={k} className="mt-8 text-2xl font-semibold text-ink">{body}</h2>);
      else if (level === 2) blocks.push(<h3 key={k} className="mt-8 text-xl font-semibold text-ink">{body}</h3>);
      else blocks.push(<h4 key={k} className="mt-6 text-base font-semibold text-ink">{body}</h4>);
      i++;
    } else if (/^(-{3,}|\*{3,})\s*$/.test(line)) {
      blocks.push(<hr key={next()} className="my-6 border-border" />);
      i++;
    } else if (/^\s*[-*]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: string[] = [];
      while (i < lines.length && (ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*]\s+/).test(lines[i])) {
        let item = lines[i].replace(ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*]\s+/, "");
        i++;
        // A wrapped item continues on indented lines.
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+[.)])\s/.test(lines[i])) {
          item += " " + lines[i].trim();
          i++;
        }
        items.push(item);
      }
      const k = next();
      const lis = items.map((t, j) => <li key={j}>{inline(t, `${k}-${j}`)}</li>);
      blocks.push(
        ordered ? (
          <ol key={k} className="mt-3 list-decimal space-y-1 pl-6 text-ink">{lis}</ol>
        ) : (
          <ul key={k} className="mt-3 list-disc space-y-1 pl-6 text-ink">{lis}</ul>
        ),
      );
    } else if (line.startsWith(">")) {
      const quoted: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) quoted.push(lines[i++].replace(/^>\s?/, ""));
      const k = next();
      blocks.push(
        <blockquote key={k} className="mt-4 border-l-4 border-border-strong pl-4 text-muted">
          {inline(quoted.join(" "), k)}
        </blockquote>,
      );
    } else {
      const para: string[] = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^(#{1,4}\s|>|\s*[-*]\s+|\s*\d+[.)]\s+|-{3,}\s*$)/.test(lines[i])
      )
        para.push(lines[i++].trim());
      const k = next();
      blocks.push(<p key={k} className="mt-3 text-ink">{inline(para.join(" "), k)}</p>);
    }
  }
  return <>{blocks}</>;
}

/** /terms and /privacy: the current document, with its version and effective date. Public (a patient
 *  opens it from the "Before you start" screen, before they have agreed to anything). */
export function LegalPage({ kind, text }: { kind: LegalKind; text: string }) {
  const T = PATIENT_TEXT.legal;
  // The page has its own title; a document's own "# Title" line would show it twice.
  const body = text.replace(/^# .*\n+/, "");
  const { version } = LEGAL[kind];
  const other = kind === "terms" ? { href: "/privacy", label: T.privacyTitle } : { href: "/terms", label: T.termsTitle };
  return (
    <div className="landing">
      <SiteNav />
      <main className="mx-auto w-full max-w-3xl px-4 py-10">
        <header className="border-b border-border pb-6">
          <h1 className="text-3xl font-semibold tracking-tight">
            {kind === "terms" ? T.termsTitle : T.privacyTitle}
          </h1>
          <p className="mt-2 text-sm text-muted">{T.versionLine(version, plainDate(LEGAL.effectiveDate))}</p>
        </header>
        <article>
          <LegalMarkdown source={body} />
        </article>
        <p className="mt-10 border-t border-border pt-4 text-sm text-muted">
          <Link href={other.href} className="text-link underline hover:text-link-hover">
            {other.label}
          </Link>
        </p>
      </main>
    </div>
  );
}
