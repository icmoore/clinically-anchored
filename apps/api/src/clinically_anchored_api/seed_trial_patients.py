"""Seed synthetic trial patients into a clinician's demo clinic (Messaging v2).

Creates (or reuses) a separate demo clinic, adds the clinician as its owner, copies the
active procedure list from the dev clinic, then writes invented patients, each with an
EPISODE (surgery date, procedure, schedule version), backdated check-in, messages,
contact-log entries and reviews, chosen so that every state the schedule engine
(core/schedule.py) can report is on screen:

   1  Elena Marchetti      cholecystectomy: window 4 open, unused; late check-in (window 1 missed)
   2  Gordon Whitlock      right hemicolectomy: window 5 open; red-flag check-in (ileus), call and
                           ED visit
   3  Nadia Haddad         hemorrhoid surgery: red-flag check-in today and a message awaiting a
                           reply (the one to try "Draft a reply with AI" on)
   4  Marcus Bennett       sigmoid resection: window 4 used; the clinic wrote last, a reply is open
   5  Priya Raman          fistulotomy: window 5 used, window 6 opens tomorrow
   6  Walter Kowalski      inguinal hernia: an all-clear check-in not yet reviewed; no messages
   7  Sophie Tremblay      appendectomy: wound infection (red flag), call and same-day visit
   8  Raymond Ashby        rectal cancer surgery: checked in on day 12 (windows 1-3 missed) with a
                           dizziness red flag; the longest thread; window 7 open
   9  Amara Nwosu          ventral hernia: a worried swelling question awaiting a reply
  10  Daniel Cho           sphincterotomy: DISCHARGED by the clinic
  11  Hannah Doucet        seton surgery: check-in pending (messaging locked); the clinic has
                           written and she has a reply open
  12  Tomas Reyes          anal advancement flap: checked in, waiting for window 1 to open
  13  Beatrice Lang        colon resection: the schedule ran out and the care period closed itself
  14  Gerald Fox           right hemicolectomy on schedule v2 (only with
                           --include-alternate-schedule)

Everything here is invented. No real patient data.

How the plan is built. Each patient's surgery date is `today - pod` days (Toronto), so what the
engine reports depends on the day the seed runs, and each patient declares the state they should
be in NOW. `validate_plan` runs every patient's facts through the real engine and refuses to write
anything if the plan breaks a rule (one scheduled message per window, a reply answers the latest
clinic message, nothing after a close) or lands in a different state than declared. It runs
against the schedule versions in the database, so editing v1's offsets (they are still being
confirmed) can make the plan refuse, with the reason. Run it between a morning and an evening: a
few events are "an hour or two ago".

Rows are written the way the api writes them, with the same audit events (patient.created,
episode.created, check_in.submitted incl. missed_intervals, check_in.reviewed, message.sent with
episode / origin / window / category, message.read, touchpoint.logged, episode.closed), so
`reconcile` stays clean and the chain verifies. They are inserted directly, not through the
database's send functions, because those stamp "now" and these are backdated. Two deliberate
differences from live use, both visible in the log:

  * business rows are backdated (created_at, occurred_at, read_at, reviewed_at, closed_at), but
    audit rows carry the real time the seed ran: the log records when something was written;
  * the audit actor is SEED_ACTOR and metadata carries "seed": SEED_TAG, so a seeded event is
    never mistaken for something the clinician did. (reviewed_by / logged_by / closed_by on the
    rows themselves name the clinician, because the UI needs a user there.)

Usage, from apps/api with the same .env the api uses locally:

    uv run python -m clinically_anchored_api.seed_trial_patients --dry-run
    uv run python -m clinically_anchored_api.seed_trial_patients --email you@example.com

Needs migration 9 applied (after supabase/scripts/reset_trial_data.sh). Re-running is safe: the
demo clinic, membership and procedures are reused, and a patient that already exists (matched by
its synthetic contact address) is skipped, never duplicated.

--clinic dev fills the shared dev clinic instead of the separate demo clinic (the same patients,
so each account has its own copy to click through). The dev clinic must already exist and the
account must already be a member of it: this never adds a membership to it.

--include-alternate-schedule also adds schedule version 2 (offsets 2,5,10,17,...) and patient 14.
Version 2 then becomes the NEWEST schedule, and every patient added from the dashboard afterwards
gets it. Leave it off unless you mean that.
"""

import argparse
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from clinically_anchored_api.core import categories
from clinically_anchored_api.core.rules import evaluate_red_flags
from clinically_anchored_api.core.schedule import (
    EpisodeState,
    Phase,
    Schedule,
    episode_end,
    episode_state,
    windows,
)

TORONTO = ZoneInfo("America/Toronto")
SEED_TAG = "trial-patients-v2"
SEED_ACTOR = {"type": "system", "id": "seed_trial_patients"}
DEMO_CLINIC_NAME = "Major Mackenzie Surgery (demo)"
SOURCE_CLINIC_ID = "00000000-0000-0000-0000-0000000000c1"  # dev clinic (migrations 2 and 7)

# The schedule versions the plan is written for. The database's copies win when writing (see
# the module docstring); these are what migration 9 seeds and what a dry run assumes.
DEFAULT_SCHEDULES = {
    1: Schedule(1, (2, 3, 5, 7, 14, 21, 28, 35, 42), 7, time(7, 0), "America/Toronto"),
    2: Schedule(2, (2, 5, 10, 17, 24, 31, 38, 45, 52), 7, time(7, 0), "America/Toronto"),
}


# --- the plan (pure data; no database) ------------------------------------------------


