"""Clinic facts the patient-facing screens need."""


def office_phone(supabase, clinic_id: str) -> str | None:
    """The clinic's office number, or None. Null, never a blank string: an empty value must not
    reach a patient's screen as an empty number (the screens then say "call your surgeon's office"
    with no number, and the clinic sees a warning until an owner sets one)."""
    rows = supabase.table("clinics").select("office_phone").eq("id", clinic_id).execute().data
    return ((rows[0]["office_phone"] or "").strip() or None) if rows else None
