// Every patient-facing word for the message-period screens lives in this one file: the "How this
// works" panel, the main status sentence, the timeline and its labels, and the "follow-up ended"
// text. The surgeon's wording changes, and later translation, touch only this file.
//
// PLACEHOLDER WORDING -- pending clinical review. Do not treat as final.
//
// Rules for what goes here: short words, short sentences, for people who may not be English-first.
// Say "period" (not "window"). Nothing here may sound like blame: a period that passed unused is
// never "missed". Times are passed in already written in the clinic's time zone ("Wed, Oct 21,
// 7:00 a.m."). The urgent notice is not here: it is the same on every patient screen
// (components/patient-frame.tsx).

// Times end in "a.m." / "p.m.": add the full stop only when there is not one already.
const stop = (text: string) => (text.endsWith(".") ? text : `${text}.`);

export const PATIENT_TEXT = {
  explainer: {
    title: "How this works",
    steps: [
      "Complete your check-in.",
      "Then you can send a message during set periods.",
      "For anything urgent, call the office or go to the emergency department.",
    ],
    gotIt: "Got it",
    reopen: "How this works",
  },

  // The one main sentence about what the patient can do now.
  status: {
    beforeCheckIn: "Please complete your check-in first. After that you can send messages at set times.",
    startCheckIn: "Start your check-in",
    waiting: (when: string) => stop(`You can send your first message from ${when}`),
    open: (until: string) => stop(`You can send a message now, until ${until}`),
    used: (next: string) =>
      stop(`Thanks, your message was sent. You can send your next one from ${next}`),
    usedLast: "Thanks, your message was sent. There are no more periods after this one.",
    canReply: "You can reply to your care team's latest message.",
    ended:
      "Your follow-up through this app has ended. If you need help, call the office or go to the emergency department.",
    dayAfterSurgery: (n: number) => `Day ${n} after surgery`,
    surgeryIn: (n: number) => `Surgery in ${n} day${n === 1 ? "" : "s"}`,
  },

  timeline: {
    heading: "Your message periods",
    onePerPeriod: "You can write once in each period. Most people don't need to use them all.",
    nextOpens: (day: string) => `Next period opens ${day}.`,
    tapHint: "Tap a circle to see its dates.",
    period: (n: number, total: number) => `Period ${n} of ${total}.`,
    opens: (when: string) => stop(`Opens ${when}`),
    closes: (when: string) => stop(`Closes ${when}`),
    // What each state means, said once on tap and to screen readers.
    state: {
      used: "You wrote in this period.",
      open: "You can write now.",
      upcoming: "Not open yet.",
      passed: "This period has ended.",
      before_check_in: "This period ended before your check-in.",
    },
    // The short key shown under the row.
    legend: [
      { mark: "✓", text: "you wrote" },
      { mark: "●", text: "open now" },
      { mark: "○", text: "not yet" },
      { mark: "·", text: "ended" },
    ],
  },
} as const;
