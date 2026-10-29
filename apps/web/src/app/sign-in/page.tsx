import { PatientSignIn } from "@/components/patient-sign-in";
import { SiteNav } from "@/components/site-nav";

// Where a patient signs in: their email, then the code we send to it. The invite email points here.
// It has the site's top bar, like /login, so a care team member who lands here can find their way.
export default function SignInPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteNav current="sign-in" />
      <PatientSignIn />
    </div>
  );
}
