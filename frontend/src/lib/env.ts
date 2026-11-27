// Public configuration of the frontend. Everything here ends up in the browser bundle, so a
// secret must never be added.
//
// Next.js inlines NEXT_PUBLIC_* values only for literal `process.env.NAME` expressions.
// Reading never throws: the application shows a configuration message instead, and the
// production build does not need the values.

export type PublicEnv = {
  apiUrl: string;
  supabaseUrl: string;
  supabasePublishableKey: string;
};

function isHttpUrl(value: string): boolean {
  try {
    return ["http:", "https:"].includes(new URL(value).protocol);
  } catch {
    return false;
  }
}

// A secret key (or a legacy service_role key) bypasses Row Level Security and must never reach
// a browser. This cannot undo a leak, but it stops the application from using such a key.
function isSecretKey(key: string): boolean {
  if (key.startsWith("sb_secret_")) return true;
  const payload = key.split(".")[1];
  if (!payload) return false;
  try {
    const claims = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    return claims.role === "service_role";
  } catch {
    return false;
  }
}

function readPublicEnv(): PublicEnv | null {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
  const supabasePublishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? "";

  if (!isHttpUrl(apiUrl) || !isHttpUrl(supabaseUrl)) return null;
  if (!supabasePublishableKey || isSecretKey(supabasePublishableKey)) return null;

  return { apiUrl: apiUrl.replace(/\/+$/, ""), supabaseUrl, supabasePublishableKey };
}

/** The configuration, or null when a value is missing or not acceptable. */
export const publicEnv: PublicEnv | null = readPublicEnv();
