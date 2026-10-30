import Link from "next/link";
import { Fragment, type ReactNode } from "react";
import {
  EXAMPLE_NEXT_OPENS_AT,
  EXAMPLE_TIMEZONE,
  EXAMPLE_WINDOWS,
  GUIDE_FACTS,
  GUIDE_PERIODS,
} from "@/lib/guide-data";
import {
  GUIDE_CATEGORIES,
  GUIDE_LEGEND,
  GUIDE_TEXT,
  type GuideBlock,
  type GuideSection,
} from "@/lib/guide-strings";
import { PatientTimeline } from "./patient-timeline";

const T = GUIDE_TEXT;
const number = (i: number) => String(i + 1).padStart(2, "0");

/** A string with [link text](/path) in it, as text and links. Nothing else is interpreted. */
function inline(text: string): ReactNode {
  const parts: ReactNode[] = [];
  const re = /\[([^\]]+)\]\(([^)]+)\)/g;
  let last = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const href = m[2];
    const cls = "text-link underline hover:text-link-hover";
    parts.push(
      href.startsWith("/") ? (
        <Link key={m.index} href={href} className={cls}>
          {m[1]}
        </Link>
      ) : (
        <a key={m.index} href={href} className={cls}>
          {m[1]}
        </a>
      ),
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts.map((p, i) => <Fragment key={i}>{p}</Fragment>);
}

const TONE = {
  info: "border-info-border bg-info-bg text-info-text",
  watch: "border-watch-border bg-watch-bg text-watch-text",
  flag: "border-flag-border bg-flag-bg text-flag-text",
} as const;

export function Callout({
  tone,
  title,
  children,
}: {
  tone: keyof typeof TONE;
  title?: string;
  children: ReactNode;
}) {
  return (
    <div role="note" className={`my-4 break-inside-avoid rounded-lg border p-4 ${TONE[tone]}`}>
      {title && <p className="font-semibold">{title}</p>}
      <p className={title ? "mt-1" : ""}>{children}</p>
    </div>
  );
}

/** The sticky "On this page" list on a wide screen; the same list folded away on a phone. */
export function GuideToc({ sections }: { sections: GuideSection[] }) {
  const list = (
    <ol className="space-y-1 text-sm">
      {sections.map((s, i) => (
        <li key={s.id}>
          <a
            href={`#${s.id}`}
            className="flex gap-2 rounded px-2 py-1.5 text-link hover:bg-primary-tint hover:text-link-hover"
          >
            <span aria-hidden="true" className="font-mono text-muted">
              {number(i)}
            </span>
            {s.title}
          </a>
        </li>
      ))}
    </ol>
  );
  return (
    <>
      <nav aria-label={T.onThisPage} className="hidden print:lg:hidden lg:sticky lg:top-24 lg:block lg:self-start">
        <p className="mb-2 px-2 text-xs font-semibold uppercase tracking-wide text-muted">{T.onThisPage}</p>
        {list}
      </nav>
      <details className="mb-8 rounded-lg border border-border bg-surface print:hidden lg:hidden">
        <summary className="cursor-pointer px-4 py-3 text-sm font-semibold text-ink">{T.onThisPage}</summary>
        <nav aria-label={T.onThisPage} className="border-t border-border p-2">
          {list}
        </nav>
      </details>
    </>
  );
}

function Heading({ id, level, children }: { id: string; level: 2 | 3; children: ReactNode }) {
  const Tag = level === 2 ? "h2" : "h3";
  return (
    <Tag
      id={id}
      className={`group scroll-mt-24 break-after-avoid ${
        level === 2 ? "text-2xl font-semibold tracking-tight" : "mt-8 text-lg font-semibold"
      }`}
    >
      {children}{" "}
      <a
        href={`#${id}`}
        aria-label={T.linkToSection(String(children))}
        className="ml-1 text-link opacity-0 hover:text-link-hover focus:opacity-100 group-hover:opacity-100 print:hidden"
      >
        <span aria-hidden="true">#</span>
      </a>
    </Tag>
  );
}

