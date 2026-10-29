import { PatientSignIn } from "@/components/patient-sign-in";

// Where a patient signs in: their email, then the code we send to it. The invite email points here.
// It shares its page (top bar, centred column, form styles) with /login: see components/auth-page.tsx.
export default function SignInPage() {
  return <PatientSignIn />;
}
