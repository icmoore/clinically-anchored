"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api";
import { Episode, IssuedLink, issueLink, resetLink } from "@/lib/clinician-api";
import { primary, secondary } from "./buttons";
import { Section } from "./section";

/** The patient's one link: copy it, or reset it. Once the care period has ended (discharged, or
 *  the schedule ran out) the link no longer works, so there is nothing to copy or reset. */
export function PatientLinksSection({
  clinicId,
  episode,
}: {
  clinicId: string;
  episode: Episode | null;
}) {
  if (episode === null) {
    return (
      <Section title="Patient links" box={false}>
        <div className="rounded-lg border border-dashed border-border-strong p-4 text-sm text-muted">
          This patient has no care period yet.
        </div>
      </Section>
    );
  }
  return (
    <Section title="Patient links">
      {episode.state.phase === "closed" ? (
        <p className="text-sm text-muted">
          {episode.state.close_reason === "discharged"
            ? "This patient was discharged."
            : "The care period has ended."}{" "}
          {linkStillShows(episode)
            ? "Their link still opens for a limited time, but only to show the end of the care period and the request for more follow-up (or the answer to it)."
            : "The patient\u2019s link no longer works."}
        </p>
      ) : (
        <LinkControls clinicId={clinicId} episodeId={episode.id} />
      )}
    </Section>
  );
}

/** The ended screen (an episode that ran out and can still be asked about) keeps the old link open
 *  to that one thing. The api decides the dates; this only words it. */
function linkStillShows(episode: Episode): boolean {
  if (episode.state.close_reason !== "schedule_complete") return false;
  const ext = episode.extension;
  if (!ext) return false;
  const status = ext.request?.status;
  return status === "pending" || status === "declined" || ext.can_request;
}

function LinkControls({ clinicId, episodeId }: { clinicId: string; episodeId: string }) {
  const [link, setLink] = useState<IssuedLink | null>(null);
  const [busy, setBusy] = useState<"copy" | "reset" | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(kind: "copy" | "reset") {
    setBusy(kind);
    setError(null);
    setCopied(false);
    try {
      const issued = kind === "copy" ? await issueLink(clinicId, episodeId) : await resetLink(clinicId, episodeId);
      setLink(issued);
      setConfirmReset(false);
      try {
        await navigator.clipboard.writeText(issued.url);
        setCopied(true);
      } catch {
        // Clipboard can be blocked (e.g. non-HTTPS); the URL is shown below to copy by hand.
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create that link.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <p className="text-sm text-muted">
        One link for the whole care period: check-in, then messages. Text or email it to the patient
        yourself. It stops working when the care period ends, or if you reset it.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button onClick={() => run("copy")} disabled={busy !== null} className={secondary}>
          Copy patient link
        </button>
        {!confirmReset && (
          <button onClick={() => setConfirmReset(true)} disabled={busy !== null} className={secondary}>
            Reset link...
          </button>
        )}
      </div>
      {confirmReset && (
        <div className="mt-2 rounded-md border border-watch-border bg-watch-bg p-3 text-sm text-watch-text">
          <p>The current link stops working at once. You will need to send the patient the new one.</p>
          <div className="mt-2 flex gap-2">
            <button onClick={() => run("reset")} disabled={busy !== null} className={primary}>
              {busy === "reset" ? "Resetting..." : "Reset and copy new link"}
            </button>
            <button onClick={() => setConfirmReset(false)} disabled={busy !== null} className={secondary}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-flag-text">{error}</p>}
      {link && (
        <div className="mt-2 space-y-1">
          <p className="text-xs text-muted">{copied ? "Copied. " : "Copy this link: "}</p>
          <input
            readOnly
            value={link.url}
            aria-label="Patient link"
            onFocus={(e) => e.currentTarget.select()}
            className="w-full rounded-md border border-border-strong bg-surface-subtle px-2 py-1.5 font-mono text-xs"
          />
        </div>
      )}
    </div>
  );
}
