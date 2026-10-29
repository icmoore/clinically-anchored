// Every word of the public patient guide (/guide) lives in this one file. The page itself
// (app/guide/page.tsx, components/guide.tsx) has no words of its own. The surgeon's wording changes,
// and later translation, touch only this file.
//
// PLACEHOLDER WORDING -- pending clinical review. Do not treat as final. The page is marked
// "noindex" (app/guide/page.tsx) until the surgeon approves it.
//
// Rules for what goes here: short words, short sentences, for people recovering from surgery,
// about half of whom do not have English as a first language. Say "period" (not "window"). Nothing
// here may sound like blame. Describe ONLY what the app does today: every claim below was checked
// against the code (the schedule engine and its active version, the patient pages, sign-in, consent,
// the check-in form and the email templates). Where the code does not settle something, a TODO
// says so; the pull request lists them all. The numbers come from lib/guide-data.ts, which a test
// keeps in step with the api.
//
// A string may contain [link text](/path); nothing else is interpreted.

import { CATEGORY_LABELS } from "./categories";
import { GuideFacts } from "./guide-data";
import { PATIENT_TEXT } from "./patient-strings";

/** Shown under the subtitle while the wording is not approved. Set to false when the surgeon
 *  approves it, and remove `robots` from app/guide/page.tsx at the same time. */
export const SHOW_DRAFT_NOTICE = true;

export const GUIDE_TEXT = {
  eyebrow: "Patient guide",
  title: "Using this service after your surgery",
  subtitle: "How to sign in, do your check-in and write to your care team.",
  draftNotice: "Draft wording. Your surgeon has not approved this guide yet.",
  skipLink: "Skip to the guide",
  onThisPage: "On this page",
  linkToSection: (title: string) => `Link to this section: ${title}`,
  // The callout before section 01, always visible.
  urgent: {
    title: "This is not for emergencies.",
    text: "If something is urgent, call your surgeon's office or go to the nearest emergency department.",
  },
  exampleCaption: "Example only, with made-up dates. Your own dates are on your page.",
  periodsTable: {
    caption: "When each message period is open. Days are counted from the day of your surgery.",
    period: "Period",
    opens: "Opens",
    closes: "Closes",
    day: (n: number) => `Day ${n}`,
  },
  footer: {
    help: "Need help? Call your surgeon's office.",
    signIn: "Go to patient sign-in",
  },
} as const;

export type GuideBlock =
  | { kind: "p"; text: string }
  | { kind: "h3"; id: string; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "callout"; tone: "info" | "watch" | "flag"; title?: string; text: string }
  | { kind: "periods" } // the table, from the active schedule
  | { kind: "categories" } // the list a patient chooses from, from lib/categories.ts
  | { kind: "timeline" } // the example, in the real component
  | { kind: "legend" }; // what each mark means

export interface GuideSection {
  id: string;
  title: string;
  blocks: GuideBlock[];
}

const U = PATIENT_TEXT;

