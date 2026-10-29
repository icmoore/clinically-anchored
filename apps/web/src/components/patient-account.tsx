"use client";

import { useCallback, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  Account,
  FallbackLink,
  createFallbackLink,
  getAccount,
  inviteAccount,
  reinviteAccount,
  resendInvite,
  setPatientEmail,
  turnOffAccess,
} from "@/lib/clinician-api";
import { clock } from "@/lib/format";
import { usePolling } from "@/lib/use-polling";
import { danger, primary, secondary } from "./buttons";
import { Section } from "./section";
import { StatusBadge, StatusVariant } from "./status-badge";

const ADMIN_ROLES = ["owner", "clinician"];
const REFRESH_MS = 10000;

/** The patient's sign-in account: whether they are invited, active, locked or turned off, the email
 *  the invite goes to, and what the clinic can do about it. Any member can invite and resend; turning
 *  access off, re-inviting and a single-use link are for an owner or clinician (the api checks too). */
export function PatientAccountSection({
  clinicId,
  patientId,
  role,
  onChanged,
}: {
  clinicId: string;
  patientId: string;
  role: string;
  onChanged?: () => void; // the page's own copy of the patient (name, email) is now stale
}) {
  const fetchAccount = useCallback(() => getAccount(clinicId, patientId), [clinicId, patientId]);
  const { data: account, failed, reload } = usePolling(fetchAccount, REFRESH_MS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmOff, setConfirmOff] = useState(false);
  const canAdmin = ADMIN_ROLES.includes(role);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setConfirmOff(false);
      reload();
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (account === null) {
    return (
      <Section title="Patient account">
        <p className="text-sm text-muted">{failed ? "Couldn't load. Retrying..." : "Loading..."}</p>
      </Section>
    );
  }

  const { status } = account;
  return (
    <Section title="Patient account">
      <StatusLine account={account} />
      <EmailRow
        account={account}
        disabled={busy}
        onSave={(email) => run(() => setPatientEmail(clinicId, patientId, email))}
      />

      <div className="mt-3 flex flex-wrap gap-2">
        {status === "none" && (
          <button
            onClick={() => run(() => inviteAccount(clinicId, patientId))}
            disabled={busy || !account.email}
            className={primary}
          >
            Send invite
          </button>
        )}
        {status === "invited" && (
          <button
            onClick={() => run(() => resendInvite(clinicId, patientId))}
            disabled={busy || !account.email}
            className={secondary}
          >
            Resend invite
          </button>
        )}
        {(status === "locked" || status === "off") && canAdmin && (
          <button
            onClick={() => run(() => reinviteAccount(clinicId, patientId))}
            disabled={busy}
            className={primary}
          >
            Re-invite
          </button>
        )}
        {(status === "invited" || status === "active" || status === "locked") &&
          canAdmin &&
          !confirmOff && (
            <button onClick={() => setConfirmOff(true)} disabled={busy} className={danger}>
              Turn off access...
            </button>
          )}
      </div>

      {status === "none" && !account.email && (
        <p className="mt-2 text-xs text-muted">Add the patient&apos;s email to send an invite.</p>
      )}
      {!canAdmin && (status !== "none") && (
        <p className="mt-2 text-xs text-muted">
          Turning off access and re-inviting are for an owner or clinician.
        </p>
      )}

      {confirmOff && (
        <div className="mt-3 rounded-md border border-watch-border bg-watch-bg p-3 text-sm text-watch-text">
          <p>
            They will be signed out on every device and cannot sign in again until you invite them
            again.
          </p>
          <div className="mt-2 flex gap-2">
            <button
              onClick={() => run(() => turnOffAccess(clinicId, patientId))}
              disabled={busy}
              className={primary}
            >
              {busy ? "Turning off..." : "Turn off access"}
            </button>
            <button onClick={() => setConfirmOff(false)} disabled={busy} className={secondary}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-flag-text">
          {error}
        </p>
      )}

      <NoEmail
        account={account}
        canAdmin={canAdmin}
        onCreate={() => createFallbackLink(clinicId, patientId)}
        onCreated={reload}
      />
    </Section>
  );
}

const BADGE: Record<string, { variant: StatusVariant | null; label: string }> = {
  none: { variant: null, label: "Not invited" },
  invited: { variant: "info", label: "Invited" },
  active: { variant: "clear", label: "Active" },
  locked: { variant: "watch", label: "Locked" },
  off: { variant: null, label: "Access turned off" },
};

/** One plain sentence about where the account stands, and the last invite email's fate. */
function StatusLine({ account }: { account: Account }) {
  const badge = BADGE[account.status];
  let sentence = "";
  if (account.status === "none") sentence = "This patient has not been invited yet.";
  if (account.status === "invited" && account.invited_at) {
    sentence = `Invited on ${clock(account.invited_at)}. They have not finished signing in.`;
  }
  if (account.status === "active" && account.activated_at) {
    sentence = `Active since ${clock(account.activated_at)}.`;
  }
  if (account.status === "locked") {
    sentence = "Locked after three wrong surgery dates. Re-invite to unlock it.";
  }
  if (account.status === "off" && account.turned_off_at) {
    sentence = `Access turned off on ${clock(account.turned_off_at)}.`;
  }
  const invite = account.last_invite_email;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        {badge.variant ? (
          <StatusBadge variant={badge.variant}>{badge.label}</StatusBadge>
        ) : (
          <span className="rounded-full border border-border bg-surface-subtle px-2 py-0.5 text-xs font-medium text-muted">
            {badge.label}
          </span>
        )}
        <span className="text-sm text-ink">{sentence}</span>
      </div>
      {account.last_sign_in_at && (
        <p className="mt-1 text-xs text-muted">Last sign-in {clock(account.last_sign_in_at)}.</p>
      )}
      {invite && account.status === "invited" && <InviteLine invite={invite} />}
    </div>
  );
}

