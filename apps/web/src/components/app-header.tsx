"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

export function AppHeader({ clinicName, email }: { clinicName: string; email: string | null }) {
  const router = useRouter();
  return (
    <header className="border-b border-border bg-surface">
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-4 py-3">
        <Link href="/dashboard" className="min-w-0">
          <p className="truncate text-sm font-semibold text-ink">{clinicName}</p>
          <p className="truncate text-xs text-muted">Clinically Anchored</p>
        </Link>
        <div className="flex items-center gap-3">
          {email && <span className="hidden truncate text-xs text-muted sm:block">{email}</span>}
          <button
            onClick={async () => {
              await supabase.auth.signOut();
              router.replace("/login");
            }}
            className="rounded-md border border-primary px-3 py-1.5 text-sm text-primary hover:bg-primary-tint"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
