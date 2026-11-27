"use client";

import { useI18n } from "@/lib/i18n/I18nProvider";

export function Spinner({ fullScreen = false }: { fullScreen?: boolean }) {
  const { t } = useI18n();

  return (
    <div
      role="status"
      className={`flex items-center justify-center gap-3 p-6 text-sm text-slate-500 dark:text-slate-400 ${fullScreen ? "min-h-dvh" : ""}`}
    >
      <span
        aria-hidden="true"
        className="size-5 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-600 motion-reduce:animate-none dark:border-slate-700 dark:border-t-indigo-400"
      />
      {t.common.loading}
    </div>
  );
}
