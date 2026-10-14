import { Suspense } from "react";
import { PatientHome } from "@/components/patient-home";

// Older links point here; the patient's home is the same screen as /care.
export default function MessagesPage() {
  return (
    <Suspense fallback={<p className="p-6 text-muted">Loading...</p>}>
      <PatientHome />
    </Suspense>
  );
}
