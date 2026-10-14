"""Seed ten synthetic trial patients into a clinician's demo clinic.

Creates (or reuses) a separate demo clinic, adds the clinician as its owner,
copies the active procedure list from the dev clinic, then writes ten
invented patients with backdated check-ins, message threads, contact-log
entries and check-in reviews, ending in a state worth demoing:

  * Trial Patient 1 -- Elena Marchetti, laparoscopic cholecystectomy, POD 7.
    Uneventful. Latest check-in clear and not yet reviewed.
  * Trial Patient 2 -- Gordon Whitlock, right hemicolectomy, POD 14. Red-flag
    check-in on POD 7 (ileus): phone call, emergency visit, staple-removal
    visit. Now recovering; latest check-in clear and not yet reviewed.
  * Trial Patient 3 -- Nadia Haddad, hemorrhoid surgery, POD 6. Red-flag
    check-in today (pain 8/10) and a patient message still awaiting a reply:
    the one to try "Draft a reply with AI" on.
  * Trial Patients 4-10 -- a wider mix (5 to 26 messages each):
    4 Marcus Bennett, sigmoid resection, smooth recovery, staples out;
    5 Priya Raman, fistulotomy, drainage questions, settling;
    6 Walter Kowalski, inguinal hernia, all-clear check-in awaiting review
      (a candidate to close out in one click);
    7 Sophie Tremblay, appendectomy, wound infection on POD 5 (red flag),
      call and same-day visit, now settled;
    8 Raymond Ashby, rectal cancer surgery, longest thread, dizziness red flag
      on POD 12 handled by call and visit;
    9 Amara Nwosu, ventral hernia, a worried swelling question awaiting a reply;
    10 Daniel Cho, fissure sphincterotomy, minimal contact: closed out from the
      check-in.

Everything here is invented. No real patient data.

Rows are written the way the api writes them, and every audit event the api
would record is recorded (patient.created, check_in.submitted,
check_in.reviewed, message.sent, message.read, touchpoint.logged), so
`reconcile` stays clean and the chain still verifies. Two deliberate
differences from live use, both visible in the log:

  * business rows are backdated (created_at, occurred_at, read_at,
    reviewed_at), but audit rows carry the real time the seed ran: the log
    records when something was written, and this is when;
  * the audit actor is SEED_ACTOR and metadata carries "seed": SEED_TAG, so a
    seeded event is never mistaken for something the clinician did.
    (reviewed_by / logged_by on the rows themselves name the clinician,
    because the UI needs a user there.)

Usage, from apps/api with the same .env the api uses locally:

    uv run python -m clinically_anchored_api.seed_trial_patients --dry-run
    uv run python -m clinically_anchored_api.seed_trial_patients --email you@example.com

Re-running is safe: the demo clinic, membership and procedures are reused,
and a patient that already exists (matched by its synthetic contact address)
is skipped, never duplicated. Times are relative to the day it runs, so the
demo looks current whenever it's seeded.
"""

import argparse
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from clinically_anchored_api.core.rules import evaluate_red_flags

TORONTO = ZoneInfo("America/Toronto")
SEED_TAG = "trial-patients-v1"
SEED_ACTOR = {"type": "system", "id": "seed_trial_patients"}
DEMO_CLINIC_NAME = "Major Mackenzie Surgery (demo)"
SOURCE_CLINIC_ID = "00000000-0000-0000-0000-0000000000c1"  # dev clinic (migrations 2 and 7)


# --- the plan (pure data; no database) ------------------------------------------------


@dataclass
class Msg:
    sender: str  # "patient" | "clinician"
    at: datetime
    body: str
    read_at: datetime | None = None  # patient messages only; set by _mark_reads


@dataclass
class CheckIn:
    at: datetime
    post_op_day: int
    answers: dict
    reviewed_at: datetime | None = None
    expect_red_flag: bool = False


@dataclass
class ManualTouchpoint:
    at: datetime
    kind: str  # "call" | "visit" | "email"
    duration_minutes: int
    note: str


@dataclass
class PatientPlan:
    label: str
    full_name: str
    contact: str
    procedure: str  # must match a procedure name in the clinic
    created_at: datetime
    messages: list[Msg]
    check_ins: list[CheckIn] = field(default_factory=list)
    touchpoints: list[ManualTouchpoint] = field(default_factory=list)


class Clock:
    """Local (Toronto) times relative to the day the seed runs."""

    def __init__(self, now: datetime):
        self.now = now.astimezone(TORONTO)
        self.today = self.now.date()

    def day(self, offset: int, hhmm: str) -> datetime:
        hour, minute = (int(x) for x in hhmm.split(":"))
        d = self.today + timedelta(days=offset)
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TORONTO)

    def ago(self, minutes: int) -> datetime:
        return self.now - timedelta(minutes=minutes)


def _mark_reads(messages: list[Msg]) -> None:
    """A patient message counts as read shortly before the next clinician
    reply; one with no reply after it stays unread (awaiting reply)."""
    for i, msg in enumerate(messages):
        if msg.sender != "patient":
            continue
        reply = next((m for m in messages[i + 1 :] if m.sender == "clinician"), None)
        if reply is not None:
            msg.read_at = max(msg.at + timedelta(minutes=2), reply.at - timedelta(minutes=4))


def _p(at, body):
    return Msg("patient", at, body)


def _c(at, body):
    return Msg("clinician", at, body)


