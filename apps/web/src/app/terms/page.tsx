import type { Metadata } from "next";
import { LegalPage } from "@/components/legal-document";
import { readLegalDocument } from "@/lib/legal-docs";

// NOINDEX until the documents are final: delete the `robots` line when they are approved.
export const metadata: Metadata = {
  title: "Terms of Use",
  description: "The terms for using Clinically Anchored to write to your care team after surgery.",
  robots: { index: false, follow: false },
};

export default async function TermsPage() {
  const { text } = await readLegalDocument("terms");
  return <LegalPage kind="terms" text={text} />;
}
