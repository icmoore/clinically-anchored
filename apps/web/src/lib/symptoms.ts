// Post-op check-in v1: procedure-specific branching symptom tree (decisions-
// and-open-questions.md decision 13). This file is the single source of
// truth for both the patient-facing form (check-in/check-in-form.tsx) and
// the clinician-facing summary (dashboard/patients/[id]/page.tsx) -- the
// form renders from SYMPTOMS_BY_CATEGORY/SYMPTOMS, and buildSummary() below
// reads the same definitions back out, so the two can't drift apart.
//
// *** DRAFT PENDING SARAH'S REVIEW ***
// Her email gave full detail for Pain and Bleeding, and started Fever, all
// under one example procedure (fistulotomy). Everything else here --
// Discharge/Dizziness/Fatigue/Constipation/Diarrhea/Nausea-vomiting/No-bowel-
// movement/Distension, and which symptom set applies to which of her other
// fifteen procedures -- is Ian/Claude's best-guess inference in the same
// style as her examples, not sourced from her. See decision 13 for the
// sourced-vs-inferred breakdown.

export type ProcedureCategory =
  | "abdominal_general"
  | "abdominal_bowel_resection"
  | "anorectal"
  | "other";

export interface FieldOption {
  value: string;
  label: string;
}

export type FieldType = "scale_1_10" | "radio" | "multi_checkbox" | "number" | "yes_no";

export interface FieldDef {
  key: string;
  label: string;
  type: FieldType;
  options?: FieldOption[];
  // Multi-checkbox fields where each checked option gets its own follow-up
  // (e.g. "how often are you taking <medication>?" per medication checked).
  // The follow-up answers are stored under answers[symptomKey][perOptionFollowUp.key],
  // keyed by option value.
  perOptionFollowUp?: { key: string; label: string; options: FieldOption[] };
}

export interface SymptomDef {
  key: string;
  label: string; // the checkbox label on the "what's worrying you" screen
  fields: FieldDef[];
}

export const TREND_OPTIONS: FieldOption[] = [
  { value: "better", label: "Better" },
  { value: "worse", label: "Worse" },
  { value: "same", label: "Same" },
];

export const FREQUENCY_OPTIONS: FieldOption[] = [
  { value: "once_a_day", label: "Once a day" },
  { value: "several_times_a_day", label: "Several times a day" },
  { value: "every_2_3_hours", label: "Every 2-3 hours" },
  { value: "hourly", label: "Hourly" },
];

export const YES_NO_OPTIONS: FieldOption[] = [
  { value: "true", label: "Yes" },
  { value: "false", label: "No" },
];

export const PAIN_MEDICATIONS: FieldOption[] = [
  { value: "tylenol", label: "Tylenol" },
  { value: "advil", label: "Advil" },
  { value: "oxycodone", label: "Oxycodone" },
  { value: "hydromorphone", label: "Hydromorphone" },
  { value: "morphine", label: "Morphine" },
];

export const SURGERY_TYPE_OPTIONS: FieldOption[] = [
  { value: "open", label: "Open (there is a long incision on your abdomen)" },
  { value: "laparoscopic", label: "Laparoscopic" },
];

export const WOUND_CLOSURE_OPTIONS: FieldOption[] = [
  { value: "staples", label: "Staples" },
  { value: "absorbing_sutures", label: "Absorbing sutures" },
  { value: "unsure", label: "Not applicable or unsure" },
];