@dataclass
class Msg:
    sender: str  # "patient" | "clinician"
    at: datetime
    body: str
    category: str | None = None  # a patient's scheduled message: what they said it was about
    reply: bool = False  # a patient's one reply to the clinic's latest message
    read_at: datetime | None = None  # patient messages only; set by _mark_reads


@dataclass
class CheckIn:
    at: datetime
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
class Closed:
    at: datetime | None  # None for a schedule that ran out: the engine says when
    reason: str  # "discharged" | "schedule_complete"


@dataclass
class Expect:
    """Where the patient must be NOW, per the engine."""

    phase: Phase
    missed: tuple[int, ...] = ()
    reply: bool = False  # the patient can send a reply: a clinic message awaits one, not closed


@dataclass
class PatientPlan:
    label: str
    full_name: str
    contact: str
    procedure: str  # must match a procedure name in the clinic
    surgery_date: date
    schedule_version: int
    created_at: datetime
    expect: Expect
    check_in: CheckIn | None = None
    messages: list[Msg] = field(default_factory=list)
    touchpoints: list[ManualTouchpoint] = field(default_factory=list)
    closed: Closed | None = None


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


class Surgery:
    """One patient's timeline: `at(3, "08:30")` is post-op day 3 at 08:30 Toronto time, and
    `pod` is today's post-op day (so today's events use `Clock.ago` instead)."""

    def __init__(self, t: Clock, pod: int):
        self.t, self.pod = t, pod
        self.date = t.today - timedelta(days=pod)

    def at(self, day: int, hhmm: str) -> datetime:
        return self.t.day(day - self.pod, hhmm)


def _p(at, body, category):
    return Msg("patient", at, body, category=category)


def _r(at, body):
    return Msg("patient", at, body, reply=True)


def _c(at, body):
    return Msg("clinician", at, body)


def _mark_reads(plan: PatientPlan) -> None:
    """A patient message counts as read shortly before the next clinician message. One with no
    answer stays unread (awaiting reply) -- unless it is only a courtesy reply, which the clinic
    has seen, or the care period is closed and nothing is left waiting."""
    messages = plan.messages
    for i, msg in enumerate(messages):
        if msg.sender != "patient":
            continue
        reply = next((m for m in messages[i + 1 :] if m.sender == "clinician"), None)
        if reply is not None:
            msg.read_at = max(msg.at + timedelta(minutes=2), reply.at - timedelta(minutes=4))
        elif msg.reply:
            msg.read_at = msg.at + timedelta(minutes=30)
        elif plan.closed is not None:
            msg.read_at = msg.at + timedelta(hours=1)


def _pain(scale, trend="better", character="coming_and_going", meds=("tylenol",)):
    return {
        "scale": scale,
        "trend": trend,
        "character": character,
        "medications": list(meds),
        "medication_frequency": {m: "several_times_a_day" for m in meds},
    }


def _answers(symptoms, *, abdominal=True, wound="staples", surgery="laparoscopic", **parts):
    answers: dict = {"symptoms": list(symptoms)}
    if abdominal:
        answers["surgery_type"] = surgery
        answers["wound_closure"] = wound
    answers.update(parts)
    return answers


