// What a patient picks before writing a scheduled message. The api stores and accepts the KEY;
// the labels are the web app's and will change. Both the keys (core/categories.py in apps/api)
// and these labels are PLACEHOLDERS pending the surgeon: change a label here, a key in both.
export const CATEGORY_LABELS: Record<string, string> = {
  recovery_update: "How my recovery is going",
  symptom_or_wound: "A symptom, or my wound",
  medication: "My medication",
  admin: "Appointments or paperwork",
};

export function categoryLabel(key: string): string {
  return CATEGORY_LABELS[key] ?? key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