function InviteLine({ invite }: { invite: NonNullable<Account["last_invite_email"]> }) {
  const when = clock(invite.at);
  const text: Record<typeof invite.status, string> = {
    sent: `Invite email sent ${when}.`,
    demo: `Demo clinic: the invite was saved in the Demo inbox, not sent (${when}).`,
    failed: `The invite email could not be sent (${when}). Try Resend.`,
    refused: `The invite was not sent: that address cannot receive email (${when}).`,
    queued: `Sending the invite email (${when})...`,
  };
  const bad = invite.status === "failed" || invite.status === "refused";
  return <p className={`mt-1 text-xs ${bad ? "text-flag-text" : "text-muted"}`}>{text[invite.status]}</p>;
}

/** The address the invite and the sign-in codes go to. It cannot change while the account is
 *  active (the api refuses; turn access off first). */
function EmailRow({
  account,
  disabled,
  onSave,
}: {
  account: Account;
  disabled: boolean;
  onSave: (email: string | null) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const locked = account.status === "active";

  if (editing) {
    return (
      <form
        className="mt-3 flex flex-wrap items-center gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          await onSave(value.trim() || null);
          setEditing(false);
        }}
      >
        <label className="sr-only" htmlFor="patient-email">
          Patient email
        </label>
        <input
          id="patient-email"
          type="email"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="patient@example.org"
          className="min-w-0 flex-1 rounded-md border border-border-strong bg-surface px-3 py-2 text-base"
        />
        <button type="submit" disabled={disabled} className={primary}>
          Save
        </button>
        <button type="button" onClick={() => setEditing(false)} className={secondary}>
          Cancel
        </button>
      </form>
    );
  }
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <span className="text-muted">The invite goes to</span>
      {account.email ? (
        <span className="break-all font-medium text-ink">{account.email}</span>
      ) : (
        <span className="text-muted">no email on file</span>
      )}
      {!locked && (
        <button
          onClick={() => {
            setValue(account.email ?? "");
            setEditing(true);
          }}
          disabled={disabled}
          className="text-sm text-primary underline disabled:opacity-60"
        >
          {account.email ? "Change" : "Add email"}
        </button>
      )}
      {locked && (
        <span className="text-xs text-muted">To change it, turn off access first.</span>
      )}
    </div>
  );
}

/** For a patient with no email: a single-use sign-in link. It is shown once, works once, lasts 24
 *  hours, and asks the patient for their surgery date each time. */
function NoEmail({
  account,
  canAdmin,
  onCreate,
  onCreated,
}: {
  account: Account;
  canAdmin: boolean;
  onCreate: () => Promise<FallbackLink>;
  onCreated: () => void;
}) {
  const [link, setLink] = useState<FallbackLink | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const blocked = account.status === "locked" || account.status === "off";

  async function create() {
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      const made = await onCreate();
      setLink(made);
      onCreated();
      try {
        await navigator.clipboard.writeText(made.url);
        setCopied(true);
      } catch {
        // Clipboard can be blocked (for example without HTTPS); the link is shown below to copy by hand.
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create the link.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="mt-4 border-t border-border pt-3">
      <summary className="cursor-pointer text-sm text-primary underline">
        Patient has no email?
      </summary>
      <div className="mt-2 text-sm text-ink">
        <p>
          Create a single-use link and give it to the patient yourself. It works once and expires in
          24 hours. They will be asked for their surgery date. We show it only now: it can&apos;t be
          shown again.
        </p>
        {canAdmin ? (
          <button
            onClick={create}
            disabled={busy || blocked}
            className={`${secondary} mt-2`}
          >
            {busy ? "Creating..." : "Create single-use link"}
          </button>
        ) : (
          <p className="mt-2 text-xs text-muted">Only an owner or clinician can create a link.</p>
        )}
        {blocked && canAdmin && (
          <p className="mt-1 text-xs text-muted">Re-invite this patient first.</p>
        )}
        {error && (
          <p role="alert" className="mt-2 text-sm text-flag-text">
            {error}
          </p>
        )}
        {link && (
          <div className="mt-3 space-y-1">
            <p className="text-xs text-muted">
              {copied ? "Copied. " : "Copy this link now: "}It expires {clock(link.expires_at)}.
            </p>
            <input
              readOnly
              value={link.url}
              aria-label="Single-use sign-in link"
              onFocus={(e) => e.currentTarget.select()}
              className="w-full rounded-md border border-border-strong bg-surface-subtle px-2 py-1.5 font-mono text-xs"
            />
          </div>
        )}
      </div>
    </details>
  );
}