def build_plan(now: datetime, include_alternate_schedule: bool = False) -> list[PatientPlan]:
    t = Clock(now)
    plans: list[PatientPlan] = []

    def new(label, name, procedure, pod, expect, *, version=1, created="15:00", **kw):
        s = Surgery(t, pod)
        plan = PatientPlan(
            label=label,
            full_name=name,
            contact=f"trial-patient-{label.split()[-1]}@example.com",
            procedure=procedure,
            surgery_date=s.date,
            schedule_version=version,
            created_at=s.at(0, created),
            expect=expect,
            **kw,
        )
        plans.append(plan)
        return s, plan

    # 1: laparoscopic cholecystectomy, day 8. Checked in on day 3, so window 1 went by.
    s = Surgery(t, 8)
    new(
        "Trial Patient 1", "Elena Marchetti", "Cholecystectomy", 8,
        Expect(Phase.OPEN, missed=(1,)),
        created="15:10",
        check_in=CheckIn(
            s.at(3, "19:30"),
            _answers(["pain", "fatigue"], wound="absorbing_sutures", pain=_pain(2),
                     fatigue={"trend": "better", "interfering_with_activities": False}),
        ),
        messages=[
            _p(s.at(3, "20:05"),
               "Hi Dr. Moore, it's Elena. Is it okay to shower? Also a sharp pain in my right "
               "shoulder, mostly when I lie down. The incisions themselves feel ok.",
               "symptom_or_wound"),
            _c(s.at(4, "08:30"),
               "Hi Elena, yes, you can shower now; pat the incisions dry, no scrubbing. The "
               "shoulder pain is referred pain from the gas used during surgery and usually "
               "settles in a few days. Walking helps."),
            _r(s.at(4, "08:52"), "Thanks, that's reassuring."),
            _p(s.at(6, "20:05"),
               "Shoulder pain is gone. When can I drive and go back to work? I have a desk job.",
               "recovery_update"),
            _c(s.at(7, "08:30"),
               "Glad to hear it. You can drive once you're off the oxycodone and can brake hard "
               "without pain, usually about a week. Desk work whenever you feel up to it."),
            _r(s.at(7, "09:02"), "Perfect, thank you so much!"),
        ],
    )

    # 2: right hemicolectomy, day 15. Red-flag check-in on day 4 (ileus): call, ED visit.
    s = Surgery(t, 15)
    new(
        "Trial Patient 2", "Gordon Whitlock", "Right hemicolectomy", 15,
        Expect(Phase.OPEN, missed=(1,), reply=True),
        created="14:00",
        check_in=CheckIn(
            s.at(4, "08:05"),
            _answers(
                ["pain", "nausea_vomiting", "no_bowel_movement", "distension"],
                pain=_pain(6, "same", meds=("tylenol", "oxycodone")),
                nausea_vomiting={"trend": "worse", "keeping_fluids_down": False,
                                 "vomiting_episodes_today": 2},
                no_bowel_movement={"days": 3, "distension": True, "nausea_vomiting": True},
                distension={"trend": "worse", "associated_pain": True},
            ),
            reviewed_at=s.at(4, "08:38"),
            expect_red_flag=True,
        ),
        messages=[
            _p(s.at(4, "08:20"),
               "Belly is very swollen and I'm nauseous. No gas since yesterday. I just filled in "
               "the check-in.", "symptom_or_wound"),
            _c(s.at(4, "09:00"),
               "Gordon, please go to the emergency department now. I've called ahead and they "
               "are expecting you."),
            _r(s.at(4, "09:25"), "Leaving now."),
            _p(s.at(6, "10:10"),
               "Home since this morning. Passing gas, and I kept down some soup.",
               "recovery_update"),
            _c(s.at(6, "12:30"), "That's what we wanted to hear. Small meals, keep walking."),
            _r(s.at(6, "13:00"), "Will do, thanks."),
            _p(s.at(12, "09:30"),
               "Can I stop the oxycodone now? And is it ok to keep taking my aspirin?",
               "medication"),
            _c(s.at(12, "11:00"),
               "Yes to stopping the oxycodone if Tylenol is enough. Restart the aspirin now; "
               "take it with food."),
            _r(s.at(12, "11:20"), "Great, thank you."),
            _c(s.at(14, "12:00"),
               "Staples are out and the incision looks good. See you in clinic."),
        ],
        touchpoints=[
            ManualTouchpoint(s.at(4, "08:50"), "call", 12,
                             "Called about no gas, distension, vomiting. Sent to ED."),
            ManualTouchpoint(s.at(4, "11:30"), "visit", 240,
                             "ED assessment: ileus, managed conservatively, discharged day 6."),
            ManualTouchpoint(s.at(14, "10:00"), "visit", 15, "Staple removal."),
        ],
    )

    # 3: hemorrhoid surgery, day 6. Red flag today and a message waiting for a reply.
    new(
        "Trial Patient 3", "Nadia Haddad", "Hemorrhoid surgery", 6,
        Expect(Phase.USED, missed=(1, 2)),
        created="10:00",
        check_in=CheckIn(
            t.ago(135),
            _answers(
                ["pain", "bleeding"], abdominal=False,
                pain=_pain(8, "worse", "worse_with_bowel_movements",
                           ("tylenol", "advil", "oxycodone")),
                bleeding={"trend": "same", "timing": "with_bowel_movements",
                          "description": "tissue_paper_only",
                          "dressing_change_frequency": "once_a_day",
                          "started_prescribed_medication": True},
            ),
            expect_red_flag=True,
        ),
        messages=[
            _p(t.ago(120),
               "The pain is much worse today, 8 out of 10, especially after going to the "
               "bathroom. Tylenol and Advil aren't touching it. Should I be doing something "
               "different?", "symptom_or_wound"),
        ],
    )

    # 4: sigmoid colon resection, day 12. The clinic wrote last; she has a reply open.
    s = Surgery(t, 12)
    new(
        "Trial Patient 4", "Marcus Bennett", "Sigmoid colon resection", 12,
        Expect(Phase.USED, missed=(1,), reply=True),
        created="15:30",
        check_in=CheckIn(
            s.at(3, "18:00"),
            _answers(["pain", "fatigue"],
                     pain=_pain(5, meds=("tylenol", "oxycodone")),
                     fatigue={"trend": "same", "interfering_with_activities": True}),
            reviewed_at=s.at(3, "20:00"),
        ),
        messages=[
            _p(s.at(3, "18:20"),
               "Home since last night after my sigmoid resection. Is it normal to be this "
               "tired? Small loose bowel movement this afternoon.", "recovery_update"),
            _c(s.at(3, "20:15"),
               "Very normal, and the bowel movement is a good sign. Short walks, small frequent "
               "meals, rest when you need to."),
            _r(s.at(4, "08:30"), "Thanks, will do."),
            _p(s.at(6, "10:10"),
               "Can I shower with the staples in? Do I need to cover them?", "recovery_update"),
            _c(s.at(6, "13:30"),
               "Yes, shower daily and pat dry. No baths or swimming until the staples are out."),
            _p(s.at(9, "16:30"), "When can I get back to golf? Asking for a friend.",
               "recovery_update"),
            _c(s.at(9, "19:05"),
               "Putting and short walks now. Full swings and carrying a bag after about 6 weeks."),
            _r(s.at(10, "09:00"), "Ha, thanks. Staples out tomorrow at 10."),
            _c(s.at(11, "12:30"),
               "Nothing special to do. Eat normally and take a Tylenol an hour beforehand."),
        ],
        touchpoints=[ManualTouchpoint(s.at(11, "10:20"), "visit", 15, "Staples removed.")],
    )

    # 5: fistulotomy, day 20. Window 5 used; window 6 opens tomorrow.
    s = Surgery(t, 20)
    new(
        "Trial Patient 5", "Priya Raman", "Fistulotomy for fistula", 20,
        Expect(Phase.USED, missed=(1, 2)),
        created="11:00",
        check_in=CheckIn(
            s.at(5, "09:40"),
            _answers(["pain", "discharge"], abdominal=False, pain=_pain(4),
                     discharge={"trend": "better", "character": "clear",
                                "dressing_change_frequency": "twice_a_day"}),
            reviewed_at=s.at(5, "10:30"),
        ),
        messages=[
            _p(s.at(5, "10:15"),
               "There's a fair bit of drainage on the dressing. Is that expected?",
               "symptom_or_wound"),
            _c(s.at(5, "13:00"),
               "Yes, a fistulotomy wound drains for a few weeks. Change the dressing when it is "
               "damp, and call us if it turns thick, foul-smelling or the pain jumps."),
            _p(s.at(9, "08:45"), "How often should I be changing the dressing now?",
               "recovery_update"),
            _c(s.at(9, "11:20"), "Once or twice a day is fine, whenever it's wet."),
            _p(s.at(16, "19:10"), "Settling nicely. Almost no drainage now.", "recovery_update"),
            _c(s.at(17, "09:00"), "Excellent. You can drop to one dressing a day."),
            _r(s.at(17, "09:30"), "Thank you!"),
        ],
    )

    # 6: inguinal hernia repair, day 4. An all-clear check-in waiting to be closed out.
    s = Surgery(t, 4)
    new(
        "Trial Patient 6", "Walter Kowalski", "Inguinal hernia repair (in the groin)", 4,
        Expect(Phase.OPEN, missed=(1,)),
        created="13:00",
        check_in=CheckIn(
            s.at(3, "14:00"),
            _answers(["pain"], wound="absorbing_sutures", pain=_pain(3)),
        ),
    )

    # 7: appendectomy, day 9. Wound infection on day 5: call and same-day visit.
    s = Surgery(t, 9)
    new(
        "Trial Patient 7", "Sophie Tremblay", "Appendectomy", 9,
        Expect(Phase.USED, missed=(1, 2)),
        created="12:00",
        check_in=CheckIn(
            s.at(5, "07:40"),
            _answers(
                ["fever", "discharge", "pain"], wound="absorbing_sutures",
                fever={"took_temperature": True, "temperature_c": 39.1, "chills": True},
                discharge={"trend": "worse", "character": "pus_like",
                           "dressing_change_frequency": "every_2_3_hours"},
                pain=_pain(5, "worse"),
            ),
            reviewed_at=s.at(5, "08:15"),
            expect_red_flag=True,
        ),
        messages=[
            _p(s.at(5, "07:55"),
               "Fever of 39.1 this morning and the wound is red and leaking yellow fluid.",
               "symptom_or_wound"),
            _c(s.at(5, "08:25"),
               "Sophie, please come to the clinic this morning. I've booked you at 10:30."),
            _r(s.at(5, "08:50"), "On my way."),
            _p(s.at(8, "09:10"),
               "The antibiotic is upsetting my stomach. Should I take it with food?",
               "medication"),
            _c(s.at(8, "11:00"),
               "Yes, take it with a meal and keep going to the end of the course."),
            _r(s.at(8, "11:20"), "Thanks, that helped."),
        ],
        touchpoints=[
            ManualTouchpoint(s.at(5, "08:20"), "call", 10, "Fever 39.1 and purulent discharge."),
            ManualTouchpoint(s.at(5, "10:30"), "visit", 35,
                             "Wound opened and drained, antibiotics started."),
        ],
    )

    # 8: rectal cancer surgery, day 30. Late check-in on day 12 (dizziness): the longest thread.
    s = Surgery(t, 30)
    new(
        "Trial Patient 8", "Raymond Ashby", "Rectal cancer surgery", 30,
        Expect(Phase.OPEN, missed=(1, 2, 3)),
        created="09:30",
        check_in=CheckIn(
            s.at(12, "09:10"),
            _answers(["dizziness", "fatigue", "diarrhea"], wound="absorbing_sutures",
                     dizziness={"trend": "worse", "orthostatic": True, "since": "yesterday"},
                     fatigue={"trend": "worse", "interfering_with_activities": True},
                     diarrhea={"frequency_per_day": 7, "blood_in_stool": False,
                               "duration": "several_days"}),
            reviewed_at=s.at(12, "09:40"),
            expect_red_flag=True,
        ),
        messages=[
            _p(s.at(12, "09:20"),
               "I get dizzy every time I stand up, since yesterday. Bowels are going 7 times "
               "a day.", "symptom_or_wound"),
            _c(s.at(12, "09:55"),
               "Raymond, please come to the clinic today so we can check your bloodwork."),
            _r(s.at(12, "10:15"), "Coming at noon."),
            _p(s.at(16, "08:40"),
               "Dizziness is gone. Bowels are still frequent. Would a fibre supplement help?",
               "medication"),
            _c(s.at(16, "11:30"), "Yes, start psyllium once a day and drink plenty of fluids."),
            _r(s.at(16, "12:00"), "Will do."),
            _p(s.at(24, "18:20"),
               "Who do I call about the pathology appointment? Nobody has phoned me.",
               "admin"),
            _c(s.at(24, "19:40"),
               "The office books it. I've asked them to call you tomorrow morning."),
            _c(s.at(28, "10:00"), "Just checking in. How are you feeling this week?"),
            _r(s.at(28, "10:30"), "Doing well, thanks for checking."),
        ],
        touchpoints=[
            ManualTouchpoint(s.at(12, "09:50"), "call", 15,
                             "Dizziness on standing, 7 loose stools a day."),
            ManualTouchpoint(s.at(12, "13:00"), "visit", 60,
                             "Dehydrated; IV fluids, bloodwork, held the diuretic."),
        ],
    )

    # 9: ventral hernia repair, day 6. A worried swelling question awaiting a reply.
    s = Surgery(t, 6)
    new(
        "Trial Patient 9", "Amara Nwosu", "Ventral hernia repair (on the belly)", 6,
        Expect(Phase.USED, missed=(1,)),
        created="14:00",
        check_in=CheckIn(
            s.at(4, "17:30"),
            _answers(["pain"], wound="absorbing_sutures", pain=_pain(4)),
            reviewed_at=s.at(4, "18:30"),
        ),
        messages=[
            _p(s.at(4, "17:50"), "Pain is fine. A bit of bruising around the incision.",
               "recovery_update"),
            _c(s.at(4, "19:30"), "Bruising is normal and fades over a couple of weeks."),
            _r(s.at(4, "20:00"), "Thanks."),
            _p(t.ago(100),
               "The area above my incision has swollen up since this morning and feels firm. "
               "I'm worried something's wrong.", "symptom_or_wound"),
        ],
    )

    # 10: sphincterotomy for fissure, day 10. Minimal contact, then discharged by the clinic.
    s = Surgery(t, 10)
    new(
        "Trial Patient 10", "Daniel Cho", "Sphincterotomy for fissure", 10,
        Expect(Phase.CLOSED, missed=(1,)),
        created="11:00",
        check_in=CheckIn(
            s.at(3, "20:00"),
            _answers(["pain"], abdominal=False, pain=_pain(2)),
            reviewed_at=s.at(3, "20:30"),
        ),
        messages=[
            _p(s.at(3, "20:15"),
               "Is it normal to have a little bleeding with bowel movements?", "symptom_or_wound"),
            _c(s.at(3, "21:00"),
               "A little on the tissue is expected for a week or two. Call if it's more."),
            _r(s.at(4, "07:50"), "Ok, thanks."),
        ],
        closed=Closed(s.at(6, "11:00"), "discharged"),
    )

    # 11: seton surgery, day 1. Check-in pending, so messaging is locked; the clinic has written.
    new(
        "Trial Patient 11", "Hannah Doucet", "Seton surgery for fistula", 1,
        Expect(Phase.LOCKED_CHECKIN, reply=True),
        created="16:00",
        messages=[
            _c(t.ago(200),
               "Welcome, Hannah. Please complete your check-in when you can, then you can write "
               "to us in your message windows."),
        ],
    )

    # 12: anal advancement flap, day 1. Checked in; waiting for window 1 to open.
    new(
        "Trial Patient 12", "Tomas Reyes", "Anal advancement flap", 1,
        Expect(Phase.WAITING),
        created="14:30",
        check_in=CheckIn(t.ago(90), _answers(["pain"], abdominal=False, pain=_pain(3))),
    )

    # 13: colon resection, day 52. The schedule ran out three days ago and closed itself.
    s = Surgery(t, 52)
    new(
        "Trial Patient 13", "Beatrice Lang", "Other colon resection", 52,
        Expect(Phase.CLOSED, missed=(1, 3, 4, 7, 8, 9)),
        created="10:30",
        check_in=CheckIn(
            s.at(3, "11:00"),
            _answers(["fatigue"],
                     fatigue={"trend": "better", "interfering_with_activities": False}),
            reviewed_at=s.at(3, "12:00"),
        ),
        messages=[
            _p(s.at(4, "10:00"), "Feeling tired but eating well.", "recovery_update"),
            _c(s.at(4, "15:00"), "Good. Keep up the walking."),
            _r(s.at(4, "16:00"), "Will do."),
            _p(s.at(15, "09:30"), "Back to short walks outside. Any limits on lifting?",
               "recovery_update"),
            _c(s.at(15, "13:00"), "Nothing over 10 lb until six weeks."),
            _p(s.at(22, "10:30"), "Nothing heavy, all good. Thank you for everything.", "admin"),
        ],
        closed=Closed(None, "schedule_complete"),
    )

    if include_alternate_schedule:
        # 14: right hemicolectomy on schedule v2, day 11 (v2 opens on days 2, 5, 10, 17, ...).
        s = Surgery(t, 11)
        new(
            "Trial Patient 14", "Gerald Fox", "Right hemicolectomy", 11,
            Expect(Phase.OPEN), version=2, created="14:00",
            check_in=CheckIn(
                s.at(2, "08:30"),
                _answers(["pain"], pain=_pain(4)),
                reviewed_at=s.at(2, "09:15"),
            ),
            messages=[
                _p(s.at(2, "08:50"), "Home, sore but managing. Is the swelling by the incision "
                   "normal?", "symptom_or_wound"),
                _c(s.at(2, "11:30"),
                   "Some swelling is expected. Call us if it becomes hot or red."),
                _r(s.at(2, "11:50"), "Thanks."),
                _p(s.at(7, "19:00"), "Eating normally again.", "recovery_update"),
                _c(s.at(7, "20:10"), "Wonderful."),
                _r(s.at(7, "20:30"), "Thanks!"),
            ],
        )

    for plan in plans:
        _mark_reads(plan)
    return plans