// Sourced near-verbatim from Sarah's email (pain, bleeding; fever extended
// with a "chills" question in the same spirit as her partial example).
export const SYMPTOMS: Record<string, SymptomDef> = {
  pain: {
    key: "pain",
    label: "Pain",
    fields: [
      {
        key: "scale",
        label: "Rate your pain from 1 (least) to 10 (most severe)",
        type: "scale_1_10",
      },
      { key: "trend", label: "How is the pain changing since surgery?", type: "radio", options: TREND_OPTIONS },
      {
        key: "character",
        label: "Is the pain",
        type: "radio",
        options: [
          { value: "constant", label: "Constant" },
          { value: "coming_and_going", label: "Coming and going" },
          { value: "worse_with_bowel_movements", label: "Most pronounced / worse with bowel movements" },
        ],
      },
      {
        key: "medications",
        label: "Are you taking any of the following pain medications?",
        type: "multi_checkbox",
        options: PAIN_MEDICATIONS,
        perOptionFollowUp: {
          key: "medication_frequency",
          label: "How often are you taking it?",
          options: FREQUENCY_OPTIONS,
        },
      },
    ],
  },
  bleeding: {
    key: "bleeding",
    label: "Bleeding",
    fields: [
      {
        key: "trend",
        label: "Has your bleeding",
        type: "radio",
        options: [
          { value: "same", label: "Stayed the same since surgery" },
          { value: "worse", label: "Gotten worse since surgery" },
        ],
      },
      {
        key: "timing",
        label: "Is your bleeding occurring",
        type: "radio",
        options: [
          { value: "with_bowel_movements", label: "Only with bowel movements" },
          { value: "any_time", label: "Any time, even outside of bowel movements" },
        ],
      },
      {
        key: "description",
        label: "Please describe your bleeding",
        type: "radio",
        options: [
          { value: "tissue_paper_only", label: "Only noticed on tissue paper when wiping" },
          { value: "dripping_into_toilet", label: "Dripping into the toilet bowl" },
          { value: "soaking_through_clothes", label: "Soaking through your undergarments and clothes" },
        ],
      },
      {
        key: "dressing_change_frequency",
        label: "How frequently are you changing your dressing because of bleeding?",
        type: "radio",
        options: FREQUENCY_OPTIONS,
      },
      {
        key: "started_prescribed_medication",
        label: "Have you started any medication your surgeon prescribed for bleeding (e.g. tranexamic acid)?",
        type: "yes_no",
      },
    ],
  },
  fever: {
    key: "fever",
    label: "Fever or chills",
    fields: [
      { key: "took_temperature", label: "Have you taken your temperature with a thermometer?", type: "yes_no" },
      { key: "temperature_c", label: "What does the thermometer read, in Celsius?", type: "number" },
      { key: "chills", label: "Are you having chills along with the fever?", type: "yes_no" },
    ],
  },
  // --- Everything below is inferred, not sourced from Sarah's email. ---
  discharge: {
    key: "discharge",
    label: "Discharge or leakage from the wound",
    fields: [
      {
        key: "trend",
        label: "Is the discharge",
        type: "radio",
        options: [
          { value: "more", label: "More than before" },
          { value: "same", label: "About the same" },
          { value: "less", label: "Less than before" },
        ],
      },
      {
        key: "character",
        label: "How would you describe it?",
        type: "radio",
        options: [
          { value: "clear_watery", label: "Clear or watery" },
          { value: "yellow_cloudy", label: "Yellow or cloudy" },
          { value: "bloody", label: "Bloody" },
          { value: "pus_like", label: "Pus-like or foul-smelling" },
        ],
      },
      {
        key: "dressing_change_frequency",
        label: "How frequently are you changing your dressing because of it?",
        type: "radio",
        options: FREQUENCY_OPTIONS,
      },
    ],
  },
  dizziness: {
    key: "dizziness",
    label: "Dizziness",
    fields: [
      { key: "trend", label: "Is the dizziness", type: "radio", options: TREND_OPTIONS },
      { key: "orthostatic", label: "Does it happen mainly when you stand up?", type: "yes_no" },
      {
        key: "since",
        label: "Since when?",
        type: "radio",
        options: [
          { value: "today", label: "Today" },
          { value: "since_surgery", label: "Since surgery" },
        ],
      },
    ],
  },
  fatigue: {
    key: "fatigue",
    label: "Fatigue",
    fields: [
      { key: "trend", label: "Is the fatigue", type: "radio", options: TREND_OPTIONS },
      {
        key: "interfering_with_activities",
        label: "Is it interfering with daily activities (getting up, eating, walking)?",
        type: "yes_no",
      },
    ],
  },
  constipation: {
    key: "constipation",
    label: "Constipation",
    fields: [
      { key: "days_since_bm", label: "How many days since your last bowel movement?", type: "number" },
      { key: "passing_gas", label: "Are you still passing gas?", type: "yes_no" },
      { key: "distension", label: "Do you have abdominal bloating or distension?", type: "yes_no" },
    ],
  },
  diarrhea: {
    key: "diarrhea",
    label: "Diarrhea",
    fields: [
      { key: "frequency_per_day", label: "About how many times a day?", type: "number" },
      { key: "blood_in_stool", label: "Is there any blood in your stool?", type: "yes_no" },
      {
        key: "duration",
        label: "Since when?",
        type: "radio",
        options: [
          { value: "today", label: "Today" },
          { value: "several_days", label: "Several days" },
        ],
      },
    ],
  },
  nausea_vomiting: {
    key: "nausea_vomiting",
    label: "Nausea or vomiting",
    fields: [
      { key: "trend", label: "Is it", type: "radio", options: TREND_OPTIONS },
      { key: "keeping_fluids_down", label: "Are you able to keep fluids down?", type: "yes_no" },
      { key: "vomiting_episodes_today", label: "About how many times have you vomited today?", type: "number" },
    ],
  },
  no_bowel_movement: {
    key: "no_bowel_movement",
    label: "No bowel movements or not passing gas",
    fields: [
      {
        key: "days",
        label: "How many days since your last bowel movement or passing gas?",
        type: "number",
      },
      { key: "distension", label: "Do you have abdominal bloating or distension?", type: "yes_no" },
      { key: "nausea_vomiting", label: "Any nausea or vomiting with it?", type: "yes_no" },
    ],
  },
  distension: {
    key: "distension",
    label: "Worsening bloating or distension",
    fields: [
      { key: "trend", label: "Is it", type: "radio", options: TREND_OPTIONS },
      { key: "associated_pain", label: "Is it associated with pain?", type: "yes_no" },
    ],
  },
};