def _more_patients(t: Clock) -> list[PatientPlan]:
    """Trial Patients 4-10: a broader mix of procedures and states."""

    # Trial Patient 4: sigmoid colon resection, day -12. Smooth recovery, 18 messages.
    p4 = PatientPlan(
        label="Trial Patient 4",
        full_name="Marcus Bennett",
        contact="trial-patient-4@example.com",
        procedure="Sigmoid colon resection",
        created_at=t.day(-12, "15:30"),
        messages=[
            _p(t.day(-11, "09:05"),
               "Hi Dr. Moore, it's Marcus Bennett. Home since last night after my sigmoid "
               "resection. Is it normal to be this tired?"),
            _c(t.day(-11, "12:40"),
               "Hi Marcus, yes, very normal. Your body is putting a lot of energy into healing. "
               "Short walks several times a day, small frequent meals, and rest when you need "
               "to. Let me know if you get a fever, vomiting, or your belly swells up."),
            _p(t.day(-10, "08:30"),
               "Eating small amounts, no bowel movement yet but passing gas."),
            _c(t.day(-10, "11:50"),
               "Passing gas is the important sign. A bowel movement over the next few days is "
               "expected. Keep walking and drinking fluids, and use the stool softener on your "
               "discharge sheet."),
            _p(t.day(-9, "18:20"),
               "Filled in the check-in. Had a small loose bowel movement this afternoon."),
            _c(t.day(-9, "20:15"),
               "Thanks Marcus, that's a good sign and your check-in looks fine. Keep doing what "
               "you're doing."),
            _p(t.day(-7, "10:10"),
               "Can I shower with the staples in? And do I need to cover them?"),
            _c(t.day(-7, "13:30"),
               "Yes, shower daily. Let the water run over the incision and pat it dry. No need "
               "to cover it unless your clothes rub. No baths or swimming until the staples are "
               "out and it's fully healed."),
            _p(t.day(-6, "14:45"),
               "I'm down to one oxycodone a day. Is it ok to stop?"),
            _c(t.day(-6, "17:20"),
               "Yes, stop it as soon as you're comfortable on Tylenol alone. It also helps "
               "keep the bowels moving."),
            _p(t.day(-5, "09:00"),
               "Going to the bathroom 4-5 times a day now, pretty loose. Is that ok?"),
            _c(t.day(-5, "12:10"),
               "That's common for a few weeks after a sigmoid resection. A fibre supplement "
               "and a bit more pasta and rice often firms things up. Call me if you see blood, "
               "go more than 6-8 times a day, or feel dizzy or dried out."),
            _p(t.day(-4, "11:45"),
               "Check-in done. Stools are settling to 2-3 a day."),
            _c(t.day(-4, "14:00"),
               "Wonderful, thanks Marcus."),
            _p(t.day(-3, "16:30"),
               "When can I get back to golf? Asking for a friend."),
            _c(t.day(-3, "19:05"),
               "Putting and short walks are fine now. Full swings and carrying a bag after "
               "about 6 weeks, when the incision has healed well."),
            _p(t.day(-2, "10:20"),
               "The nurse called, staples out tomorrow at 10. Anything I need to do?"),
            _c(t.day(-2, "12:30"),
               "Nothing special. Eat normally and take a Tylenol an hour beforehand if you "
               "like. It takes just a few minutes."),
        ],
        check_ins=[
            CheckIn(
                at=t.day(-9, "18:00"),
                post_op_day=3,
                reviewed_at=t.day(-9, "20:00"),
                answers={
                    "symptoms": ["pain", "fatigue"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "staples",
                    "pain": {
                        "scale": 5,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol", "oxycodone"],
                        "medication_frequency": {
                            "tylenol": "several_times_a_day",
                            "oxycodone": "several_times_a_day",
                        },
                    },
                    "fatigue": {"trend": "same", "interfering_with_activities": True},
                },
            ),
            CheckIn(
                at=t.day(-4, "11:30"),
                post_op_day=8,
                reviewed_at=t.day(-4, "13:45"),
                answers={
                    "symptoms": ["pain", "diarrhea"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "staples",
                    "pain": {
                        "scale": 3,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol"],
                        "medication_frequency": {"tylenol": "several_times_a_day"},
                    },
                    "diarrhea": {
                        "frequency_per_day": 3,
                        "blood_in_stool": False,
                        "duration": "several_days",
                    },
                },
            ),
            CheckIn(
                at=t.day(-1, "19:00"),
                post_op_day=11,
                answers={
                    "symptoms": ["pain", "fatigue"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "staples",
                    "pain": {
                        "scale": 2,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol"],
                        "medication_frequency": {"tylenol": "once_a_day"},
                    },
                    "fatigue": {"trend": "better", "interfering_with_activities": False},
                },
            ),
        ],
        touchpoints=[
            ManualTouchpoint(
                at=t.day(-1, "10:30"),
                kind="visit",
                duration_minutes=15,
                note="Staple removal. Incision healing well.",
            ),
        ],
    )

    # Trial Patient 5: fistulotomy, day -9. Drainage questions, settling; 12 messages.
    p5 = PatientPlan(
        label="Trial Patient 5",
        full_name="Priya Raman",
        contact="trial-patient-5@example.com",
        procedure="Fistulotomy for fistula",
        created_at=t.day(-9, "13:50"),
        messages=[
            _p(t.day(-8, "10:15"),
               "Hi Dr. Moore, Priya here. First day home after the fistula surgery. Is it "
               "normal to have a lot of drainage on the pad?"),
            _c(t.day(-8, "13:40"),
               "Hi Priya, yes. Expect drainage for a few weeks while the wound heals from the "
               "inside out. Wear a pad or gauze and change it when it's damp. Bright red "
               "bleeding that soaks through, or fever, is when to call."),
            _p(t.day(-7, "08:50"), "How often should I do the sitz baths?"),
            _c(t.day(-7, "11:30"),
               "3-4 times a day and after every bowel movement, for 10-15 minutes in a few "
               "inches of warm water. Pat dry gently afterwards."),
            _p(t.day(-6, "19:20"),
               "Check-in done. Drainage is yellowish and more than yesterday. Should I worry?"),
            _c(t.day(-5, "09:10"),
               "I've looked at your check-in. Yellowish drainage that increases a bit in the "
               "first week is expected as the wound cleans itself. Keep up the sitz baths. If "
               "it turns thick and foul-smelling, or you get a fever, tell me."),
            _p(t.day(-4, "12:00"), "Can I go to yoga or the gym?"),
            _c(t.day(-4, "15:10"),
               "Walking is great. Hold off on yoga and gym work for 2 weeks, mostly because of "
               "the sitting and stretching. Then we'll see how it looks."),
            _p(t.day(-3, "17:45"),
               "Constipated since yesterday. I'm taking the softener."),
            _c(t.day(-3, "20:00"),
               "Add the laxative from your discharge sheet tonight, plenty of water, and a "
               "sitz bath after you go. You want the stool soft, not hard."),
            _p(t.day(-1, "09:40"),
               "Much better. Pain is 1-2, drainage is less. Check-in done."),
            _c(t.day(-1, "12:15"),
               "That's great to hear, Priya. Your check-in looks good. Keep it up and message "
               "me if anything changes."),
        ],
        check_ins=[
            CheckIn(
                at=t.day(-6, "19:00"),
                post_op_day=3,
                reviewed_at=t.day(-5, "09:00"),
                answers={
                    "symptoms": ["pain", "discharge"],
                    "pain": {
                        "scale": 4,
                        "trend": "better",
                        "character": "worse_with_bowel_movements",
                        "medications": ["tylenol", "advil"],
                        "medication_frequency": {
                            "tylenol": "several_times_a_day",
                            "advil": "several_times_a_day",
                        },
                    },
                    "discharge": {
                        "trend": "more",
                        "character": "yellow_cloudy",
                        "dressing_change_frequency": "several_times_a_day",
                    },
                },
            ),
            CheckIn(
                at=t.day(-1, "09:30"),
                post_op_day=8,
                reviewed_at=t.day(-1, "12:05"),
                answers={
                    "symptoms": ["pain", "discharge"],
                    "pain": {
                        "scale": 1,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol"],
                        "medication_frequency": {"tylenol": "once_a_day"},
                    },
                    "discharge": {
                        "trend": "less",
                        "character": "clear_watery",
                        "dressing_change_frequency": "once_a_day",
                    },
                },
            ),
        ],
    )

    # Trial Patient 6: inguinal hernia repair, day -15. Quiet; latest check-in is all clear
    # and unreviewed: a candidate to discharge from follow-up in one click. 6 messages.
    p6 = PatientPlan(
        label="Trial Patient 6",
        full_name="Walter Kowalski",
        contact="trial-patient-6@example.com",
        procedure="Inguinal hernia repair (in the groin)",
        created_at=t.day(-15, "11:20"),
        messages=[
            _p(t.day(-14, "10:00"),
               "Dr. Moore, Walter Kowalski. I have bruising that's gone down into my scrotum. "
               "Is that normal?"),
            _c(t.day(-14, "13:30"),
               "Yes, that's common after a groin hernia repair. Bruising and swelling travel "
               "down with gravity and fade over 1-2 weeks. A snug supportive brief and an ice "
               "pack wrapped in a towel for 15-20 minutes helps. Call me if the swelling "
               "becomes tense and very painful."),
            _p(t.day(-12, "18:15"),
               "Check-in done, no issues. Walking about half an hour a day."),
            _c(t.day(-11, "08:40"),
               "Good. Keep walking. No lifting over 10 lb for the next 4-6 weeks."),
            _p(t.day(-2, "09:20"),
               "Two weeks out and feeling great. I work in a warehouse. When can I lift?"),
            _c(t.day(-2, "12:00"),
               "Light duty is fine now. Full lifting is best after 4-6 weeks so the repair can "
               "strengthen. I can write a note for your employer if you need one."),
        ],
        check_ins=[
            CheckIn(
                at=t.day(-12, "18:00"),
                post_op_day=3,
                reviewed_at=t.day(-11, "08:30"),
                answers={
                    "symptoms": ["pain"],
                    "surgery_type": "open",
                    "wound_closure": "absorbing_sutures",
                    "pain": {
                        "scale": 3,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol"],
                        "medication_frequency": {"tylenol": "several_times_a_day"},
                    },
                },
            ),
            CheckIn(
                at=t.day(-1, "08:15"),
                post_op_day=14,
                answers={
                    "symptoms": ["pain"],
                    "surgery_type": "open",
                    "wound_closure": "absorbing_sutures",
                    "pain": {
                        "scale": 1,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": [],
                        "medication_frequency": {},
                    },
                },
            ),
        ],
    )

    # Trial Patient 7: appendectomy, day -10. Wound infection on POD 5 (fever, pus-like
    # discharge): red flag, call, same-day visit. Now settled. 11 messages.
    p7 = PatientPlan(
        label="Trial Patient 7",
        full_name="Sophie Tremblay",
        contact="trial-patient-7@example.com",
        procedure="Appendectomy",
        created_at=t.day(-10, "18:00"),
        messages=[
            _p(t.day(-9, "10:30"),
               "Hi Dr. Moore, Sophie here. Day 1 after my appendectomy. Sore but ok. Can I "
               "shower?"),
            _c(t.day(-9, "13:00"),
               "Hi Sophie, yes, you can shower. Let the water run over the incisions and pat "
               "them dry. No baths or swimming for 2 weeks."),
            _p(t.day(-8, "09:15"),
               "A bit constipated from the painkillers."),
            _c(t.day(-8, "12:40"),
               "Use the stool softener on your discharge sheet, drink lots of water, and cut "
               "back on the oxycodone as soon as Tylenol is enough."),
            _p(t.day(-5, "07:50"),
               "Woke up with chills. My temp is 38.9. One of the incisions looks red and "
               "there's yellow-green drainage."),
            _c(t.day(-5, "08:25"),
               "Sophie, thank you for telling me right away. I've seen your check-in. This "
               "could be a wound infection and I'd like to see it today. I'm calling you now "
               "to arrange a time."),
            _c(t.day(-5, "14:00"),
               "Good to see you today. As discussed: keep the wound open to drain, change the "
               "gauze twice a day, take the antibiotics until they're finished, and call me "
               "if the fever returns or the redness spreads."),
            _p(t.day(-4, "09:30"),
               "Fever is gone. The gauze change stings a bit. Is it ok for me to do it myself?"),
            _c(t.day(-4, "11:45"),
               "Yes, you're doing it right. Rinse gently with clean water and pat dry before "
               "the gauze. Stinging is expected. Let me know if it gets more painful."),
            _p(t.day(-2, "18:40"),
               "Check-in done. No fever for 2 days and the drainage has almost stopped."),
            _c(t.day(-1, "08:30"),
               "Thanks Sophie, that's what I wanted to hear. Finish the antibiotics and keep "
               "the area clean. We'll see how it's looking at your next visit."),
        ],
        check_ins=[
            CheckIn(
                at=t.day(-7, "19:00"),
                post_op_day=3,
                reviewed_at=t.day(-6, "08:00"),
                answers={
                    "symptoms": ["pain", "constipation"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "absorbing_sutures",
                    "pain": {
                        "scale": 4,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol", "oxycodone"],
                        "medication_frequency": {
                            "tylenol": "several_times_a_day",
                            "oxycodone": "once_a_day",
                        },
                    },
                    "constipation": {"days_since_bm": 2, "passing_gas": True, "distension": False},
                },
            ),
            CheckIn(
                at=t.day(-5, "07:40"),
                post_op_day=5,
                reviewed_at=t.day(-5, "08:15"),
                expect_red_flag=True,
                answers={
                    "symptoms": ["fever", "discharge", "pain"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "absorbing_sutures",
                    "pain": {
                        "scale": 5,
                        "trend": "same",
                        "character": "constant",
                        "medications": ["tylenol"],
                        "medication_frequency": {"tylenol": "several_times_a_day"},
                    },
                    "fever": {"took_temperature": True, "temperature_c": 38.9, "chills": True},
                    "discharge": {
                        "trend": "more",
                        "character": "pus_like",
                        "dressing_change_frequency": "several_times_a_day",
                    },
                    "concerns_text": "Chills this morning and one incision is red with "
                    "yellow-green drainage.",
                },
            ),
            CheckIn(
                at=t.day(-2, "18:30"),
                post_op_day=8,
                answers={
                    "symptoms": ["pain", "discharge"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "absorbing_sutures",
                    "pain": {
                        "scale": 2,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol"],
                        "medication_frequency": {"tylenol": "once_a_day"},
                    },
                    "fever": {"took_temperature": True, "temperature_c": 36.9, "chills": False},
                    "discharge": {
                        "trend": "less",
                        "character": "clear_watery",
                        "dressing_change_frequency": "once_a_day",
                    },
                },
            ),
        ],
        touchpoints=[
            ManualTouchpoint(
                at=t.day(-5, "08:30"),
                kind="call",
                duration_minutes=7,
                note="Red-flag check-in (fever 38.9, purulent drainage). Asked to come in today.",
            ),
            ManualTouchpoint(
                at=t.day(-5, "10:30"),
                kind="visit",
                duration_minutes=20,
                note="Superficial wound infection. Opened and drained in clinic; antibiotics "
                "started; gauze changes twice daily.",
            ),
        ],
    )

    # Trial Patient 8: rectal cancer surgery, day -16. Longest thread (26 messages), bowel
    # function questions, dizziness red flag on POD 12 handled by call and same-day visit.
    p8 = PatientPlan(
        label="Trial Patient 8",
        full_name="Raymond Ashby",
        contact="trial-patient-8@example.com",
        procedure="Rectal cancer surgery",
        created_at=t.day(-16, "14:00"),
        messages=[
            _p(t.day(-15, "09:00"),
               "Hi Dr. Moore, Ray Ashby. Home after the rectal surgery. Not sure what to "
               "expect with bowel movements."),
            _c(t.day(-15, "12:30"),
               "Hi Ray, good to hear you're home. Frequent small stools, urgency and some "
               "leakage are common early on after rectal surgery and improve over weeks to "
               "months. A fibre supplement and regular meals help."),
            _p(t.day(-15, "20:10"),
               "Am I allowed to lift my grandson? He's 12 kg."),
            _c(t.day(-14, "08:20"),
               "Not for about 6 weeks. No lifting over 10 lb. Let him climb into your lap "
               "while you sit instead."),
            _p(t.day(-14, "11:15"),
               "Went 8 times today, small amounts each. Is that too many?"),
            _c(t.day(-14, "13:50"),
               "It's a lot but common at this stage. Try to space meals, add the fibre "
               "supplement, and don't hold back on fluids. Call me if there's blood, you "
               "can't keep fluids down, or you feel lightheaded."),
            _p(t.day(-13, "09:30"),
               "The skin around my bottom is sore from wiping. Any suggestions?"),
            _c(t.day(-13, "12:10"),
               "Barrier cream (zinc oxide) after each bowel movement, wipe by patting rather "
               "than rubbing, or rinse with warm water. A sitz bath can also settle it."),
            _p(t.day(-12, "18:45"), "Check-in done. Feeling ok."),
            _c(t.day(-12, "20:30"), "Thanks Ray. Looks good."),
            _p(t.day(-11, "15:00"),
               "When will I hear about the pathology results?"),
            _c(t.day(-11, "17:40"),
               "Usually 1-2 weeks after surgery. My office will call to book a results visit "
               "as soon as it's back. I'll go through it in detail with you then."),
            _p(t.day(-10, "10:20"),
               "Appetite is poor and I've lost about 4 lb."),
            _c(t.day(-10, "14:15"),
               "Common after major surgery. Small meals 5-6 times a day, protein shakes if "
               "you need them. We'll keep an eye on it. Tell me if you can't eat at all."),
            _p(t.day(-8, "08:50"),
               "Eating more and the bowel movements are down to about 5 a day."),
            _c(t.day(-8, "11:30"), "Good progress, Ray."),
            _p(t.day(-7, "19:10"), "Check-in done."),
            _c(t.day(-6, "09:00"),
               "Reviewed, all looks as expected. Keep up the fluids."),
            _p(t.day(-4, "09:20"),
               "Dizzy when I stood up this morning, nearly fell. Check-in done."),
            _c(t.day(-4, "09:50"),
               "Ray, thanks for telling me right away. Dizziness after surgery can mean "
               "dehydration or low blood count. Sit or lie down and drink fluids. I'm calling "
               "you now, and I'd like to see you today and do some bloodwork."),
            _p(t.day(-4, "10:35"), "Ok, I can come this afternoon. My wife will drive."),
            _c(t.day(-4, "16:40"),
               "Thanks for coming in. Your bloodwork is reassuring and mostly you're dry from "
               "the frequent stools. Drink at least 2 litres a day, add an electrolyte drink, "
               "and stand up slowly. Message me tomorrow either way."),
            _p(t.day(-3, "08:30"),
               "Dizziness much better this morning after all the fluids."),
            _c(t.day(-3, "10:45"),
               "Glad to hear it. Keep it up for the next few days."),
            _p(t.day(-1, "11:00"),
               "Check-in done, all fine. When is my results appointment?"),
            _c(t.day(-1, "14:20"),
               "It's booked for next Tuesday at 10:30. My office will confirm by phone."),
        ],
        check_ins=[
            CheckIn(
                at=t.day(-12, "18:30"),
                post_op_day=4,
                reviewed_at=t.day(-12, "20:15"),
                answers={
                    "symptoms": ["pain", "fatigue"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "absorbing_sutures",
                    "pain": {
                        "scale": 4,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol", "oxycodone"],
                        "medication_frequency": {
                            "tylenol": "several_times_a_day",
                            "oxycodone": "once_a_day",
                        },
                    },
                    "fatigue": {"trend": "same", "interfering_with_activities": True},
                },
            ),
            CheckIn(
                at=t.day(-7, "19:00"),
                post_op_day=9,
                reviewed_at=t.day(-6, "08:50"),
                answers={
                    "symptoms": ["pain", "diarrhea"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "absorbing_sutures",
                    "pain": {
                        "scale": 2,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol"],
                        "medication_frequency": {"tylenol": "several_times_a_day"},
                    },
                    "diarrhea": {
                        "frequency_per_day": 5,
                        "blood_in_stool": False,
                        "duration": "several_days",
                    },
                },
            ),
            CheckIn(
                at=t.day(-4, "09:10"),
                post_op_day=12,
                reviewed_at=t.day(-4, "09:40"),
                expect_red_flag=True,
                answers={
                    "symptoms": ["dizziness", "fatigue", "diarrhea"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "absorbing_sutures",
                    "dizziness": {"trend": "worse", "orthostatic": True, "since": "today"},
                    "fatigue": {"trend": "worse", "interfering_with_activities": True},
                    "diarrhea": {
                        "frequency_per_day": 6,
                        "blood_in_stool": False,
                        "duration": "several_days",
                    },
                    "concerns_text": "Lightheaded when I stand up, nearly fell this morning.",
                },
            ),
            CheckIn(
                at=t.day(-1, "10:50"),
                post_op_day=15,
                answers={
                    "symptoms": ["fatigue", "diarrhea"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "absorbing_sutures",
                    "fatigue": {"trend": "better", "interfering_with_activities": False},
                    "diarrhea": {
                        "frequency_per_day": 3,
                        "blood_in_stool": False,
                        "duration": "several_days",
                    },
                },
            ),
        ],
        touchpoints=[
            ManualTouchpoint(
                at=t.day(-4, "09:55"),
                kind="call",
                duration_minutes=8,
                note="Red-flag check-in (orthostatic dizziness). Asked to hydrate and come in "
                "this afternoon.",
            ),
            ManualTouchpoint(
                at=t.day(-4, "14:00"),
                kind="visit",
                duration_minutes=25,
                note="Same-day assessment for dizziness. Bloodwork drawn, reassuring. "
                "Dehydration from frequent stools; oral rehydration advised.",
            ),
        ],
    )

    # Trial Patient 9: ventral hernia repair, day -8. A question awaiting a reply that is
    # not an emergency (swelling): a second one to try the AI draft on. 9 messages.
    p9 = PatientPlan(
        label="Trial Patient 9",
        full_name="Amara Nwosu",
        contact="trial-patient-9@example.com",
        procedure="Ventral hernia repair (on the belly)",
        created_at=t.day(-8, "12:00"),
        messages=[
            _p(t.day(-7, "09:30"),
               "Hi Dr. Moore, Amara Nwosu. Do I need to wear the abdominal binder all the "
               "time?"),
            _c(t.day(-7, "13:15"),
               "Hi Amara, wear it when you're up and about, and take it off to sleep and "
               "shower. It supports the repair and eases the pulling feeling when you move."),
            _p(t.day(-6, "16:00"),
               "There's a firm swelling over the incision. Is that normal?"),
            _c(t.day(-6, "18:20"),
               "That's common after a ventral hernia repair, often a small fluid collection "
               "(seroma). It usually goes away over a few weeks. Call me if it becomes red, "
               "hot, very painful, or you get a fever."),
            _p(t.day(-5, "19:30"), "Check-in done. Pain is about a 4."),
            _c(t.day(-4, "08:45"),
               "Thanks Amara, that's reasonable for this stage. Keep taking the Tylenol "
               "regularly."),
            _p(t.day(-2, "10:10"),
               "Can I drive yet? I need to do the school run."),
            _c(t.day(-2, "12:50"),
               "Once you're off the oxycodone and can brake hard and turn the wheel without "
               "pain, you're fine to drive. For most people that's around 1-2 weeks."),
            _p(t.ago(75),
               "Hi Dr. Moore, the swelling looks bigger this morning and it's a little tender "
               "when I press on it. No redness and no fever. I'm a bit nervous. Should I come "
               "in or just keep watching?"),
        ],
        check_ins=[
            CheckIn(
                at=t.day(-5, "19:00"),
                post_op_day=3,
                reviewed_at=t.day(-4, "08:40"),
                answers={
                    "symptoms": ["pain"],
                    "surgery_type": "open",
                    "wound_closure": "staples",
                    "pain": {
                        "scale": 4,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol", "oxycodone"],
                        "medication_frequency": {
                            "tylenol": "several_times_a_day",
                            "oxycodone": "once_a_day",
                        },
                    },
                },
            ),
            CheckIn(
                at=t.day(-2, "10:00"),
                post_op_day=6,
                reviewed_at=t.day(-2, "12:40"),
                answers={
                    "symptoms": ["pain"],
                    "surgery_type": "open",
                    "wound_closure": "staples",
                    "pain": {
                        "scale": 3,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol"],
                        "medication_frequency": {"tylenol": "several_times_a_day"},
                    },
                },
            ),
            CheckIn(
                at=t.ago(95),
                post_op_day=8,
                answers={
                    "symptoms": ["pain"],
                    "surgery_type": "open",
                    "wound_closure": "staples",
                    "pain": {
                        "scale": 5,
                        "trend": "same",
                        "character": "constant",
                        "medications": ["tylenol"],
                        "medication_frequency": {"tylenol": "several_times_a_day"},
                    },
                    "concerns_text": "The swelling over the incision seems bigger this "
                    "morning.",
                },
            ),
        ],
    )

    # Trial Patient 10: sphincterotomy for fissure, day -8. Minimal contact: the clinician
    # confirms from the check-in that no further follow-up is needed. 5 messages.
    p10 = PatientPlan(
        label="Trial Patient 10",
        full_name="Daniel Cho",
        contact="trial-patient-10@example.com",
        procedure="Sphincterotomy for fissure",
        created_at=t.day(-8, "15:00"),
        messages=[
            _p(t.day(-6, "20:00"),
               "Hi Dr. Moore, Daniel Cho. Sorry if this is a silly question, but the pain I "
               "had before surgery is already gone. Is that normal?"),
            _c(t.day(-5, "08:30"),
               "Not a silly question at all, Daniel. Most people notice relief quickly "
               "because the muscle spasm that caused the pain has been released. You'll still "
               "have some soreness from the incision. Keep taking the stool softener and "
               "drinking fluids."),
            _p(t.day(-4, "12:00"),
               "Can I get back to cycling and the gym?"),
            _c(t.day(-4, "15:20"),
               "Walking is fine now. Give it another week or two before cycling, mostly for "
               "comfort. The gym is fine as soon as it feels ok, no heavy straining."),
            _c(t.day(-1, "10:05"),
               "Hi Daniel, I've looked at your check-in and everything looks great. You don't "
               "need any further follow-up beyond your 6-week visit. Message me any time if "
               "something changes."),
        ],
        check_ins=[
            CheckIn(
                at=t.day(-1, "08:00"),
                post_op_day=7,
                reviewed_at=t.day(-1, "10:00"),
                answers={
                    "symptoms": ["pain", "bleeding"],
                    "pain": {
                        "scale": 1,
                        "trend": "better",
                        "character": "worse_with_bowel_movements",
                        "medications": ["tylenol"],
                        "medication_frequency": {"tylenol": "once_a_day"},
                    },
                    "bleeding": {
                        "trend": "same",
                        "timing": "with_bowel_movements",
                        "description": "tissue_paper_only",
                        "dressing_change_frequency": "once_a_day",
                        "started_prescribed_medication": False,
                    },
                },
            ),
        ],
    )

    return [p4, p5, p6, p7, p8, p9, p10]


def build_plan(now: datetime) -> list[PatientPlan]:
    t = Clock(now)

    # Trial Patient 1: laparoscopic cholecystectomy, day -7. Uneventful.
    p1 = PatientPlan(
        label="Trial Patient 1",
        full_name="Elena Marchetti",
        contact="trial-patient-1@example.com",
        procedure="Cholecystectomy",
        created_at=t.day(-7, "15:10"),
        messages=[
            _p(t.day(-5, "09:14"),
               "Hi Dr. Moore, it's Elena. I had my gallbladder out two days ago. Is it okay to "
               "shower? The nurse said something about the glue on my incisions but I can't "
               "remember what."),
            _c(t.day(-5, "18:40"),
               "Hi Elena, yes, you can shower now. Let the water run over the incisions and pat "
               "them dry, no scrubbing. The glue will peel off on its own over 1-2 weeks. No "
               "baths, hot tubs or swimming for 2 weeks."),
            _p(t.day(-4, "07:55"),
               "Thanks. I have a sharp pain in my right shoulder, mostly when I lie down. The "
               "incisions themselves feel ok. Is that normal?"),
            _c(t.day(-4, "12:20"),
               "That's very common after laparoscopic surgery. It's referred pain from the gas "
               "we use during the operation and usually settles within a few days. Walking "
               "helps. If you get a fever over 38.5, yellowing of your eyes or skin, or pain "
               "that keeps getting worse, call the office or go to the emergency department."),
            _p(t.day(-2, "20:05"),
               "Shoulder pain is gone. When can I drive and go back to work? I have a desk job."),
            _c(t.day(-1, "08:30"),
               "Glad to hear it. You can drive once you're off the oxycodone and can brake hard "
               "without pain, usually about a week. Desk work is fine whenever you feel up to "
               "it; most people go back in 1-2 weeks. No lifting over 10-15 lb for 4 weeks."),
            _p(t.day(-1, "09:02"), "Perfect, thank you so much!"),
            _c(t.day(-1, "10:15"),
               "You're welcome, Elena. Your check-in looked good. Reach out if anything changes."),
        ],
        check_ins=[
            CheckIn(
                at=t.day(-2, "19:30"),
                post_op_day=5,
                answers={
                    "symptoms": ["pain", "fatigue"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "absorbing_sutures",
                    "pain": {
                        "scale": 2,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol"],
                        "medication_frequency": {"tylenol": "several_times_a_day"},
                    },
                    "fatigue": {"trend": "better", "interfering_with_activities": False},
                },
            ),
        ],
    )

    # Trial Patient 2: laparoscopic right hemicolectomy, day -14. Ileus on POD 7.
    p2 = PatientPlan(
        label="Trial Patient 2",
        full_name="Gordon Whitlock",
        contact="trial-patient-2@example.com",
        procedure="Right hemicolectomy",
        created_at=t.day(-14, "16:45"),
        messages=[
            _p(t.day(-10, "17:30"),
               "Hello Dr. Moore, Gordon Whitlock here. Home today. The nurse said to message you "
               "with questions. Is it ok to sleep in my recliner? Getting out of bed is hard."),
            _c(t.day(-10, "20:10"),
               "Hi Gordon, yes, the recliner is fine; many people find it easier for the first "
               "week. Get up and walk every couple of hours during the day."),
            _p(t.day(-9, "10:20"),
               "Just filled in the check-in. Still haven't had a proper bowel movement, only "
               "passing a bit of gas. Should I be worried?"),
            _c(t.day(-9, "13:05"),
               "Thanks for checking in. Passing gas is the important part, so that's reassuring. "
               "Keep walking, drink plenty of fluids, and take the stool softener on your "
               "discharge sheet. Let me know if you stop passing gas, your belly gets more "
               "swollen, or you start vomiting."),
            _p(t.day(-8, "21:40"),
               "Belly is more bloated tonight. Still some gas. Only had soup for dinner."),
            _c(t.day(-8, "22:15"),
               "Thanks for letting me know. Stick to fluids tonight and keep walking. If the "
               "bloating gets worse, you stop passing gas, or you vomit, go to the emergency "
               "department and tell them you had bowel surgery last week."),
            _p(t.day(-7, "08:15"),
               "Didn't sleep. Very bloated and threw up twice this morning. No gas since "
               "yesterday afternoon."),
            _c(t.day(-7, "08:42"),
               "Gordon, these are signs your bowel may have slowed down or become blocked. "
               "Please go to the emergency department now and bring your discharge papers. "
               "I'm calling you right now as well."),
            _p(t.day(-7, "09:30"), "On our way. My daughter is driving."),
            _p(t.day(-6, "12:10"),
               "Hi Dr. Moore, this is Linda, Gordon's daughter. He's been admitted and they put "
               "a tube in his nose. He wanted me to say thank you for the quick call."),
            _c(t.day(-6, "13:00"),
               "Thank you, Linda. I've been in touch with the team looking after him. This "
               "usually settles in a few days. Encourage him to walk the halls when he can."),
            _p(t.day(-4, "16:20"),
               "Home again as of this afternoon. Things are moving now. They called it an ileus."),
            _c(t.day(-4, "19:10"),
               "Welcome home, Gordon. I'm glad it settled. Start slowly: small meals, low fibre "
               "for the next week, and keep walking. Same rules as before: vomiting, swelling "
               "or no gas means call or go back to emergency."),
            _p(t.day(-3, "11:02"), "What can I eat? Is toast ok? Hospital food was awful."),
            _c(t.day(-3, "14:30"),
               "Toast is fine. Think soft and low fibre: eggs, white rice, pasta, chicken, fish, "
               "bananas, yogurt. Avoid raw vegetables, nuts and corn for now. Small portions, "
               "5-6 times a day."),
            _p(t.day(-3, "14:41"), "Ok thank you. When do the staples come out?"),
            _c(t.day(-3, "16:05"),
               "Usually 10-14 days after surgery. My office will call you to book a quick visit "
               "in the next couple of days."),
            _p(t.day(-3, "16:20"), "Great, thanks."),
            _p(t.day(-2, "15:20"),
               "Thanks for taking the staples out this morning. There's a small red spot at the "
               "bottom of the incision. Is that normal?"),
            _c(t.day(-2, "18:45"),
               "A little redness where the staples were is normal. Keep it clean and dry. If the "
               "redness spreads, the area gets hot or swollen, you see pus, or you have a fever, "
               "let me know right away or call the office."),
            _p(t.day(-1, "09:10"),
               "Redness looks smaller today. My energy is low though, I nap every afternoon."),
            _c(t.day(-1, "12:25"),
               "Good, that sounds like it's settling. Low energy is very normal for a few weeks "
               "after bowel surgery, especially after the extra hospital stay. Short walks, "
               "regular meals, and rest when you need to. It should improve week by week."),
            _p(t.day(-1, "19:00"), "Is it ok to have a glass of wine with dinner?"),
            _c(t.day(-1, "20:30"),
               "One glass is fine now that you're off the oxycodone. Not while you're taking any "
               "opioid pain medication."),
        ],
        check_ins=[
            CheckIn(
                at=t.day(-9, "10:15"),
                post_op_day=5,
                reviewed_at=t.day(-9, "13:00"),
                answers={
                    "symptoms": ["pain", "fatigue", "no_bowel_movement"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "staples",
                    "pain": {
                        "scale": 4,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol", "oxycodone"],
                        "medication_frequency": {
                            "tylenol": "several_times_a_day",
                            "oxycodone": "once_a_day",
                        },
                    },
                    "fatigue": {"trend": "same", "interfering_with_activities": True},
                    "no_bowel_movement": {"days": 2, "distension": False, "nausea_vomiting": False},
                },
            ),
            CheckIn(
                at=t.day(-7, "08:10"),
                post_op_day=7,
                reviewed_at=t.day(-7, "08:38"),
                expect_red_flag=True,
                answers={
                    "symptoms": ["pain", "nausea_vomiting", "no_bowel_movement", "distension"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "staples",
                    "pain": {
                        "scale": 6,
                        "trend": "same",
                        "character": "constant",
                        "medications": ["tylenol", "oxycodone"],
                        "medication_frequency": {
                            "tylenol": "several_times_a_day",
                            "oxycodone": "several_times_a_day",
                        },
                    },
                    "nausea_vomiting": {
                        "trend": "worse",
                        "keeping_fluids_down": False,
                        "vomiting_episodes_today": 2,
                    },
                    "no_bowel_movement": {"days": 4, "distension": True, "nausea_vomiting": True},
                    "distension": {"trend": "worse", "associated_pain": True},
                    "concerns_text": "Very bloated, threw up twice this morning, no gas since "
                    "yesterday afternoon.",
                },
            ),
            CheckIn(
                at=t.day(-1, "08:30"),
                post_op_day=13,
                answers={
                    "symptoms": ["pain", "fatigue"],
                    "surgery_type": "laparoscopic",
                    "wound_closure": "staples",
                    "pain": {
                        "scale": 2,
                        "trend": "better",
                        "character": "coming_and_going",
                        "medications": ["tylenol"],
                        "medication_frequency": {"tylenol": "once_a_day"},
                    },
                    "fatigue": {"trend": "same", "interfering_with_activities": False},
                },
            ),
        ],
        touchpoints=[
            ManualTouchpoint(
                at=t.day(-7, "08:45"),
                kind="call",
                duration_minutes=10,
                note="Red-flag check-in (distension, vomiting, no gas). Advised emergency "
                "department; spoke with the on-call team.",
            ),
            ManualTouchpoint(
                at=t.day(-2, "10:30"),
                kind="visit",
                duration_minutes=15,
                note="Staple removal and wound check. Healing well.",
            ),
        ],
    )

    # Trial Patient 3: hemorrhoid surgery, day -6. Red flag today, reply pending.
    p3 = PatientPlan(
        label="Trial Patient 3",
        full_name="Nadia Haddad",
        contact="trial-patient-3@example.com",
        procedure="Hemorrhoid surgery",
        created_at=t.day(-6, "14:20"),
        messages=[
            _p(t.day(-5, "11:30"),
               "Hi Dr. Moore, I'm one day after my hemorrhoid surgery. The pain is a lot worse "
               "than I expected, about 7/10 even with the medication. Is that normal?"),
            _c(t.day(-5, "14:10"),
               "Hi Nadia, unfortunately yes, this surgery is known for being painful for the "
               "first week. Take Tylenol and Advil regularly on a schedule and use the oxycodone "
               "for breakthrough pain. Sitz baths (a few inches of warm water) for 10-15 minutes, "
               "3-4 times a day and after bowel movements, help a lot."),
            _p(t.day(-5, "14:25"), "Ok. Should I alternate the Tylenol and Advil?"),
            _c(t.day(-5, "17:50"),
               "Yes, you can stagger them so you're taking something every 3 hours or so. "
               "Follow the doses on your discharge sheet and don't go over the daily maximum of "
               "either."),
            _p(t.day(-4, "09:00"), "Haven't had a bowel movement yet and I'm scared to go."),
            _c(t.day(-4, "12:15"),
               "That's a very common fear. Keep taking the stool softener and the laxative on "
               "your discharge sheet, and drink at least 8 glasses of water a day. The "
               "oxycodone makes constipation worse, so use it only when you need it. Going "
               "sooner keeps the stool softer, which makes it less painful."),
            _p(t.day(-3, "15:20"),
               "Still nothing, day 3. Bloated but passing gas. Could someone call me?"),
            _c(t.day(-3, "16:12"),
               "Thanks for the chat, Nadia. As discussed: a second dose of the laxative today "
               "and tomorrow, keep up the fluids, and a sitz bath right after you go. If you're "
               "not passing gas, or still nothing by tomorrow evening, call the office."),
            _p(t.day(-2, "09:50"),
               "Filled in the check-in. Small bowel movement this morning, finally. Painful but "
               "ok. A bit of blood on the toilet paper."),
            _c(t.day(-2, "12:05"),
               "Good news that things are moving. A small amount of blood on the toilet paper is "
               "expected for a couple of weeks. Keep up the softener and sitz baths. Bleeding "
               "that soaks a pad, clots, or feeling faint means go to emergency."),
            _p(t.day(-1, "13:30"),
               "Is it normal to have some leakage? There's yellowish fluid on my pad."),
            _c(t.day(-1, "17:40"),
               "Yes, some yellowish or slightly blood-tinged drainage is normal while the area "
               "heals. Wear a pad or gauze and change it as needed. If it becomes thick or "
               "foul-smelling, or you get a fever, let me know."),
            _p(t.day(-1, "20:10"),
               "Thank you. Can I go back to work next week? I'm a teacher."),
            _c(t.day(-1, "20:45"),
               "Most people need 1-2 weeks off after this surgery, mainly for pain and sitting "
               "comfortably. If you're off the oxycodone and can sit through a class period, "
               "next week is reasonable. A cushion helps."),
            _p(t.ago(110),
               "Had my first proper bowel movement this morning and the pain was a 9, now it's "
               "down to about a 6. There was some blood dripping into the toilet after. Is this "
               "normal or should I come in?"),
        ],
        check_ins=[
            CheckIn(
                at=t.day(-2, "09:40"),
                post_op_day=4,
                reviewed_at=t.day(-2, "12:00"),
                answers={
                    "symptoms": ["pain", "bleeding"],
                    "pain": {
                        "scale": 7,
                        "trend": "same",
                        "character": "worse_with_bowel_movements",
                        "medications": ["tylenol", "advil", "oxycodone"],
                        "medication_frequency": {
                            "tylenol": "several_times_a_day",
                            "advil": "several_times_a_day",
                            "oxycodone": "several_times_a_day",
                        },
                    },
                    "bleeding": {
                        "trend": "same",
                        "timing": "with_bowel_movements",
                        "description": "tissue_paper_only",
                        "dressing_change_frequency": "once_a_day",
                        "started_prescribed_medication": True,
                    },
                },
            ),
            CheckIn(
                at=t.ago(135),
                post_op_day=6,
                expect_red_flag=True,
                answers={
                    "symptoms": ["pain", "bleeding"],
                    "pain": {
                        "scale": 8,
                        "trend": "same",
                        "character": "worse_with_bowel_movements",
                        "medications": ["tylenol", "advil", "oxycodone"],
                        "medication_frequency": {
                            "tylenol": "several_times_a_day",
                            "advil": "several_times_a_day",
                            "oxycodone": "once_a_day",
                        },
                    },
                    "bleeding": {
                        "trend": "same",
                        "timing": "with_bowel_movements",
                        "description": "dripping_into_toilet",
                        "dressing_change_frequency": "several_times_a_day",
                        "started_prescribed_medication": True,
                    },
                    "concerns_text": "First proper bowel movement this morning. Pain was a 9 "
                    "and some blood dripped into the toilet after.",
                },
            ),
        ],
        touchpoints=[
            ManualTouchpoint(
                at=t.day(-3, "16:00"),
                kind="call",
                duration_minutes=8,
                note="Constipation and pain plan reviewed by phone.",
            ),
        ],
    )

    plans = [p1, p2, p3, *_more_patients(t)]
    for plan in plans:
        _mark_reads(plan.messages)
    return plans


def validate_plan(plans: list[PatientPlan], now: datetime) -> list[str]:
    """Problems with the plan, or [] if it's fine to write."""
    problems: list[str] = []
    for plan in plans:
        n = len(plan.messages)
        if not 5 <= n <= 30:
            problems.append(f"{plan.label}: {n} messages, expected 5-30")
        events = (
            [plan.created_at]
            + [m.at for m in plan.messages]
            + [c.at for c in plan.check_ins]
            + [tp.at for tp in plan.touchpoints]
        )
        if any(e >= now for e in events):
            problems.append(f"{plan.label}: an event is in the future")
        if any(e < plan.created_at for e in events):
            problems.append(f"{plan.label}: an event predates the patient record")
        times = [m.at for m in plan.messages]
        if times != sorted(times):
            problems.append(f"{plan.label}: messages out of order")
        for c in plan.check_ins:
            flagged, _ = evaluate_red_flags(c.answers)
            if flagged != c.expect_red_flag:
                problems.append(
                    f"{plan.label}: POD {c.post_op_day} check-in red flag is {flagged}, "
                    f"expected {c.expect_red_flag}"
                )
            if c.reviewed_at is not None and c.reviewed_at <= c.at:
                problems.append(f"{plan.label}: POD {c.post_op_day} reviewed before submitted")
    return problems


def describe(plans: list[PatientPlan]) -> str:
    lines = []
    for plan in plans:
        patient = sum(m.sender == "patient" for m in plan.messages)
        unread = sum(m.sender == "patient" and m.read_at is None for m in plan.messages)
        flags = [c.post_op_day for c in plan.check_ins if evaluate_red_flags(c.answers)[0]]
        pending = [c.post_op_day for c in plan.check_ins if c.reviewed_at is None]
        lines.append(
            f"{plan.label}: {plan.full_name} ({plan.procedure}), "
            f"{len(plan.messages)} messages ({patient} patient, "
            f"{len(plan.messages) - patient} clinician, {unread} awaiting reply), "
            f"{len(plan.check_ins)} check-ins (red flag on POD {flags or 'none'}; "
            f"unreviewed POD {pending or 'none'}), "
            f"{len(plan.touchpoints)} logged calls/visits"
        )
    return "\n".join(lines)


# --- writing ----------------------------------------------------------------------------


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat()


def _audit(supabase, *, clinic_id: str, event_type: str, payload: dict, ref_type: str, ref_id):
    from clinically_anchored_api.core.audit import record_event

    record_event(
        supabase,
        clinic_id=clinic_id,
        event_type=event_type,
        payload=payload,
        metadata={"ref_type": ref_type, "ref_id": ref_id, "actor": SEED_ACTOR, "seed": SEED_TAG},
    )


def _insert(supabase, table: str, values: dict) -> dict:
    result = supabase.table(table).insert(values).execute()
    if not result.data:
        raise RuntimeError(f"{table} row was not saved")
    return result.data[0]


def find_user_id(supabase, email: str) -> str:
    wanted = email.strip().lower()
    page = 1
    while True:
        users = supabase.auth.admin.list_users(page=page, per_page=200)
        for user in users:
            if (user.email or "").lower() == wanted:
                return user.id
        if len(users) < 200:
            raise SystemExit(f"No Supabase Auth user with email {email}. Create it first.")
        page += 1


def ensure_demo_clinic(supabase, user_id: str) -> str:
    found = supabase.table("clinics").select("id").eq("name", DEMO_CLINIC_NAME).execute().data
    if found:
        clinic_id = found[0]["id"]
        print(f"Using existing clinic {DEMO_CLINIC_NAME!r} ({clinic_id})")
    else:
        clinic_id = _insert(supabase, "clinics", {"name": DEMO_CLINIC_NAME})["id"]
        print(f"Created clinic {DEMO_CLINIC_NAME!r} ({clinic_id})")

    member = (
        supabase.table("clinic_members")
        .select("id")
        .eq("clinic_id", clinic_id)
        .eq("user_id", user_id)
        .execute()
        .data
    )
    if not member:
        _insert(
            supabase,
            "clinic_members",
            {"clinic_id": clinic_id, "user_id": user_id, "role": "owner"},
        )
        print("Added the clinician as owner of the demo clinic")

    others = (
        supabase.table("clinic_members")
        .select("clinic_id")
        .eq("user_id", user_id)
        .neq("clinic_id", clinic_id)
        .execute()
        .data
    )
    if others:
        print(
            "WARNING: this user is also a member of other clinics "
            f"({', '.join(r['clinic_id'] for r in others)}). The dashboard opens the first "
            "clinic it gets back, which may not be the demo one."
        )

    have = supabase.table("procedures").select("id").eq("clinic_id", clinic_id).execute().data
    if not have:
        source = (
            supabase.table("procedures")
            .select("name, category")
            .eq("clinic_id", SOURCE_CLINIC_ID)
            .eq("active", True)
            .execute()
            .data
        )
        if not source:
            raise SystemExit("No active procedures in the dev clinic to copy.")
        supabase.table("procedures").insert(
            [
                {
                    "clinic_id": clinic_id,
                    "name": r["name"],
                    "category": r["category"],
                    "active": True,
                }
                for r in source
            ]
        ).execute()
        print(f"Copied {len(source)} procedures into the demo clinic")
    return clinic_id


def seed_patient(supabase, *, clinic_id: str, user_id: str, plan: PatientPlan) -> None:
    exists = (
        supabase.table("patients")
        .select("id")
        .eq("clinic_id", clinic_id)
        .eq("contact", plan.contact)
        .execute()
        .data
    )
    if exists:
        print(f"{plan.label}: already exists ({exists[0]['id']}), skipped")
        return

    procs = (
        supabase.table("procedures")
        .select("id")
        .eq("clinic_id", clinic_id)
        .eq("name", plan.procedure)
        .eq("active", True)
        .execute()
        .data
    )
    if not procs:
        raise SystemExit(f"{plan.label}: no active procedure named {plan.procedure!r}")
    procedure_id = procs[0]["id"]

    patient = _insert(
        supabase,
        "patients",
        {
            "clinic_id": clinic_id,
            "full_name": plan.full_name,
            "contact": plan.contact,
            "created_at": _iso(plan.created_at),
        },
    )
    patient_id = patient["id"]
    _audit(
        supabase,
        clinic_id=clinic_id,
        event_type="patient.created",
        payload={"patient_id": patient_id, "full_name": plan.full_name, "contact": plan.contact},
        ref_type="patient",
        ref_id=patient_id,
    )

    # Everything else in time order, so the audit chain reads like the story.
    steps: list[tuple[datetime, int, object]] = []
    for c in plan.check_ins:
        steps.append((c.at, 0, ("check_in", c)))
        if c.reviewed_at is not None:
            steps.append((c.reviewed_at, 1, ("review", c)))
    for m in plan.messages:
        steps.append((m.at, 2, ("message", m)))
        if m.read_at is not None:
            steps.append((m.read_at, 1, ("read", m)))
    for tp in plan.touchpoints:
        steps.append((tp.at, 3, ("touchpoint", tp)))
    steps.sort(key=lambda s: (s[0], s[1]))

    check_in_ids: dict[int, str] = {}
    message_ids: dict[int, str] = {}
    for _, _, (kind, item) in steps:
        if kind == "check_in":
            is_red_flag, _ = evaluate_red_flags(item.answers)
            row = _insert(
                supabase,
                "check_ins",
                {
                    "clinic_id": clinic_id,
                    "patient_id": patient_id,
                    "procedure_id": procedure_id,
                    "post_op_day": item.post_op_day,
                    "answers": item.answers,
                    "is_red_flag": is_red_flag,
                    "created_at": _iso(item.at),
                },
            )
            check_in_ids[id(item)] = row["id"]
            _audit(
                supabase,
                clinic_id=clinic_id,
                event_type="check_in.submitted",
                payload={
                    "check_in_id": row["id"],
                    "patient_id": patient_id,
                    "procedure_id": row["procedure_id"],
                    "post_op_day": row["post_op_day"],
                    "answers": row["answers"],
                    "is_red_flag": row["is_red_flag"],
                },
                ref_type="check_in",
                ref_id=row["id"],
            )
        elif kind == "review":
            check_in_id = check_in_ids[id(item)]
            row = (
                supabase.table("check_ins")
                .update({"reviewed_at": _iso(item.reviewed_at), "reviewed_by": user_id})
                .eq("id", check_in_id)
                .execute()
                .data[0]
            )
            _audit(
                supabase,
                clinic_id=clinic_id,
                event_type="check_in.reviewed",
                payload={
                    "check_in_id": check_in_id,
                    "reviewed_by": user_id,
                    "reviewed_at": row["reviewed_at"],
                },
                ref_type="check_in",
                ref_id=check_in_id,
            )
        elif kind == "message":
            row = _insert(
                supabase,
                "messages",
                {
                    "clinic_id": clinic_id,
                    "patient_id": patient_id,
                    "sender": item.sender,
                    "body": item.body,
                    "created_at": _iso(item.at),
                },
            )
            message_ids[id(item)] = row["id"]
            if item.sender == "clinician":
                # Same auto contact-log entry the send path writes (core/touchpoints.py).
                tp = _insert(
                    supabase,
                    "touchpoints",
                    {
                        "clinic_id": clinic_id,
                        "patient_id": patient_id,
                        "kind": "message",
                        "source": "auto",
                        "occurred_at": row["created_at"],
                        "logged_by": None,
                        "created_at": row["created_at"],
                    },
                )
                _audit_touchpoint(supabase, clinic_id, tp)
            _audit(
                supabase,
                clinic_id=clinic_id,
                event_type="message.sent",
                payload={
                    "message_id": row["id"],
                    "patient_id": patient_id,
                    "sender": row["sender"],
                    "body": row["body"],
                },
                ref_type="message",
                ref_id=row["id"],
            )
        elif kind == "read":
            message_id = message_ids[id(item)]
            row = (
                supabase.table("messages")
                .update({"read_at": _iso(item.read_at)})
                .eq("id", message_id)
                .execute()
                .data[0]
            )
            _audit(
                supabase,
                clinic_id=clinic_id,
                event_type="message.read",
                payload={
                    "message_id": message_id,
                    "patient_id": patient_id,
                    "read_by": user_id,
                    "read_at": row["read_at"],
                },
                ref_type="message",
                ref_id=message_id,
            )
        elif kind == "touchpoint":
            tp = _insert(
                supabase,
                "touchpoints",
                {
                    "clinic_id": clinic_id,
                    "patient_id": patient_id,
                    "kind": item.kind,
                    "source": "manual",
                    "occurred_at": _iso(item.at),
                    "duration_minutes": item.duration_minutes,
                    "note": item.note,
                    "logged_by": user_id,
                    "created_at": _iso(item.at),
                },
            )
            _audit_touchpoint(supabase, clinic_id, tp)

    print(f"{plan.label}: created {plan.full_name} ({patient_id})")


def _audit_touchpoint(supabase, clinic_id: str, row: dict) -> None:
    _audit(
        supabase,
        clinic_id=clinic_id,
        event_type="touchpoint.logged",
        payload={
            "touchpoint_id": row["id"],
            "patient_id": row["patient_id"],
            "kind": row["kind"],
            "source": row["source"],
            "occurred_at": row["occurred_at"],
            "duration_minutes": row["duration_minutes"],
            "note": row["note"],
            "logged_by": row["logged_by"],
        },
        ref_type="touchpoint",
        ref_id=row["id"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--email", help="the clinician's Supabase Auth email (required to write)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    plans = build_plan(now)
    problems = validate_plan(plans, now)
    print(describe(plans))
    if problems:
        print("\nPlan problems, nothing written:\n  " + "\n  ".join(problems))
        return 1
    if args.dry_run:
        print("\nDry run: nothing written.")
        return 0
    if not args.email:
        parser.error("--email is required unless --dry-run")

    from clinically_anchored_api.core.db import get_supabase

    supabase = get_supabase()
    user_id = find_user_id(supabase, args.email)
    clinic_id = ensure_demo_clinic(supabase, user_id)
    for plan in plans:
        seed_patient(supabase, clinic_id=clinic_id, user_id=user_id, plan=plan)
    print("\nDone. Check with: uv run python -m clinically_anchored_api.core.reconcile "
          f"--clinic {clinic_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
