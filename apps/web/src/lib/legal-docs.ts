import { readFile } from "node:fs/promises";
import path from "node:path";
import { LEGAL, LegalKind } from "./legal";

// Server only (the /terms and /privacy pages): reads the current document's markdown from
// content/legal/. The pages are built once, so the file is read at build time.

export async function readLegalDocument(kind: LegalKind): Promise<{ version: string; text: string }> {
  const { version, file } = LEGAL[kind];
  const text = await readFile(path.join(process.cwd(), "content", "legal", file), "utf8");
  return { version, text: text.replace(/\r\n/g, "\n") };
}
