// Every patient-facing word for the message-period screens lives in this one file: the "How this
// works" panel, the main status sentence, the timeline and its labels, the "follow-up ended"
// text, and signing in (email and code, the surgery-date check, consent, a locked or turned-off
// account, the single-use link). The surgeon's wording changes, and later translation, touch only this file.
//
// PLACEHOLDER WORDING -- pending clinical review. Do not treat as final.
//
// Rules for what goes here: short words, short sentences, for people who may not be English-first.
// Say "period" (not "window"). Nothing here may sound like blame: a period that passed unused is
// never "missed". Times are passed in already written in the clinic's time zone ("Wed, Oct 21,
// 7:00 a.m."). The urgent notice is not here: it is the same on every patient screen
// (components/patient-frame.tsx).

import { CONSENT_KEYS, LEGAL } from "./legal";

// Times end in "a.m." / "p.m.": add the full stop only when there is not one already.
const stop = (text: string) => (text.endsWith(".") ? text : `${text}.`);

const triesLeft = (left: number) =>
  stop(`That date does not match. You have ${left} ${left === 1 ? "try" : "tries"} left`);

export const PATIENT_TEXT = {
  explainer: {
    title: "How this works",
    steps: [
      "Complete your check-in.",
      "Then you can send a message during set periods.",
      "For anything urgent, call the office or go to the emergency department.",
    ],
    // How many messages a patient gets, in one place. PLACEHOLDER wording pending clinical review.
    note: "You can send one message in each scheduled period. When your care team writes to you, you can send one reply.",
    gotIt: "Got it",
    reopen: "How this works",
    // A link to the public patient guide (/guide), inside the panel.
    guideLink: "Read the full patient guide",
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

  // The two ways in from the public website (its top bar, and the two sign-in cards at the bottom of
  // the landing page), and the "wrong page?" line on the clinician login. A patient reads all of these to pick the right one. PLACEHOLDER wording.
  siteLinks: {
    patientSignIn: "Patient sign-in",
    careTeamLogin: "Care team login",
    patientGuide: "Patient guide",
    patientPrompt: "Are you a patient?",
    patientLink: "Sign in here",
    // The patient's card in the sign-in band at the bottom of the landing page.
    patientCardTitle: "For patients",
    patientCardBody: "Enter your email and we will send you a code.",
  },

  // Signing in: an email, then a code from that email. No password. The api answers the same way
  // for every address, so nothing here may suggest the email "was not found".
  signIn: {
    title: "Patient sign-in",
    intro: "Enter the email address your surgeon's office has for you. We will email you a code.",
    emailLabel: "Your email",
    sendCode: "Send my code",
    sending: "Sending...",
    invalidEmail: "Please enter a valid email address.",
    couldntSend: "Couldn't send the code. Please try again.",
    // The api answers 503 when sign-in is not set up on the server (not something the patient did).
    unavailable: "Sign-in isn't available right now. Please try again later, or call your surgeon's office.",
    tooMany: "Too many tries. Please wait a minute and try again.",
    codeTitle: "Enter your code",
    // The same words whether or not the email can sign in.
    codeSent: "If this email can sign in, we have sent you a code. It can take a minute to arrive.",
    codeValid: "The code works for 10 minutes.",
    codeLabel: "Your code",
    signIn: "Sign in",
    signingIn: "Signing in...",
    codeFailed: "That code did not work. Check it, or ask for a new one.",
    newCode: "Send me a new code",
    otherEmail: "Use a different email",
    staySignedIn: "You will stay signed in on this device.",
    noEmail: "No email? Your surgeon's office can give you a sign-in link.",
    // A link to the public patient guide (/guide), on both steps.
    guidePrompt: "New to this?",
    guideLink: "Read the patient guide",
    // For a clinician who opened this page by mistake.
    careTeamPrompt: "Are you on the care team?",
    careTeamLink: "Log in here",
    sessionEnded: "Please sign in again.",
  },

  // The first time only: confirm it is them, then "Before you start" (below). PLACEHOLDER wording.
  identity: {
    title: "Check it's you",
    intro: "Please enter the date of your surgery. We ask this once, to make sure it is you.",
    label: "Date of your surgery",
    // Under the date box. The box shows the date in the order this device uses, so the example is
    // written out in words (a made-up date) rather than in numbers. PLACEHOLDER wording.
    example: "Pick the date from the calendar. Example: March 14, 2026.",
    button: "Continue",
    checking: "Checking...",
    wrong: (left: number) => triesLeft(left),
    // After a wrong date at the first sign-in: how many tries are left, and who to call. With no
    // office phone on file it says "call the office" with no number, as the urgent notice does
    // (components/patient-frame.tsx). (The single-use link screen has no phone: it uses `wrong`.)
    wrongWithHelp: (left: number, phone: string | null) =>
      `${triesLeft(left)} ${stop(
        phone ? `If you need help, call the office at ${phone}` : "If you need help, call the office",
      )}`,
    couldntCheck: "Couldn't check the date. Please try again.",
  },
  // "Before you start": the Terms of Use, the Privacy Notice and the two consents, on one screen,
  // the first time and again whenever the documents change. The two checkbox sentences are not
  // typed here: they are read from content/legal/config.json (their version and text are one thing
  // there, and the api hashes them). PLACEHOLDER wording for everything else; the surgeon and the
  // lawyer approve it.
  acceptance: {
    title: "Before you start",
    // Shown again, with this line, when the documents have changed since the patient last agreed.
    changed: "Our Terms of Use or Privacy Notice have changed. Please read them and agree again to keep using the app.",
    intro: "Please read these two pages. Then tick both boxes to continue.",
    readTerms: "Read the Terms of Use",
    readPrivacy: "Read the Privacy Notice",
    opensNewTab: "(opens in a new tab)",
    items: CONSENT_KEYS.map((key) => ({ key, text: LEGAL.consent[key].text })),
    button: "Continue",
    saving: "Saving...",
    needBoth: "Tick both boxes to continue.",
    couldntSave: "Couldn't save. Please try again.",
    reload: "The wording has changed. Please reload this page.",
    decline: "I don't want to use the app",
    declining: "Saving...",
    // After "I don't want to use the app": the patient is signed out and sees only this.
    declined: {
      title: "You chose not to use the app",
      body: "That is okay. You have been signed out and nothing has been turned on. Your care continues with your surgeon's office. If you change your mind, sign in again, or call the office.",
    },
  },
  // The public Terms and Privacy pages (/terms, /privacy).
  legal: {
    termsTitle: "Terms of Use",
    privacyTitle: "Privacy Notice",
    versionLine: (version: string, date: string) => `Version ${version} \u00b7 effective ${date}`,
  },
  // The patient's account menu (top right of every signed-in screen).
  accountMenu: {
    button: "Account",
    stopUsing: "Stop using the app",
  },
  // "Stop using the app": asked once more before it happens.
  stopUsing: {
    title: "Stop using the app?",
    body: "If you stop, you will not be able to send messages to your care team in this app, and you will be signed out. Your surgeon's office will see that you stopped. To start again, they would need to invite you again.",
    confirm: "Yes, stop using the app",
    cancel: "No, keep using it",
    working: "Stopping...",
    failed: "Couldn't do that. Please try again, or call the office.",
    done: {
      title: "You have stopped using the app",
      body: "You have been signed out and your messages have been turned off. Your care continues with your surgeon's office. If you want to use the app again, call the office.",
    },
  },
  locked: {
    title: "Your account is locked",
    body: "The surgery date did not match too many times, so we locked your account to keep it safe. Please call your surgeon's office. They can unlock it.",
  },
  turnedOff: {
    title: "Sign-in is turned off",
    body: "Your surgeon's office has turned off sign-in for this account. Please call them if you have questions.",
  },
  signOut: "Sign out",

  // An old per-episode link (?token=): replaced by signing in with an email and a code.
  replacedLink: {
    title: "This link has been replaced",
    body: "This link has been replaced. Your surgeon's office will email you a sign-in invite.",
    signIn: "Already have an invite? Sign in",
  },

  // The single-use link the office gives a patient who has no email.
  link: {
    title: "Sign in with your link",
    intro: "Your surgeon's office gave you this link. Enter the date of your surgery to sign in.",
    button: "Sign in",
    incomplete: "This link is not complete. Please ask your surgeon's office for a new one.",
    expired: "This link does not work any more. Please ask your surgeon's office for a new one.",
    couldntSignIn: "Couldn't sign you in. Please try again.",
  },
} as const;
