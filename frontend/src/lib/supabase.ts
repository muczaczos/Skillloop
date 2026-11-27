// Supabase is used for authentication ONLY. Application data goes through the FastAPI backend
// (lib/api), never through supabase-js.
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

import { publicEnv } from "@/lib/env";

let client: SupabaseClient | null = null;

/** The browser's single client. Call it from effects and event handlers, not while rendering. */
export function getSupabase(): SupabaseClient {
  if (!publicEnv) {
    throw new Error("The public environment variables are not configured.");
  }
  client ??= createClient(publicEnv.supabaseUrl, publicEnv.supabasePublishableKey);
  return client;
}