# --- checking the plan against the engine ------------------------------------------------


def interval_at(plan: PatientPlan, schedule: Schedule, at: datetime) -> int | None:
    """The message window that was open at `at`, if any."""
    return next(
        (w.number for w in windows(plan.surgery_date, schedule) if w.opens_at <= at < w.closes_at),
        None,
    )


def _closed_at(plan: PatientPlan, schedule: Schedule) -> datetime | None:
    if plan.closed is None:
        return None
    return plan.closed.at or episode_end(plan.surgery_date, schedule)


def simulate(
    plan: PatientPlan, schedule: Schedule, now: datetime
) -> tuple[EpisodeState, list[str]]:
    """Replay the patient's messages under the rules and return the engine's state for `now`
    plus any rule broken along the way."""
    problems: list[str] = []
    used: set[int] = set()
    latest_clinic: Msg | None = None
    answered: set[int] = set()  # id() of clinic messages that already got their reply
    check_in_at = plan.check_in.at if plan.check_in else None
    for m in sorted(plan.messages, key=lambda m: m.at):
        if m.sender == "clinician":
            latest_clinic = m
        elif m.reply:
            if latest_clinic is None:
                problems.append(f"{plan.label}: a reply at {m.at:%b %d %H:%M} answers nothing")
            elif id(latest_clinic) in answered:
                problems.append(f"{plan.label}: a second reply to the same clinic message")
            else:
                answered.add(id(latest_clinic))
        else:
            if check_in_at is None or m.at <= check_in_at:
                problems.append(f"{plan.label}: a scheduled message before the check-in")
            if not categories.is_offered(m.category):
                problems.append(f"{plan.label}: unknown category {m.category!r}")
            n = interval_at(plan, schedule, m.at)
            if n is None:
                problems.append(f"{plan.label}: a message at {m.at:%b %d %H:%M} is in no window")
            elif n in used:
                problems.append(f"{plan.label}: two scheduled messages in window {n}")
            else:
                used.add(n)
    reply_available = latest_clinic is not None and id(latest_clinic) not in answered
    state = episode_state(
        surgery_date=plan.surgery_date,
        schedule=schedule,
        now=now,
        checkin_completed_at=check_in_at,
        closed_at=_closed_at(plan, schedule),
        close_reason=plan.closed.reason if plan.closed else None,
        used_intervals=used,
        reply_available=reply_available,
    )
    return state, problems


