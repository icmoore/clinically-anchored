import { Suspense } from "react";
import { PatientHome } from "@/components/patient-home";

// The patient's home is the same screen as /care (older invites and bookmarks point here).
export default function MessagesPage() {
  return (
    <Suspense fallback={<p className="p-6 text-muted">Loading...</p>}>
      <PatientHome />
    </Suspense>
  );
}
