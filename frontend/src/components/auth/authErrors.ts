import { isAuthRetryableFetchError, type AuthError } from "@supabase/supabase-js";

import type { Dictionary } from "@/lib/i18n/dictionaries/pl";

export type AuthErrorKey = keyof Dictionary["auth"]["errors"];

/** Translation key for an error of Supabase Auth. Its English message is never shown. */
export function authErrorKey(error: AuthError): AuthErrorKey {
  if (isAuthRetryableFetchError(error)) return "network";
  switch (error.code) {
    case "invalid_credentials":
      return "invalidCredentials";
    case "user_already_exists":
    case "email_exists":
      return "userExists";
    case "weak_password":
      return "weakPassword";
    case "email_not_confirmed":
      return "emailNotConfirmed";
    case "email_address_invalid":
      return "emailInvalid";
    case "over_request_rate_limit":
    case "over_email_send_rate_limit":
      return "rateLimit";
    default:
      return "unknown";
  }
}