export function guideSections(f: GuideFacts): GuideSection[] {
  const day = (n: number) => `day ${n}`;
  return [
    // ------------------------------------------------------------------------------------------
    {
      id: "how-this-helps",
      title: "How this helps your care",
      blocks: [
        {
          kind: "p",
          text: "After surgery, your care team checks on you in two ways. You fill in a short check-in. Then you can send messages at set times. You do not have to phone to tell them how you are.",
        },
        { kind: "h3", id: "what-it-does", text: "What this does for you" },
        {
          kind: "ul",
          items: [
            "Your answers help your care team see problems early.",
            "You always know when you can write next. Your page shows it.",
            "You can write when it suits you, day or night.",
          ],
        },
        {
          kind: "callout",
          tone: "flag",
          title: "For anything urgent, use the phone.",
          text: "A message may not be read right away. If you are worried, call your surgeon's office or go to the nearest emergency department.",
        },
        { kind: "h3", id: "how-ai-is-used", text: "How AI is used" },
        // TODO(surgeon): confirm this wording. It says what the app does today (drafts and summaries
        // for the care team, a person approves every reply) and nothing about where anything is processed.
        {
          kind: "p",
          text: "Your care team may use a computer program (AI) in two ways. It can help write a first draft of a reply. It can also sum up your messages and your check-in so they are quicker to read.",
        },
        {
          kind: "ul",
          items: [
            "A person on your care team reads every reply and approves it before it is sent to you.",
            "The AI does not send anything on its own.",
            "The AI does not make decisions about your care. Your care team does.",
            "The app also uses simple rules to put check-ins that may need attention first on your care team's list. A person still reads them.",
          ],
        },
        { kind: "h3", id: "no-promises", text: "What this cannot do" },
        {
          kind: "p",
          text: "This service is a way to stay in touch. It cannot promise how your recovery will go. It does not replace seeing a doctor.",
        },
      ],
    },
    // ------------------------------------------------------------------------------------------
    {
      id: "getting-started",
      title: "Getting started",
      blocks: [
        { kind: "h3", id: "your-invite", text: "Your invite" },
        {
          kind: "p",
          text: "Your surgeon's office sends you an email to invite you. The subject starts with your surgeon's office name and says that you are invited to sign in. The email tells you to open the sign-in page and enter your email address. It has no medical information in it.",
        },
        // TODO(sender): say who the email comes from (name and address) once the production
        // sender is chosen. The subject and the words above are from core/email_templates.py.
        {
          kind: "callout",
          tone: "info",
          title: "Cannot find it?",
          text: "Look in your spam or junk folder. Then call your surgeon's office. They can send it again.",
        },
        { kind: "h3", id: "signing-in", text: "Signing in" },
        {
          kind: "ol",
          items: [
            "Go to the [patient sign-in page](/sign-in).",
            "Enter the email address your surgeon's office has for you.",
            `We email you a ${f.codeDigits}-digit code. Check your spam or junk folder if it does not come.`,
            "Enter the code.",
          ],
        },
        {
          kind: "ul",
          items: [
            `The code works for ${f.codeValidMinutes} minutes. Do not share it with anyone.`,
            `You stay signed in on that device for ${f.sessionDays} days. After that, ask for a new code.`,
            "You do not need a password.",
          ],
        },
        { kind: "h3", id: "first-time", text: "The first time you sign in" },
        {
          kind: "ol",
          items: [
            "We ask for the date of your surgery. We ask once, to make sure it is you.",
            `If the date is wrong ${f.wrongDatesBeforeLock} times, we lock your account to keep it safe. Then call your surgeon's office. They can unlock it.`,
            "You read two statements and agree to both. You need to agree to both to use messages.",
          ],
        },
        {
          kind: "callout",
          tone: "info",
          title: "What you agree to",
          text: U.consent.items.map((i) => i.text).join(" "),
        },
        { kind: "h3", id: "no-email", text: "If you do not have an email address" },
        {
          kind: "p",
          text: `Ask your surgeon's office for a sign-in link. The link works once and lasts ${f.linkValidHours} hours. You enter your surgery date each time you use it. If it stops working, ask for a new one.`,
        },
        { kind: "h3", id: "someone-helping", text: "Someone helping you" },
        // TODO(surgeon): confirm. The account and the agreements are in the patient's name; the app
        // has no separate account for a family member or friend.
        {
          kind: "p",
          text: "A family member or friend can sit with you and help you use your account. The account is yours. Only share it with someone you trust, and never share your codes.",
        },
        { kind: "h3", id: "lost-access", text: "Lost access, new email or locked out" },
        {
          kind: "p",
          text: "Call your surgeon's office. They can send a new invite, change the email address on your account, or unlock your account. You cannot do these yourself.",
        },
      ],
    },
    // ------------------------------------------------------------------------------------------
    {
      id: "using-the-service",
      title: "Using the service",
      blocks: [
        { kind: "h3", id: "check-in", text: "Your check-in" },
        // TODO(surgeon): when should a patient do the check-in? The app does not say; it only asks
        // for it before the first message. Wording below is deliberately general.
        {
          kind: "p",
          text: `The check-in is a short form about how you feel after your surgery. You do it once. You cannot send a message until it is done. Your page shows a "${U.status.startCheckIn}" button until you have done it. Do it when your care team asks you to.`,
        },
        {
          kind: "ul",
          items: [
            "The form is about your surgery. For some surgeries on the belly, it first asks if your surgery was open or laparoscopic, and how your wound is closed.",
            "Then you tick the symptoms that worry you. The list depends on your surgery. For each one you tick, there are a few more questions.",
            "At the end there is a box for anything else you want to tell your care team, in your own words. It is optional.",
            "Before it is sent, you see a summary of your answers. Check it, then confirm to send.",
          ],
        },
        {
          kind: "p",
          text: "After you send it, you see a message that it was sent. Later, you can read what you told your care team on your page. Your care team reads it. It is not a way to get help fast. If something is urgent, call.",
        },
        { kind: "h3", id: "messages", text: "Messages" },
        {
          kind: "p",
          text: "After your check-in, you can write to your care team. You can write once in each scheduled period.",
        },
        { kind: "periods" },
        {
          kind: "ul",
          items: [
            `Each period opens at ${f.opensAt} ${f.zoneName}, and stays open until the next one opens.`,
            "A period you do not use does not carry over. Most people do not need to use them all.",
            `Follow-up ends on ${day(f.endsOnDay)} after your surgery, when the last period closes.`,
          ],
        },
        {
          kind: "p",
          text: "Before you write, you choose what your message is about:",
        },
        { kind: "categories" },
        { kind: "h3", id: "replies", text: "When your care team writes to you" },
        {
          kind: "ul",
          items: [
            "When your care team writes to you, you can send one reply.",
            "A reply does not use up your period.",
            "You can only reply to the latest message from your care team.",
            "We do not send you an email when your care team writes. Come back to your page to look.",
            "Your care team reads messages when they can. They may not answer right away.",
          ],
        },
        { kind: "h3", id: "timeline", text: "The timeline on your page" },
        {
          kind: "p",
          text: "Your page shows a row of circles, one for each period. Tap a circle to see its dates. This is an example:",
        },
        { kind: "timeline" },
        { kind: "legend" },
        {
          kind: "p",
          text: "A period that ended without a message is just a quiet dot. Nothing is wrong. Your page also says which day after surgery it is.",
        },
        { kind: "h3", id: "follow-up-ends", text: "When follow-up ends" },
        // TODO(surgeon): the "ask for more follow-up" feature is built but switched off until it is
        // turned on for a clinic, and its numbers are placeholders. The sentence below is written to
        // be true either way ("if your care team offers it"). Confirm before this guide is approved.
        {
          kind: "p",
          text: `Your follow-up ends when the last period closes (${day(f.endsOnDay)} after surgery), or sooner if your care team ends it. Then your page says that your follow-up through this app has ended. You cannot send messages any more.`,
        },
        {
          kind: "ul",
          items: [
            "If you need help after that, call your surgeon's office or go to the nearest emergency department.",
            `If your care team offers it, your page may show a "${U.extension.askButton}" button. You write a few words about why. Your care team decides. We do not send notifications, so come back to your page to see the answer.`,
          ],
        },
        { kind: "h3", id: "signing-out", text: "Signing out" },
        {
          kind: "p",
          text: `On your own phone or computer you can stay signed in. On a shared or public device, always press "${U.signOut}" when you finish. The button is at the top right of your pages. If you forgot, call your surgeon's office. They can turn off sign-in for your account, and turn it back on later.`,
        },
      ],
    },
    // ------------------------------------------------------------------------------------------
    {
      id: "privacy",
      title: "Your privacy",
      blocks: [
        // TODO(surgeon): the whole section. Do not state where data is stored or processed, and do
        // not say that data stays in Canada, until that is decided and reviewed.
        {
          kind: "callout",
          tone: "watch",
          title: "Not written yet",
          text: "This section will explain how your information is used and who can see it. It has not been reviewed yet, so it is empty on purpose. Ask your surgeon's office if you have questions.",
        },
      ],
    },
    // ------------------------------------------------------------------------------------------
    {
      id: "questions",
      title: "Questions",
      blocks: [
        { kind: "h3", id: "q-no-code", text: "I did not get my code." },
        {
          kind: "p",
          text: `Look in your spam or junk folder. It can take a minute to come. The code works for ${f.codeValidMinutes} minutes, so you can ask for a new one on the sign-in page. Check that you used the email address your surgeon's office has for you. If it still does not come, call your surgeon's office.`,
        },
        { kind: "h3", id: "q-locked", text: "I am locked out." },
        {
          kind: "p",
          text: `Your account locks if the surgery date is wrong ${f.wrongDatesBeforeLock} times, or if your surgeon's office turned off sign-in. Call your surgeon's office. They can fix it.`,
        },
        { kind: "h3", id: "q-missed", text: "I did not use a period." },
        {
          kind: "p",
          text: "Nothing is wrong. A period you do not use does not carry over. You can write in the next one. If you need help sooner, call your surgeon's office.",
        },
        { kind: "h3", id: "q-urgent", text: "I have an urgent problem." },
        {
          kind: "p",
          text: "Do not use this service. Call your surgeon's office or go to the nearest emergency department.",
        },
        { kind: "h3", id: "q-who-reads", text: "Who reads my messages?" },
        {
          kind: "p",
          text: "People at your surgeon's office, who are part of your care team. They read messages when they can. They may not answer right away.",
        },
        { kind: "h3", id: "q-notified", text: "Will I get an email when my care team writes?" },
        {
          kind: "p",
          text: "No. We only email you to invite you and to send sign-in codes. Come back to your page to look for new messages.",
        },
        { kind: "h3", id: "q-ai", text: "How is AI used?" },
        {
          kind: "p",
          text: "Your care team may use AI to help write a first draft of a reply, and to sum up your messages and check-in. A person on your care team reads and approves every reply before it is sent to you. The AI does not make decisions about your care. See [How AI is used](#how-ai-is-used).",
        },
      ],
    },
  ];
}

/** What each mark on the timeline means, in the words the patient screens already use. */
export const GUIDE_LEGEND = U.timeline.legend.map((l) => ({
  mark: l.mark,
  text:
    l.mark === "✓"
      ? "You wrote in this period."
      : l.mark === "●"
        ? "This period is open now. You can write."
        : l.mark === "○"
          ? "This period has not opened yet."
          : "This period has ended.",
}));

export { CATEGORY_LABELS as GUIDE_CATEGORIES };
