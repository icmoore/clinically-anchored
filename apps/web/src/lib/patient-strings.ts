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

  // Asking for more follow-up after the first one ends, and the extra follow-up itself. All of this is
  // PLACEHOLDER wording, like the rest of the file. "Period", never "window".
  extension: {
    endedTitle: "Your follow-up has ended",
    ended: "Your follow-up through this app has ended.",
    ask: "If you would like more follow-up, you can ask your care team.",
    askButton: "Request more follow-up",
    reasonLabel: "Why would you like more follow-up?",
    reasonHint: (max: number) => `A few words is enough. Up to ${max} characters.`,
    send: "Send request",
    sending: "Sending...",
    couldntSend: "Couldn't send your request. Please try again.",
    until: (day: string) => stop(`You can ask until ${day}`),
    pendingTitle: "Your request",
    pending: "Your request was sent. Your care team will review it.",
    pendingNote:
      "We don't send notifications. Come back to this page: the answer will show here.",
    yourWords: "What you wrote",
    declined: "Your care team has decided not to add more follow-up through this app.",
    messageFromTeam: "Message from your care team",
    callOrGo: "If you need help, call the office or go to the emergency department.",
    accepted: "Your care team has agreed to more follow-up.",
    acceptedMore: (n: number) =>
      stop(`You have ${n} more message period${n === 1 ? "" : "s"}, about a week apart`),
    extraHeading: "Your extra message periods",
    extraPeriod: (n: number, total: number) => `Extra period ${n} of ${total}.`,
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
