"use client";

import { useRouter } from "next/navigation";
import { Fragment, useEffect } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { AppHeader } from "@/components/layout/AppHeader";
import { Spinner } from "@/components/ui/Spinner";

/** Pages for signed-in users. */
export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const auth = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (auth.status === "anonymous") router.replace("/login");
  }, [auth.status, router]);

  if (auth.status !== "authenticated") return <Spinner fullScreen />;

  return (
    <div className="min-h-dvh">
      <AppHeader />
      {/* Keyed by user: no page state survives a change of account. */}
      <Fragment key={auth.user.id}>
        <main className="mx-auto max-w-5xl px-4 py-6 sm:py-8">{children}</main>
      </Fragment>
    </div>
  );
}
