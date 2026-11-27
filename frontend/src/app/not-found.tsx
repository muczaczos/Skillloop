"use client";

import Link from "next/link";

import { useI18n } from "@/lib/i18n/I18nProvider";

export default function NotFound() {
  const { t } = useI18n();

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col items-center justify-center gap-4 p-6 text-center">
      <h1 className="text-2xl font-semibold">{t.notFound.title}</h1>
      <Link href="/" className="font-medium text-indigo-700 hover:underline dark:text-indigo-300">
        {t.notFound.back}
      </Link>
    </main>
  );
}