// Which symptom checklist each procedure category sees. Anorectal is
// Sarah's own fistulotomy example, verbatim (pain, bleeding, fever,
// discharge, dizziness, fatigue, constipation, diarrhea). The abdominal
// sets are inferred -- general post-op red flags for abdominal_general,
// plus bowel-function flags (no_bowel_movement, distension) for resections
// where an ileus or anastomotic leak is the thing not to miss.
export const SYMPTOMS_BY_CATEGORY: Record<ProcedureCategory, string[]> = {
  anorectal: ["pain", "bleeding", "fever", "discharge", "dizziness", "fatigue", "constipation", "diarrhea"],
  abdominal_general: ["pain", "bleeding", "fever", "discharge", "nausea_vomiting", "dizziness", "fatigue"],
  abdominal_bowel_resection: [
    "pain",
    "bleeding",
    "fever",
    "discharge",
    "nausea_vomiting",
    "dizziness",
    "fatigue",
    "no_bowel_movement",
    "distension",
  ],
  other: ["pain", "bleeding", "fever", "discharge", "dizziness", "fatigue"],
};

export function isAbdominal(category: ProcedureCategory): boolean {
  return category === "abdominal_general" || category === "abdominal_bowel_resection";
}

export function symptomsForCategory(category: ProcedureCategory): SymptomDef[] {
  return SYMPTOMS_BY_CATEGORY[category].map((key) => SYMPTOMS[key]);
}

export const CONCERNS_TEXT_MAX_LENGTH = 500;

// --- Answer shape (mirrors apps/api's schemas.py CheckInCreate comment) ---

export type SymptomAnswers = Record<string, unknown>;

export interface CheckInAnswers {
  symptoms?: string[];
  surgery_type?: "open" | "laparoscopic";
  wound_closure?: "staples" | "absorbing_sutures" | "unsure";
  concerns_text?: string;
  [symptomKey: string]: unknown;
}

