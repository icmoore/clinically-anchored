import type { Metadata } from "next";
import { LegalPage } from "@/components/legal-document";
import { readLegalDocument } from "@/lib/legal-docs";

// NOINDEX until the documents are final: delete the `robots` line when they are approved.
export const metadata: Metadata = {
  title: "Privacy Notice",
  description: "How Clinically Anchored handles your information.",
  robots: { index: false, follow: false },
};

export default async function PrivacyPage() {
  const { text } = await readLegalDocument("privacy");
  return <LegalPage kind="privacy" text={text} />;
}