def validate_plan(
    plans: list[PatientPlan],
    now: datetime,
    schedules: dict[int, Schedule] | None = None,
) -> list[str]:
    """Problems with the plan, or [] if it's fine to write."""
    schedules = schedules or DEFAULT_SCHEDULES
    problems: list[str] = []
    for plan in plans:
        schedule = schedules.get(plan.schedule_version)
        if schedule is None:
            problems.append(f"{plan.label}: schedule version {plan.schedule_version} not found")
            continue
        events = (
            [plan.created_at]
            + [m.at for m in plan.messages]
            + ([plan.check_in.at] if plan.check_in else [])
            + [tp.at for tp in plan.touchpoints]
        )
        if any(e >= now for e in events):
            problems.append(f"{plan.label}: an event is in the future")
        if any(e < plan.created_at for e in events):
            problems.append(f"{plan.label}: an event predates the patient record")
        times = [m.at for m in plan.messages]
        if times != sorted(times):
            problems.append(f"{plan.label}: messages out of order")
        c = plan.check_in
        if c is not None:
            flagged, _ = evaluate_red_flags(c.answers)
            if flagged != c.expect_red_flag:
                problems.append(f"{plan.label}: check-in red flag is {flagged}, expected "
                                f"{c.expect_red_flag}")
            if c.reviewed_at is not None and not c.at < c.reviewed_at < now:
                problems.append(f"{plan.label}: check-in reviewed before it was submitted")
        if any(m.read_at is not None and not m.at < m.read_at < now for m in plan.messages):
            problems.append(f"{plan.label}: a message is read before it was sent, or in the future")

        state, rule_problems = simulate(plan, schedule, now)
        problems += rule_problems
        closed_at = _closed_at(plan, schedule)
        if plan.closed is not None:
            if closed_at is None or closed_at > now:
                problems.append(f"{plan.label}: closed in the future")
            elif any(e > closed_at for e in events):
                problems.append(f"{plan.label}: something happened after the care period closed")
            if plan.closed.reason == "schedule_complete" and closed_at != episode_end(
                plan.surgery_date, schedule
            ):
                problems.append(f"{plan.label}: a natural close must be when the schedule ends")
        want = plan.expect
        if (state.phase, state.missed, state.can_reply) != (
            want.phase, want.missed, want.reply,
        ):
            problems.append(
                f"{plan.label}: the engine says {state.phase.value}, missed {list(state.missed)}, "
                f"reply open {state.can_reply}; the plan expects {want.phase.value}, "
                f"missed {list(want.missed)}, reply open {want.reply}"
            )
    return problems


