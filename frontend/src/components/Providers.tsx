"use client";

import { AuthProvider } from "@/components/auth/AuthProvider";
import { publicEnv } from "@/lib/env";
import { I18nProvider, useI18n } from "@/lib/i18n/I18nProvider";

function ConfigurationError() {
  const { t } = useI18n();

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-3 p-6">
      <h1 className="text-xl font-semibold">{t.config.title}</h1>
      <p role="alert">{t.config.text}</p>
    </main>
  );
}

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <I18nProvider>
      {publicEnv ? <AuthProvider>{children}</AuthProvider> : <ConfigurationError />}
    </I18nProvider>
  );
}
