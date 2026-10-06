"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { supabase } from "@/lib/supabase";

/** Renders nothing. "/" used to redirect everyone to /dashboard; the landing page is
 *  public now, so only a visitor who already has a clinician session is sent on there
 *  (everyone else, including signed-out visitors, just sees the page). */
export function SignedInRedirect() {
  const router = useRouter();
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (data.session) router.replace("/dashboard");
    });
  }, [router]);
  return null;
}