def describe(plans: list[PatientPlan]) -> str:
    lines = []
    for plan in plans:
        patient = sum(m.sender == "patient" for m in plan.messages)
        unread = sum(m.sender == "patient" and m.read_at is None for m in plan.messages)
        flagged = plan.check_in is not None and evaluate_red_flags(plan.check_in.answers)[0]
        unreviewed = plan.check_in is not None and plan.check_in.reviewed_at is None
        lines.append(
            f"{plan.label}: {plan.full_name} ({plan.procedure}), surgery {plan.surgery_date}, "
            f"schedule v{plan.schedule_version}, expected {plan.expect.phase.value}"
            f"{' (missed ' + str(list(plan.expect.missed)) + ')' if plan.expect.missed else ''}"
            f"{', reply open' if plan.expect.reply else ''}; "
            f"{len(plan.messages)} messages ({patient} patient, "
            f"{len(plan.messages) - patient} clinic, {unread} awaiting reply), "
            f"check-in {'none' if plan.check_in is None else 'red flag' if flagged else 'clear'}"
            f"{', unreviewed' if unreviewed else ''}, {len(plan.touchpoints)} logged calls/visits"
        )
    return "\n".join(lines)


# --- writing ----------------------------------------------------------------------------


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat()


def _audit(supabase, *, clinic_id: str, event_type: str, payload: dict, metadata: dict):
    """One audit event, tagged as the seed's. `metadata` carries the same keys the api's own
    write of this event does (ref_type, ref_id, ids, origin...); actor and seed tag are added."""
    from clinically_anchored_api.core.audit import record_event

    record_event(
        supabase,
        clinic_id=clinic_id,
        event_type=event_type,
        payload=payload,
        metadata={**metadata, "actor": SEED_ACTOR, "seed": SEED_TAG},
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


def ensure_dev_clinic(supabase, user_id: str) -> str:
    """The shared dev clinic (migrations 2 and 7), for `--clinic dev`. It must already exist, with
    its procedures, and the user must already be a member: this never changes who has access."""
    found = supabase.table("clinics").select("id, name").eq("id", SOURCE_CLINIC_ID).execute().data
    if not found:
        raise SystemExit("The dev clinic does not exist in this database.")
    member = (
        supabase.table("clinic_members")
        .select("role")
        .eq("clinic_id", SOURCE_CLINIC_ID)
        .eq("user_id", user_id)
        .execute()
        .data
    )
    if not member:
        raise SystemExit(
            "That account is not a member of the dev clinic. Seeding does not add memberships "
            "to it; add one yourself if you mean to."
        )
    print(f"Using the dev clinic {found[0]['name']!r} ({SOURCE_CLINIC_ID}), as {member[0]['role']}")
    return SOURCE_CLINIC_ID


def ensure_alternate_schedule(supabase) -> None:
    """Add schedule version 2 if it is missing. It becomes the newest version, which new
    patients from the dashboard use: say so loudly."""
    have = supabase.table("message_schedules").select("version").eq("version", 2).execute().data
    if have:
        return
    schedule = DEFAULT_SCHEDULES[2]
    supabase.table("message_schedules").insert(
        {
            "version": 2,
            "offsets_days": list(schedule.offsets_days),
            "close_after_days": schedule.close_after_days,
            "opens_at_local": schedule.opens_at_local.isoformat(),
            "timezone": schedule.timezone,
            "note": "Alternate schedule added by seed_trial_patients --include-alternate-schedule.",
        }
    ).execute()
    print(
        "WARNING: added message schedule version 2. It is now the NEWEST version, so patients "
        "added from the dashboard from now on get it."
    )


def load_schedules(supabase, versions: set[int]) -> dict[int, Schedule]:
    rows = (
        supabase.table("message_schedules").select("*").in_("version", sorted(versions))
        .execute().data
    )
    found = {r["version"]: Schedule.from_row(r) for r in rows}
    missing = versions - set(found)
    if missing:
        raise SystemExit(f"Message schedule version(s) {sorted(missing)} not in the database.")
    return found


def seed_patient(
    supabase, *, clinic_id: str, user_id: str, plan: PatientPlan, schedule: Schedule
) -> None:
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
        metadata={"ref_type": "patient", "ref_id": patient_id},
    )

    closed_at = _closed_at(plan, schedule)
    episode = _insert(
        supabase,
        "episodes",
        {
            "clinic_id": clinic_id,
            "patient_id": patient_id,
            "procedure_id": procedure_id,
            "surgery_date": plan.surgery_date.isoformat(),
            "schedule_version": plan.schedule_version,
            "checkin_completed_at": _iso(plan.check_in.at) if plan.check_in else None,
            "closed_at": _iso(closed_at) if closed_at else None,
            "close_reason": plan.closed.reason if plan.closed else None,
            "closed_by": user_id if plan.closed and plan.closed.reason == "discharged" else None,
            "created_by": user_id,
            "created_at": _iso(plan.created_at),
        },
    )
    episode_id = episode["id"]
    episode_ref = {
        "ref_type": "episode", "ref_id": episode_id, "patient_id": patient_id,
        "schedule_version": plan.schedule_version,
    }
    _audit(
        supabase,
        clinic_id=clinic_id,
        event_type="episode.created",
        payload={
            "episode_id": episode_id,
            "patient_id": patient_id,
            "procedure_id": procedure_id,
            "surgery_date": plan.surgery_date.isoformat(),
            "schedule": schedule.snapshot(),
            "created_by": user_id,
        },
        metadata=episode_ref,
    )

    # Everything else in time order, so the audit chain reads like the story.
    steps: list[tuple[datetime, int, object]] = []
    if plan.check_in:
        steps.append((plan.check_in.at, 0, ("check_in", plan.check_in)))
        if plan.check_in.reviewed_at is not None:
            steps.append((plan.check_in.reviewed_at, 1, ("review", plan.check_in)))
    for m in plan.messages:
        steps.append((m.at, 2, ("message", m)))
        if m.read_at is not None:
            steps.append((m.read_at, 1, ("read", m)))
    for tp in plan.touchpoints:
        steps.append((tp.at, 3, ("touchpoint", tp)))
    if closed_at is not None:
        steps.append((closed_at, 4, ("close", plan.closed)))
    steps.sort(key=lambda s: (s[0], s[1]))

    check_in_id = ""
    message_ids: dict[int, str] = {}
    latest_clinic_id: str | None = None
    for _, _, (kind, item) in steps:
        if kind == "check_in":
            is_red_flag, _ = evaluate_red_flags(item.answers)
            post_op_day = (item.at.astimezone(TORONTO).date() - plan.surgery_date).days
            row = _insert(
                supabase,
                "check_ins",
                {
                    "clinic_id": clinic_id,
                    "patient_id": patient_id,
                    "episode_id": episode_id,
                    "procedure_id": procedure_id,
                    "post_op_day": post_op_day,
                    "answers": item.answers,
                    "is_red_flag": is_red_flag,
                    "created_at": _iso(item.at),
                },
            )
            check_in_id = row["id"]
            missed = episode_state(
                surgery_date=plan.surgery_date, schedule=schedule, now=item.at,
                checkin_completed_at=None, closed_at=None, close_reason=None,
                used_intervals=set(), reply_available=False,
            ).missed
            _audit(
                supabase,
                clinic_id=clinic_id,
                event_type="check_in.submitted",
                payload={
                    "check_in_id": row["id"],
                    "patient_id": patient_id,
                    "episode_id": episode_id,
                    "procedure_id": row["procedure_id"],
                    "post_op_day": row["post_op_day"],
                    "answers": row["answers"],
                    "is_red_flag": row["is_red_flag"],
                    "missed_intervals": list(missed),
                },
                metadata={"ref_type": "check_in", "ref_id": row["id"], "episode_id": episode_id},
            )
        elif kind == "review":
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
                metadata={"ref_type": "check_in", "ref_id": check_in_id},
            )
        elif kind == "message":
            values = {
                "clinic_id": clinic_id,
                "patient_id": patient_id,
                "episode_id": episode_id,
                "sender": item.sender,
                "body": item.body,
                "created_at": _iso(item.at),
                "origin": "clinician" if item.sender == "clinician" else "scheduled",
            }
            if item.reply:
                values.update(origin="reply", reply_to_message_id=latest_clinic_id)
            elif item.sender == "patient":
                values.update(
                    interval_no=interval_at(plan, schedule, item.at), category=item.category
                )
            row = _insert(supabase, "messages", values)
            message_ids[id(item)] = row["id"]
            if item.sender == "clinician":
                latest_clinic_id = row["id"]
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
                    "episode_id": episode_id,
                    "sender": row["sender"],
                    "origin": row["origin"],
                    "interval_no": row["interval_no"],
                    "category": row["category"],
                    "reply_to_message_id": row["reply_to_message_id"],
                    "body": row["body"],
                },
                metadata={
                    "ref_type": "message",
                    "ref_id": row["id"],
                    "episode_id": episode_id,
                    "origin": row["origin"],
                    "interval_no": row["interval_no"],
                    "category": row["category"],
                },
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
                metadata={"ref_type": "message", "ref_id": message_id},
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
        elif kind == "close":
            _audit(
                supabase,
                clinic_id=clinic_id,
                event_type="episode.closed",
                payload={
                    "episode_id": episode_id,
                    "patient_id": patient_id,
                    "reason": item.reason,
                    "closed_at": episode["closed_at"],
                    "closed_by": episode["closed_by"],
                },
                metadata=episode_ref,
            )

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
        metadata={"ref_type": "touchpoint", "ref_id": row["id"]},
    )


