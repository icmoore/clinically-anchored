import { PatientLinkSignIn } from "@/components/patient-link-sign-in";

// The single-use link the office gives a patient who has no email: /sign-in/link#<secret>.
export default function LinkSignInPage() {
  return <PatientLinkSignIn />;
}
