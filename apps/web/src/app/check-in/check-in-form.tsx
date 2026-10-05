"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ApiError, CheckInContext, getCheckInContext, submitCheckIn } from "@/lib/api";
import {
  CONCERNS_TEXT_MAX_LENGTH,
  CheckInAnswers,
  FieldDef,
  SURGERY_TYPE_OPTIONS,
  WOUND_CLOSURE_OPTIONS,
  buildSummary,
  isAbdominal,
  symptomsForCategory,
} from "@/lib/symptoms";

type Status = "loading" | "ready" | "submitting" | "submitted" | "error";

export function CheckInForm() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<string | null>(null);
  const [context, setContext] = useState<CheckInContext | null>(null);

  const [procedureId, setProcedureId] = useState("");
  const [postOpDay, setPostOpDay] = useState("");
  const [surgeryType, setSurgeryType] = useState<"" | "open" | "laparoscopic">("");
  const [woundClosure, setWoundClosure] = useState<"" | "staples" | "absorbing_sutures" | "unsure">("");
  const [selectedSymptoms, setSelectedSymptoms] = useState<Record<string, boolean>>({});
  const [symptomAnswers, setSymptomAnswers] = useState<Record<string, Record<string, unknown>>>({});
  const [concernsText, setConcernsText] = useState("");
  const [showReview, setShowReview] = useState(false);

  useEffect(() => {
    if (!token) return;
    getCheckInContext(token)
      .then((ctx) => {
        setContext(ctx);
        setStatus("ready");
      })
      .catch((err: unknown) => {
        setStatus("error");
        setError(err instanceof ApiError ? err.message : "Couldn't load this check-in link.");
      });
  }, [token]);

  const selectedProcedure = context?.procedures.find((p) => p.id === procedureId) ?? null;
  const category = selectedProcedure?.category ?? null;
  const abdominal = category ? isAbdominal(category) : false;
  const availableSymptoms = category ? symptomsForCategory(category) : [];
  const reportedCount = Object.values(selectedSymptoms).filter(Boolean).length;

  function handleProcedureChange(nextId: string) {
    setProcedureId(nextId);
    // A different procedure can mean a different symptom set -- clear
    // answers that no longer apply rather than leave stale data behind.
    setSelectedSymptoms({});
    setSymptomAnswers({});
    setSurgeryType("");
    setWoundClosure("");
    setShowReview(false);
  }

  function toggleSymptom(key: string, checked: boolean) {
    setSelectedSymptoms((prev) => ({ ...prev, [key]: checked }));
  }

  function updateField(symptomKey: string, fieldKey: string, value: unknown) {
    setSymptomAnswers((prev) => ({
      ...prev,
      [symptomKey]: { ...prev[symptomKey], [fieldKey]: value },
    }));
  }

  function updateNestedField(symptomKey: string, parentFieldKey: string, optionValue: string, value: unknown) {
    setSymptomAnswers((prev) => {
      const current = (prev[symptomKey]?.[parentFieldKey] as Record<string, unknown>) ?? {};
      return {
        ...prev,
        [symptomKey]: {
          ...prev[symptomKey],
          [parentFieldKey]: { ...current, [optionValue]: value },
        },
      };
    });
  }

  function buildAnswers(): CheckInAnswers {
    const symptoms = Object.keys(selectedSymptoms).filter((k) => selectedSymptoms[k]);
    const answers: CheckInAnswers = { symptoms };
    if (abdominal) {
      if (surgeryType) answers.surgery_type = surgeryType;
      if (woundClosure) answers.wound_closure = woundClosure;
    }
    for (const key of symptoms) {
      if (symptomAnswers[key]) answers[key] = symptomAnswers[key];
    }
    if (concernsText.trim()) answers.concerns_text = concernsText.trim();
    return answers;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!showReview) {
      setShowReview(true);
      return;
    }
    setStatus("submitting");
    try {
      await submitCheckIn(token, {
        procedure_id: procedureId || null,
        post_op_day: postOpDay ? Number(postOpDay) : null,
        answers: buildAnswers(),
      });
      setStatus("submitted");
    } catch (err) {
      setStatus("error");
      setError(err instanceof ApiError ? err.message : "Couldn't submit this check-in.");
    }
  }

  if (!token) {
    return <p className="text-red-600">This link is missing its token.</p>;
  }

  if (status === "loading") {
    return <p className="text-zinc-600">Loading your check-in...</p>;
  }

  if (status === "error" && !context) {
    return <p className="text-red-600">{error}</p>;
  }

  if (status === "submitted") {
    return (
      <div className="rounded-lg border border-green-200 bg-green-50 p-6">
        <p className="font-medium text-green-900">Thanks -- your check-in was sent.</p>
        <p className="mt-1 text-sm text-green-800">
          Your care team will follow up if anything needs their attention.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6">
      <div>
        <label htmlFor="procedure" className="mb-1 block text-sm font-medium text-zinc-900">
          Which procedure did you have?
        </label>
        <select
          id="procedure"
          value={procedureId}
          onChange={(e) => handleProcedureChange(e.target.value)}
          className="w-full rounded-md border border-zinc-300 px-3 py-2 text-zinc-900"
          required
        >
          <option value="" disabled>
            Select one
          </option>
          {context?.procedures.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label htmlFor="post-op-day" className="mb-1 block text-sm font-medium text-zinc-900">
          How many days since your surgery? (optional)
        </label>
        <input
          id="post-op-day"
          type="number"
          min={0}
          value={postOpDay}
          onChange={(e) => setPostOpDay(e.target.value)}
          className="w-full rounded-md border border-zinc-300 px-3 py-2 text-zinc-900"
        />
      </div>

      {abdominal && (
        <>
          <RadioGroup
            legend="Was your surgery:"
            options={SURGERY_TYPE_OPTIONS}
            value={surgeryType}
            onChange={(v) => setSurgeryType(v as typeof surgeryType)}
          />
          <RadioGroup
            legend="Do you have:"
            options={WOUND_CLOSURE_OPTIONS}
            value={woundClosure}
            onChange={(v) => setWoundClosure(v as typeof woundClosure)}
          />
        </>
      )}

      {category && (
        <fieldset>
          <legend className="mb-2 text-sm font-medium text-zinc-900">
            Please indicate what your worrisome symptoms are:
          </legend>
          <div className="flex flex-col gap-3">
            {availableSymptoms.map((symptom) => (
              <div key={symptom.key}>
                <label className="flex items-center gap-2 text-zinc-800">
                  <input
                    type="checkbox"
                    checked={Boolean(selectedSymptoms[symptom.key])}
                    onChange={(e) => toggleSymptom(symptom.key, e.target.checked)}
                  />
                  {symptom.label}
                </label>
                {selectedSymptoms[symptom.key] && (
                  <div className="mt-2 ml-6 flex flex-col gap-3 border-l-2 border-zinc-200 pl-4">
                    {symptom.fields.map((field) => (
                      <FieldBlock
                        key={field.key}
                        field={field}
                        value={symptomAnswers[symptom.key]?.[field.key]}
                        onChange={(v) => updateField(symptom.key, field.key, v)}
                        nestedValue={
                          field.perOptionFollowUp
                            ? (symptomAnswers[symptom.key]?.[field.perOptionFollowUp.key] as
                                | Record<string, unknown>
                                | undefined)
                            : undefined
                        }
                        onNestedChange={(optValue, v) =>
                          updateNestedField(symptom.key, field.key, optValue, v)
                        }
                      />
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </fieldset>
      )}

      {category && (
        <div>
          <label htmlFor="concerns" className="mb-1 block text-sm font-medium text-zinc-900">
            Anything else you&apos;d like to tell us, in your own words? (optional)
          </label>
          <textarea
            id="concerns"
            value={concernsText}
            maxLength={CONCERNS_TEXT_MAX_LENGTH}
            onChange={(e) => setConcernsText(e.target.value)}
            rows={3}
            className="w-full rounded-md border border-zinc-300 px-3 py-2 text-zinc-900"
          />
          <p className="mt-1 text-right text-xs text-zinc-500">
            {concernsText.length}/{CONCERNS_TEXT_MAX_LENGTH}
          </p>
        </div>
      )}

      {showReview && (
        <div className="rounded-lg border border-zinc-300 bg-zinc-50 p-4">
          <p className="mb-2 text-sm font-medium text-zinc-900">Please review before sending:</p>
          <pre className="whitespace-pre-wrap font-sans text-sm text-zinc-800">
            {buildSummary({
              procedureName: selectedProcedure?.name ?? null,
              postOpDay: postOpDay ? Number(postOpDay) : null,
              answers: buildAnswers(),
            })}
          </pre>
        </div>
      )}

      {status === "error" && error && <p className="text-sm text-red-600">{error}</p>}

      <button
        type="submit"
        disabled={status === "submitting" || !procedureId}
        className="rounded-md bg-zinc-900 px-4 py-2 font-medium text-white disabled:opacity-50"
      >
        {showReview
          ? status === "submitting"
            ? "Sending..."
            : "Confirm and send check-in"
          : reportedCount > 0
            ? "Review check-in"
            : "Send check-in"}
      </button>
    </form>
  );
}

function RadioGroup({
  legend,
  options,
  value,
  onChange,
}: {
  legend: string;
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <fieldset>
      <legend className="mb-2 text-sm font-medium text-zinc-900">{legend}</legend>
      <div className="flex flex-col gap-1">
        {options.map((opt) => (
          <label key={opt.value} className="flex items-center gap-2 text-sm text-zinc-700">
            <input
              type="radio"
              checked={value === opt.value}
              onChange={() => onChange(opt.value)}
            />
            {opt.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function FieldBlock({
  field,
  value,
  onChange,
  nestedValue,
  onNestedChange,
}: {
  field: FieldDef;
  value: unknown;
  onChange: (value: unknown) => void;
  nestedValue?: Record<string, unknown>;
  onNestedChange: (optionValue: string, value: unknown) => void;
}) {
  if (field.type === "scale_1_10") {
    return (
      <div>
        <label className="block text-sm text-zinc-800">{field.label}</label>
        <select
          value={typeof value === "number" ? String(value) : ""}
          onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)}
          className="mt-1 w-20 rounded-md border border-zinc-300 px-2 py-1 text-sm text-zinc-900"
        >
          <option value="">--</option>
          {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </div>
    );
  }

  if (field.type === "number") {
    return (
      <div>
        <label className="block text-sm text-zinc-800">{field.label}</label>
        <input
          type="number"
          min={0}
          value={typeof value === "number" ? value : ""}
          onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
          className="mt-1 w-24 rounded-md border border-zinc-300 px-2 py-1 text-sm text-zinc-900"
        />
      </div>
    );
  }

  if (field.type === "yes_no") {
    return (
      <div>
        <p className="text-sm text-zinc-800">{field.label}</p>
        <div className="mt-1 flex gap-4">
          {[
            { v: true, l: "Yes" },
            { v: false, l: "No" },
          ].map((opt) => (
            <label key={String(opt.v)} className="flex items-center gap-1 text-sm text-zinc-700">
              <input type="radio" checked={value === opt.v} onChange={() => onChange(opt.v)} />
              {opt.l}
            </label>
          ))}
        </div>
      </div>
    );
  }

  if (field.type === "radio") {
    return (
      <div>
        <p className="text-sm text-zinc-800">{field.label}</p>
        <div className="mt-1 flex flex-col gap-1">
          {field.options?.map((opt) => (
            <label key={opt.value} className="flex items-center gap-2 text-sm text-zinc-700">
              <input type="radio" checked={value === opt.value} onChange={() => onChange(opt.value)} />
              {opt.label}
            </label>
          ))}
        </div>
      </div>
    );
  }

  if (field.type === "multi_checkbox") {
    const selected: string[] = Array.isArray(value) ? value : [];
    return (
      <div>
        <p className="text-sm text-zinc-800">{field.label}</p>
        <div className="mt-1 flex flex-col gap-2">
          {field.options?.map((opt) => {
            const checked = selected.includes(opt.value);
            return (
              <div key={opt.value}>
                <label className="flex items-center gap-2 text-sm text-zinc-700">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? [...selected, opt.value]
                        : selected.filter((v) => v !== opt.value);
                      onChange(next);
                    }}
                  />
                  {opt.label}
                </label>
                {checked && field.perOptionFollowUp && (
                  <div className="mt-1 ml-6 flex items-center gap-2">
                    <label className="text-xs text-zinc-600">{field.perOptionFollowUp.label}</label>
                    <select
                      value={(nestedValue?.[opt.value] as string) ?? ""}
                      onChange={(e) => onNestedChange(opt.value, e.target.value)}
                      className="rounded-md border border-zinc-300 px-2 py-1 text-xs text-zinc-900"
                    >
                      <option value="">--</option>
                      {field.perOptionFollowUp.options.map((fo) => (
                        <option key={fo.value} value={fo.value}>
                          {fo.label}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  return null;
}