def _messaging_v2_applied(supabase) -> bool:
    try:
        supabase.table("episodes").select("id").limit(1).execute()
    except Exception:  # the table is not there: the old schema
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--email", help="the clinician's Supabase Auth email (required to write)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    parser.add_argument(
        "--clinic",
        choices=("demo", "dev"),
        default="demo",
        help="which clinic to fill: the separate demo clinic (default, created if missing) or "
        "the shared dev clinic (it must exist, and the account must already be a member)",
    )
    parser.add_argument(
        "--include-alternate-schedule",
        action="store_true",
        help="also add schedule version 2 and patient 14 (v2 then becomes the newest schedule)",
    )
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    plans = build_plan(now, args.include_alternate_schedule)
    versions = {p.schedule_version for p in plans}
    print(describe(plans))

    if args.dry_run:
        problems = validate_plan(plans, now)
        if problems:
            print("\nPlan problems (against the default schedules):\n  " + "\n  ".join(problems))
            return 1
        print("\nDry run: the plan is valid against the default schedules; nothing written.")
        return 0
    if not args.email:
        parser.error("--email is required unless --dry-run")

    from clinically_anchored_api.core.db import get_supabase

    supabase = get_supabase()
    if not _messaging_v2_applied(supabase):
        raise SystemExit(
            "Migration 9 (Messaging v2) is not applied to this database: there is no episodes "
            "table. Run supabase/scripts/reset_trial_data.sh, apply the migration, then seed."
        )
    if args.include_alternate_schedule:
        ensure_alternate_schedule(supabase)
    schedules = load_schedules(supabase, versions)
    problems = validate_plan(plans, now, schedules)
    if problems:
        print("\nPlan problems, nothing written:\n  " + "\n  ".join(problems))
        return 1

    user_id = find_user_id(supabase, args.email)
    clinic_id = (
        ensure_dev_clinic(supabase, user_id)
        if args.clinic == "dev"
        else ensure_demo_clinic(supabase, user_id)
    )
    for plan in plans:
        seed_patient(
            supabase, clinic_id=clinic_id, user_id=user_id, plan=plan,
            schedule=schedules[plan.schedule_version],
        )
    print("\nDone. Check with: uv run python -m clinically_anchored_api.core.reconcile "
          f"--clinic {clinic_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