function PeriodsTable() {
  const P = T.periodsTable;
  return (
    <div className="my-4 break-inside-avoid overflow-x-auto rounded-lg border border-border bg-surface">
      <table className="w-full text-left text-sm">
        <caption className="px-4 pb-1 pt-3 text-left text-xs font-semibold uppercase tracking-wide text-muted">
          {P.caption}
        </caption>
        <thead>
          <tr className="border-b border-border text-muted">
            <th scope="col" className="px-4 py-2 font-medium">
              {P.period}
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              {P.opens}
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              {P.closes}
            </th>
          </tr>
        </thead>
        <tbody>
          {GUIDE_PERIODS.map((p) => (
            <tr key={p.number} className="border-b border-border last:border-0">
              <th scope="row" className="px-4 py-2 font-medium text-ink">
                {p.number}
              </th>
              <td className="px-4 py-2">{P.day(p.opensDay)}</td>
              <td className="px-4 py-2">{P.day(p.closesDay)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Block({ block }: { block: GuideBlock }) {
  switch (block.kind) {
    case "p":
      return <p className="mt-3 text-base leading-relaxed">{inline(block.text)}</p>;
    case "h3":
      return <Heading id={block.id} level={3}>{block.text}</Heading>;
    case "ul":
      return (
        <ul className="mt-3 list-disc space-y-1.5 pl-6 leading-relaxed marker:text-muted">
          {block.items.map((t) => (
            <li key={t}>{inline(t)}</li>
          ))}
        </ul>
      );
    case "ol":
      return (
        <ol className="mt-3 list-decimal space-y-1.5 pl-6 leading-relaxed marker:text-muted">
          {block.items.map((t) => (
            <li key={t}>{inline(t)}</li>
          ))}
        </ol>
      );
    case "callout":
      return (
        <Callout tone={block.tone} title={block.title}>
          {inline(block.text)}
        </Callout>
      );
    case "periods":
      return <PeriodsTable />;
    case "categories":
      return (
        <ul className="mt-3 list-disc space-y-1.5 pl-6 leading-relaxed marker:text-muted">
          {Object.values(GUIDE_CATEGORIES).map((label) => (
            <li key={label}>{label}</li>
          ))}
        </ul>
      );
    case "timeline":
      return (
        <figure className="my-4 break-inside-avoid rounded-lg border border-border bg-surface p-3">
          <PatientTimeline
            windows={EXAMPLE_WINDOWS}
            timeZone={EXAMPLE_TIMEZONE}
            nextOpensAt={EXAMPLE_NEXT_OPENS_AT}
          />
          <figcaption className="mt-3 text-xs text-muted">{T.exampleCaption}</figcaption>
        </figure>
      );
    case "legend":
      return (
        <ul className="mt-3 space-y-1.5 leading-relaxed">
          {GUIDE_LEGEND.map((l) => (
            <li key={l.mark} className="flex gap-3">
              <span aria-hidden="true" className="w-5 shrink-0 text-center font-medium text-ink">
                {l.mark}
              </span>
              {l.text}
            </li>
          ))}
        </ul>
      );
  }
}

export function GuideSections({ sections }: { sections: GuideSection[] }) {
  return (
    <>
      {sections.map((s, i) => (
        <section key={s.id} aria-labelledby={s.id} className="mt-14 first:mt-0">
          <p aria-hidden="true" className="font-mono text-sm text-muted">
            {number(i)}
          </p>
          <Heading id={s.id} level={2}>
            {s.title}
          </Heading>
          {s.blocks.map((b, j) => (
            <Block key={j} block={b} />
          ))}
        </section>
      ))}
    </>
  );
}

// Re-exported so the page can show the schedule's facts without importing the data module twice.
export { GUIDE_FACTS };
