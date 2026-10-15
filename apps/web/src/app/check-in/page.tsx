import { Suspense } from "react";
import { CheckInScreen } from "./check-in-form";

export default function CheckInPage() {
  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col px-6 py-12">
      <h1 className="mb-6 text-xl font-semibold text-ink">Post-op check-in</h1>
      <Suspense fallback={<p className="text-muted">Loading...</p>}>
        <CheckInScreen />
      </Suspense>
    </div>
  );
}
