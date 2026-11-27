"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useLayoutEffect, useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";
import { useI18n } from "@/lib/i18n/I18nProvider";

export function AppHeader() {
  const { t } = useI18n();
  const { signOut } = useAuth();
  const pathname = usePathname();
  const [signingOut, setSigningOut] = useState(false);
  const [failed, setFailed] = useState(false);

  // See AuthForm: reset when this page is hidden instead of unmounted.
  useLayoutEffect(
    () => () => {
      setSigningOut(false);
      setFailed(false);
    },
    [],
  );

  async function handleSignOut() {
    setSigningOut(true);
    setFailed(false);
    if (!(await signOut())) {
      setSigningOut(false);
      setFailed(true);
    }
  }

  // Further sections are added here when they exist.
  const links = [{ href: "/dashboard", label: t.nav.dashboard }] as const;

  return (
    <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <span className="text-lg font-semibold">{t.common.appName}</span>
        <nav aria-label={t.nav.label} className="order-last w-full sm:order-none sm:w-auto">
          <ul className="flex gap-1">
            {links.map((link) => (
              <li key={link.href}>
                <Link
                  href={link.href}
                  aria-current={pathname === link.href ? "page" : undefined}
                  className="inline-block rounded-md px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 aria-[current=page]:bg-indigo-50 aria-[current=page]:text-indigo-800 dark:text-slate-300 dark:hover:bg-slate-800 dark:aria-[current=page]:bg-indigo-950 dark:aria-[current=page]:text-indigo-200"
                >
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <LanguageSwitcher />
          <Button variant="secondary" onClick={handleSignOut} disabled={signingOut}>
            {signingOut ? t.nav.signingOut : t.nav.signOut}
          </Button>
        </div>
      </div>
      {failed && (
        <div className="mx-auto max-w-5xl px-4 pb-3">
          <Alert>{t.auth.errors.signOut}</Alert>
        </div>
      )}
    </header>
  );
}
