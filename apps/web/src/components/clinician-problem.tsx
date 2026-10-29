import type { ClinicianState } from "@/lib/use-clinician";

/** What a clinician page shows when it could not get the signed-in clinician: the message, and a
 *  Retry button when starting over might help (the load stalled). */
export function ClinicianProblem({
  problem,
}: {
  problem: Extract<ClinicianState, { status: "error" }>;
}) {
  return (
    <div className="p-6">
      <p role="alert" className="text-flag-text">
        {problem.message}
      </p>
      {problem.retry && (
        <button
          onClick={problem.retry}
          className="mt-3 rounded-md border border-link bg-surface text-link enabled:hover:border-link-hover enabled:hover:text-link-hover enabled:hover:bg-primary-tint px-4 py-2 text-sm font-medium"
        >
          Retry
        </button>
      )}
    </div>
  );
}
