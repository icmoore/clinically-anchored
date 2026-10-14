import { Suspense } from "react";
import { PatientHome } from "@/components/patient-home";

// The one link a patient is given for their whole post-op period (issued from the clinician's
// patient page). What it shows depends on where they are: check-in first, then messages.
export default function CarePage() {
  return (
    <Suspense fallback={<p className="p-6 text-muted">Loading...</p>}>
      <PatientHome />
    </Suspense>
  );
}
