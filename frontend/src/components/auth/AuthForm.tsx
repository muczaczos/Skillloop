"use client";

import Link from "next/link";
import { useLayoutEffect, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/TextField";
import type { Dictionary } from "@/lib/i18n/dictionaries/pl";
import { useI18n } from "@/lib/i18n/I18nProvider";
import { getSupabase } from "@/lib/supabase";

import { authErrorKey, type AuthErrorKey } from "./authErrors";
import { useAuth } from "./AuthProvider";

// Mirrors minimum_password_length of supabase/config.toml. Supabase Auth has the final word.
const MIN_PASSWORD_LENGTH = 6;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type ValidationKey = keyof Dictionary["auth"]["validation"];
type FieldErrors = Partial<Record<"email" | "password" | "confirmation", ValidationKey>>;

function validate(
  mode: "login" | "register",
  email: string,
  password: string,
  confirmation: string,
): FieldErrors {
  const errors: FieldErrors = {};
  if (!email) errors.email = "emailRequired";
  else if (!EMAIL_PATTERN.test(email)) errors.email = "emailInvalid";

  if (!password) errors.password = "passwordRequired";
  else if (mode === "register" && password.length < MIN_PASSWORD_LENGTH) {
    errors.password = "passwordTooShort";
  }

  if (mode === "register" && password !== confirmation) errors.confirmation = "passwordMismatch";
  return errors;
}

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const { t } = useI18n();
  const { sessionExpired } = useAuth();
  const texts = t.auth[mode];

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  // Keys, not texts: switching the language also translates a message already on screen.
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<AuthErrorKey | null>(null);
  const [checkEmail, setCheckEmail] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // With cacheComponents a page left by navigation stays mounted, hidden. This cleanup runs
  // when it is hidden, so that no password or old message is there when the user comes back.
  useLayoutEffect(
    () => () => {
      setPassword("");
      setConfirmation("");
      setFieldErrors({});
      setFormError(null);
      setCheckEmail(false);
      setSubmitting(false);
    },
    [],
  );

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const address = email.trim();
    const errors = validate(mode, address, password, confirmation);
    setFieldErrors(errors);
    setFormError(null);
    setCheckEmail(false);
    if (Object.keys(errors).length > 0) return;

    setSubmitting(true);
    try {
      const credentials = { email: address, password };
      const { data, error } =
        mode === "login"
          ? await getSupabase().auth.signInWithPassword(credentials)
          : await getSupabase().auth.signUp(credentials);

      if (error) {
        setFormError(authErrorKey(error));
        setSubmitting(false);
      } else if (!data.session) {
        // The project requires email confirmation: there is no session yet.
        setPassword("");
        setConfirmation("");
        setCheckEmail(true);
        setSubmitting(false);
      }
      // With a session, the layout of these pages redirects to the dashboard.
    } catch {
      setFormError("unknown");
      setSubmitting(false);
    }
  }

  const fieldError = (key: ValidationKey | undefined) => key && t.auth.validation[key];

  return (
    <div className="flex flex-col gap-5">
      <h1 className="text-2xl font-semibold">{texts.title}</h1>

      {sessionExpired && mode === "login" && <Alert tone="info">{t.auth.sessionExpired}</Alert>}
      {formError && <Alert>{t.auth.errors[formError]}</Alert>}
      {checkEmail && <Alert tone="info">{t.auth.register.checkEmail}</Alert>}

      {/* method="post": a submit before the page is interactive must not put data in the URL. */}
      <form method="post" noValidate onSubmit={handleSubmit} className="flex flex-col gap-4">
        <TextField
          label={t.auth.email}
          type="email"
          name="email"
          autoComplete="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          error={fieldError(fieldErrors.email)}
        />
        <TextField
          label={t.auth.password}
          type="password"
          name="password"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          hint={mode === "register" ? t.auth.passwordHint : undefined}
          error={fieldError(fieldErrors.password)}
        />
        {mode === "register" && (
          <TextField
            label={t.auth.confirmPassword}
            type="password"
            name="confirmation"
            autoComplete="new-password"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            error={fieldError(fieldErrors.confirmation)}
          />
        )}
        <Button type="submit" disabled={submitting} className="mt-1">
          {submitting ? texts.submitting : texts.submit}
        </Button>
      </form>

      <p className="text-center text-sm text-slate-600 dark:text-slate-400">
        {texts.switchPrompt}{" "}
        <Link
          href={mode === "login" ? "/register" : "/login"}
          className="font-medium text-indigo-700 underline-offset-2 hover:underline dark:text-indigo-300"
        >
          {texts.switchLink}
        </Link>
      </p>
    </div>
  );
}
