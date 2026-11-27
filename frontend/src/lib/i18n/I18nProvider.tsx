"use client";

// Minimal translation mechanism: typed dictionaries, the choice kept in the browser.
// The language of the user's profile in the database is deliberately not synchronised yet.
import { createContext, useContext, useEffect, useMemo, useSyncExternalStore } from "react";

import type { Language } from "@/lib/api/types";

import { en } from "./dictionaries/en";
import { pl, type Dictionary } from "./dictionaries/pl";

export const DEFAULT_LANGUAGE: Language = "pl";
export const LANGUAGES: readonly Language[] = ["pl", "en"];
const STORAGE_KEY = "skillloop.language";
const dictionaries: Record<Language, Dictionary> = { pl, en };

// Used when localStorage is blocked: the choice then lasts until the page is reloaded.
let memoryLanguage: Language | null = null;
const listeners = new Set<() => void>();

function readLanguage(): Language {
  if (memoryLanguage) return memoryLanguage;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === "en" || stored === "pl" ? stored : DEFAULT_LANGUAGE;
  } catch {
    return DEFAULT_LANGUAGE;
  }
}

function writeLanguage(language: Language): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, language);
    memoryLanguage = null;
  } catch {
    memoryLanguage = language;
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener); // a change made in another tab
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

type I18n = {
  language: Language;
  setLanguage: (language: Language) => void;
  t: Dictionary;
};

const I18nContext = createContext<I18n | null>(null);

export function I18nProvider({ children }: { children: React.ReactNode }) {
  // The server always renders the default language; the browser then applies the stored one.
  const language = useSyncExternalStore(subscribe, readLanguage, () => DEFAULT_LANGUAGE);

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  const value = useMemo(
    () => ({ language, setLanguage: writeLanguage, t: dictionaries[language] }),
    [language],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used within I18nProvider");
  return value;
}
