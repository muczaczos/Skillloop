"use client";

// The single source of the session state. supabase-js stores the session in the browser,
// refreshes the access token and reports every change; this provider only mirrors it.
//
// Route protection built on it is a matter of user experience. The data is protected by the
// backend, which verifies the token of every request.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { setUnauthorizedHandler } from "@/lib/api/client";
import { getSupabase } from "@/lib/supabase";

export type SessionUser = { id: string; email: string };

type SessionState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: SessionUser };

type Auth = SessionState & {
  /** True after the backend rejected the session, until the next sign-in. */
  sessionExpired: boolean;
  /** Returns false when signing out failed. */
  signOut: () => Promise<boolean>;
};

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<SessionState>({ status: "loading" });
  const [sessionExpired, setSessionExpired] = useState(false);

  useEffect(() => {
    // Fires once with the stored session (INITIAL_SESSION) and then on every change.
    // No other supabase-js call may be awaited inside the callback (it would deadlock).
    const { data } = getSupabase().auth.onAuthStateChange((event, current) => {
      if (event === "SIGNED_IN") setSessionExpired(false);
      setSession(
        current
          ? {
              status: "authenticated",
              user: { id: current.user.id, email: current.user.email ?? "" },
            }
          : { status: "anonymous" },
      );
    });
    return () => data.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setSessionExpired(true);
      // "local": the session of this browser is removed even if the server cannot be reached.
      void getSupabase().auth.signOut({ scope: "local" });
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const signOut = useCallback(async () => {
    const { error } = await getSupabase().auth.signOut({ scope: "local" });
    return !error;
  }, []);

  const value = useMemo(
    () => ({ ...session, sessionExpired, signOut }),
    [session, sessionExpired, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}