function optionLabel(options: FieldOption[] | undefined, value: unknown): string {
  if (value === undefined || value === null || value === "") return "not answered";
  return options?.find((o) => o.value === value)?.label ?? String(value);
}

function formatFieldValue(field: FieldDef, value: unknown): string {
  if (value === undefined || value === null || value === "") return "not answered";
  switch (field.type) {
    case "yes_no":
      return value === true ? "Yes" : value === false ? "No" : String(value);
    case "radio":
      return optionLabel(field.options, value);
    case "scale_1_10":
      return `${value}/10`;
    case "number":
      return String(value);
    case "multi_checkbox": {
      const arr = Array.isArray(value) ? value : [];
      if (arr.length === 0) return "none";
      return arr.map((v) => optionLabel(field.options, v)).join(", ");
    }
    default:
      return String(value);
  }
}

/** Short label list for a compact, at-a-glance view (dashboard list row). */
export function reportedSymptomLabels(answers: CheckInAnswers): string[] {
  const keys = Array.isArray(answers.symptoms) ? answers.symptoms : [];
  return keys.map((key) => SYMPTOMS[key]?.label ?? key);
}

/** Full plain-text summary: patient, procedure, post-op day, then one
 * line per reported symptom covering every follow-up answer, built
 * entirely from the SYMPTOMS definitions above so it can't drift out of
 * sync with the form. Computed at render time (not stored) from
 * already-resolved names -- the dashboard has patient_name/procedure_name
 * on every CheckInDetail; the patient's own pre-submit review just omits
 * patientName since the form never needs to ask them to re-type it. */
export function buildSummary(opts: {
  patientName?: string | null;
  procedureName?: string | null;
  postOpDay?: number | null;
  answers: CheckInAnswers;
}): string {
  const { patientName, procedureName, postOpDay, answers } = opts;

  const header = [
    patientName ? `Patient: ${patientName}` : null,
    procedureName ? `Procedure: ${procedureName}` : null,
    postOpDay !== null && postOpDay !== undefined ? `Post-op day: ${postOpDay}` : null,
  ]
    .filter(Boolean)
    .join(" | ");

  const extras: string[] = [];
  if (answers.surgery_type) {
    extras.push(`Surgery type: ${optionLabel(SURGERY_TYPE_OPTIONS, answers.surgery_type)}`);
  }
  if (answers.wound_closure) {
    extras.push(`Wound closure: ${optionLabel(WOUND_CLOSURE_OPTIONS, answers.wound_closure)}`);
  }

  const reported = Array.isArray(answers.symptoms) ? answers.symptoms : [];
  const symptomLines = reported.map((key) => {
    const def = SYMPTOMS[key];
    if (!def) return `${key}: reported`;
    const data = (answers[key] as SymptomAnswers) ?? {};
    const parts: string[] = [];
    for (const field of def.fields) {
      parts.push(`${field.label.replace(/[?:]$/, "")}: ${formatFieldValue(field, data[field.key])}`);
      if (field.perOptionFollowUp && Array.isArray(data[field.key])) {
        const freqByOption = (data[field.perOptionFollowUp.key] as Record<string, string>) ?? {};
        for (const optValue of data[field.key] as string[]) {
          const optLabel = optionLabel(field.options, optValue);
          const freqLabel = freqByOption[optValue]
            ? optionLabel(field.perOptionFollowUp.options, freqByOption[optValue])
            : "frequency not given";
          parts.push(`${optLabel} frequency: ${freqLabel}`);
        }
      }
    }
    return `${def.label} -- ${parts.join("; ")}`;
  });

  if (answers.concerns_text) {
    extras.push(`Patient's own words: "${answers.concerns_text}"`);
  }

  const body = symptomLines.length > 0 ? symptomLines : ["No worrisome symptoms reported."];
  return [header, ...extras, ...body].filter(Boolean).join("\n");
}
