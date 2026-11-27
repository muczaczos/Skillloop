"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";
import { Spinner } from "@/components/ui/Spinner";
import { useI18n } from "@/lib/i18n/I18nProvider";

/** Sign-in and registration: only for visitors without a session. */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "authenticated") router.replace("/dashboard");
  }, [status, router]);

  if (status !== "anonymous") return <Spinner fullScreen />;

  return (
    <div className="flex min-h-dvh flex-col px-4 py-4">
      <div className="flex justify-end">
        <LanguageSwitcher />
      </div>
      <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 py-6">
        <div className="text-center">
          <p className="text-3xl font-bold tracking-tight">{t.common.appName}</p>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{t.auth.tagline}</p>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          {children}
        </div>
      </main>
    </div>
  );
}
