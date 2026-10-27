"""The demo inbox: the emails a demo clinic did not send.

A demo clinic (clinics.is_demo, set only by SQL) sends no real email. core/mailer.py stores the
rendered email, sign-in code included, in the outbox instead, and this route shows them, newest
first. Only an owner of a demo clinic may open it: anyone else who belongs to the clinic gets 403,
and an owner of an ordinary clinic gets 404, as if the page did not exist.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.schemas import DemoEmailOut

router = APIRouter(tags=["demo inbox"])


@router.get("/clinics/{clinic_id}/demo-inbox", response_model=list[DemoEmailOut])
def demo_inbox(
    clinic_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    member: ClinicMember = Depends(require_clinic_member),
) -> list[DemoEmailOut]:
    if member.role != "owner":
        raise HTTPException(status_code=403, detail="Only a clinic owner can open the demo inbox.")
    supabase = get_supabase()
    clinic = supabase.table("clinics").select("is_demo").eq("id", clinic_id).execute().data
    if not clinic or not clinic[0]["is_demo"]:
        raise HTTPException(status_code=404, detail="Not found.")
    rows = (
        supabase.table("email_outbox")
        .select(
            "id, recipient, template, patient_id, rendered_subject, rendered_text, "
            "rendered_html, created_at"
        )
        .eq("clinic_id", clinic_id)
        .eq("status", "demo")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )
    return [
        DemoEmailOut(
            id=r["id"],
            recipient=r["recipient"],
            template=r["template"],
            patient_id=r["patient_id"],
            subject=r["rendered_subject"],
            text=r["rendered_text"],
            html=r["rendered_html"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
