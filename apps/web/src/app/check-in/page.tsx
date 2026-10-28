import { Suspense } from "react";
import { CheckInPageContent } from "./check-in-form";

export default function CheckInPage() {
  return (
    <Suspense fallback={<p className="p-6 text-muted">Loading...</p>}>
      <CheckInPageContent />
    </Suspense>
  );
}
