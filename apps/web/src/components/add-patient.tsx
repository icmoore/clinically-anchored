"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ApiError, Procedure } from "@/lib/api";
import { createPatient, listProcedures } from "@/lib/clinician-api";

const secondary =
  "rounded-md border border-link bg-surface text-link enabled:hover:border-link-hover enabled:hover:text-link-hover enabled:hover:bg-primary-tint px-4 py-2 text-sm font-medium disabled:opacity-60";

/** "Patient management": adding patients, behind a "+ Add patient" button. After a patient is
 *  added the list is told to reload (`onAdded`) and the form stays open and empty (the procedure
 *  is kept, since a clinic often adds several of the same), ready for the next one. */
export function PatientManagement({
  clinicId,
  onAdded,
}: {
  clinicId: string;
  onAdded: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [added, setAdded] = useState<{ id: string; name: string } | null>(null);

  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
        Patient management
      </h2>
      <button
        type="button"
        aria-expanded={open}
        aria-controls="add-patient-form"
        onClick={() => setOpen((o) => !o)}
        className={`${secondary} min-h-11`}
      >
        + Add patient
      </button>
      {open && (
        <div id="add-patient-form" className="mt-3">
          <AddPatientForm
            clinicId={clinicId}
            onAdded={(patient) => {
              setAdded({ id: patient.id, name: patient.full_name });
              onAdded();
            }}
            onClose={() => {
              setOpen(false);
              setAdded(null);
            }}
          />
        </div>
      )}
      {added && (
        <p role="status" className="mt-3 text-sm text-clear-text">
          Added {added.name}.{" "}
          <Link
            href={`/dashboard/patients/${added.id}`}
            className="font-medium text-link underline hover:text-link-hover"
          >
            Open their page
          </Link>
        </p>
      )}
    </section>
  );
}

function AddPatientForm({
  clinicId,
  onAdded,
  onClose,
}: {
  clinicId: string;
  onAdded: (patient: { id: string; full_name: string }) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [email, setEmail] = useState("");
  const [surgeryDate, setSurgeryDate] = useState("");
  const [procedureId, setProcedureId] = useState("");
  const [procedures, setProcedures] = useState<Procedure[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameField = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listProcedures(clinicId)
      .then(setProcedures)
      .catch(() => setProcedures([]));
  }, [clinicId]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const patient = await createPatient(clinicId, {
        full_name: name,
        contact: contact || null,
        email: email.trim() || null,
        surgery_date: surgeryDate,
        procedure_id: procedureId,
      });
      setName("");
      setContact("");
      setEmail("");
      setSurgeryDate("");
      onAdded(patient);
      nameField.current?.focus();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't add this patient.");
    } finally {
      setBusy(false);
    }
  }

  const field = "min-w-0 rounded-md border border-border-strong bg-surface px-3 py-2 text-base";
  return (
    <form onSubmit={handleSubmit} className="grid gap-2 sm:grid-cols-2">
      <input
        ref={nameField}
        required
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Patient name (use a fake name for now)"
        aria-label="Patient name"
        className={field}
      />
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="Email for the sign-in invite (optional)"
        aria-label="Patient email"
        className={field}
      />
      <input
        value={contact}
        onChange={(e) => setContact(e.target.value)}
        placeholder="Phone (optional)"
        aria-label="Phone"
        className={`${field} sm:col-span-2`}
      />
      <label className="flex flex-col gap-1 text-xs text-muted">
        Surgery date
        <input
          required
          type="date"
          value={surgeryDate}
          onChange={(e) => setSurgeryDate(e.target.value)}
          className={`${field} text-ink`}
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted">
        Procedure
        <select
          required
          value={procedureId}
          onChange={(e) => setProcedureId(e.target.value)}
          className={`${field} text-ink`}
        >
          <option value="" disabled>
            {procedures === null ? "Loading..." : "Select one"}
          </option>
          {procedures?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        disabled={busy || !name.trim() || !surgeryDate || !procedureId}
        className="rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2 text-base font-medium text-on-primary disabled:opacity-60"
      >
        Add patient
      </button>
      <button type="button" onClick={onClose} className={`${secondary} text-base`}>
        Close
      </button>
      {error && <p className="text-sm text-flag-text sm:col-span-2">{error}</p>}
    </form>
  );
}
