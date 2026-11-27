"use client";

import { LANGUAGES, useI18n } from "@/lib/i18n/I18nProvider";

export function LanguageSwitcher() {
  const { language, setLanguage, t } = useI18n();

  return (
    <div role="group" aria-label={t.common.language} className="flex gap-1">
      {LANGUAGES.map((option) => (
        <button
          key={option}
          type="button"
          lang={option}
          aria-pressed={option === language}
          onClick={() => setLanguage(option)}
          className="min-h-9 min-w-10 rounded-md px-2 text-xs font-semibold uppercase text-slate-600 hover:bg-slate-200 focus-visible:outline-2 focus-visible:outline-indigo-500 aria-pressed:bg-indigo-600 aria-pressed:text-white dark:text-slate-300 dark:hover:bg-slate-800"
        >
          {option}
        </button>
      ))}
    </div>
  );
}
