"use client";

import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { ApiError } from "@/lib/api/client";
import { getProfile } from "@/lib/api/profile";
import type { Profile } from "@/lib/api/types";
import type { Dictionary } from "@/lib/i18n/dictionaries/pl";
import { useI18n } from "@/lib/i18n/I18nProvider";

type ProfileState =
  | { status: "loading" }
  | { status: "ready"; profile: Profile }
  | { status: "error"; error: ApiError };

function errorText(error: ApiError, texts: Dictionary["apiErrors"]): string {
  if (error.kind === "unauthorized") return texts.unauthorized;
  if (error.kind === "unavailable") return texts.unavailable;
  if (error.kind === "network") return texts.network;
  if (error.kind === "timeout") return texts.timeout;
  if (error.code === "PROFILE_NOT_FOUND") return texts.profileNotFound;
  return texts.generic;
}

const CARD =
  "rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900";

export function DashboardView() {
  const { t, language } = useI18n();
  const auth = useAuth();
  const [state, setState] = useState<ProfileState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getProfile(controller.signal)
      .then((profile) => setState({ status: "ready", profile }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          status: "error",
          error: error instanceof ApiError ? error : new ApiError("invalid_response"),
        });
      });
    return () => controller.abort();
  }, [attempt]);

  function retry() {
    setState({ status: "loading" });
    setAttempt((value) => value + 1);
  }

  const email = auth.status === "authenticated" ? auth.user.email : "";

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">{t.dashboard.title}</h1>

      <div className="grid gap-4 md:grid-cols-2">
        <section className={CARD}>
          <h2 className="mb-4 font-semibold">{t.dashboard.account}</h2>

          {state.status === "loading" && <Spinner />}

          {state.status === "error" && (
            <div className="flex flex-col items-start gap-3">
              <Alert>
                <p className="font-medium">{t.apiErrors.title}</p>
                <p>{errorText(state.error, t.apiErrors)}</p>
                {state.error.requestId && (
                  <p className="mt-1 break-all text-xs">
                    {t.apiErrors.requestId}: {state.error.requestId}
                  </p>
                )}
              </Alert>
              <Button variant="secondary" onClick={retry}>
                {t.common.retry}
              </Button>
            </div>
          )}

          {state.status === "ready" && (
            <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-[8rem_1fr]">
              <dt className="text-slate-500 dark:text-slate-400">{t.dashboard.email}</dt>
              <dd className="break-all">{email}</dd>
              <dt className="text-slate-500 dark:text-slate-400">{t.dashboard.displayName}</dt>
              <dd>{state.profile.display_name ?? t.dashboard.displayNameEmpty}</dd>
              <dt className="text-slate-500 dark:text-slate-400">{t.dashboard.memberSince}</dt>
              <dd>
                {new Intl.DateTimeFormat(language, { dateStyle: "long" }).format(
                  new Date(state.profile.created_at),
                )}
              </dd>
            </dl>
          )}
        </section>

        <section className={CARD}>
          <h2 className="mb-2 font-semibold">{t.dashboard.comingSoonTitle}</h2>
          <p className="text-sm text-slate-600 dark:text-slate-400">{t.dashboard.comingSoonText}</p>
        </section>
      </div>
    </div>
  );
}
