import { ApiError, apiRequest } from "./client";
import type { Profile } from "./types";

function isProfile(value: unknown): value is Profile {
  const profile = value as Profile | null;
  return (
    typeof profile?.id === "string" &&
    (profile.display_name === null || typeof profile.display_name === "string") &&
    typeof profile.created_at === "string"
  );
}

export async function getProfile(signal?: AbortSignal): Promise<Profile> {
  const payload = await apiRequest("/profile", { signal });
  if (!isProfile(payload)) throw new ApiError("invalid_response");
  return payload;
}
