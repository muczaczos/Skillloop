// The only place that talks to the FastAPI backend: adds the access token, applies a time
// limit and turns every failure into an ApiError.
import { publicEnv } from "@/lib/env";
import { getSupabase } from "@/lib/supabase";

import type { ApiErrorBody, ValidationDetail } from "./types";

const REQUEST_TIMEOUT_MS = 15_000;

export type ApiErrorKind =
  | "unauthorized" // 401, or there is no session
  | "unavailable" // 503 and 504: worth trying again
  | "network" // the backend (or the auth server) could not be reached
  | "timeout" // the backend did not answer in time
  | "api" // any other error response
  | "invalid_response";

export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    readonly status: number | null = null,
    readonly code: string | null = null,
    readonly requestId: string | null = null,
    readonly details: ValidationDetail[] = [],
  ) {
    super(code ?? kind);
    this.name = "ApiError";
  }
}

let onUnauthorized: (() => void) | null = null;

/** Registered by the AuthProvider: what to do when the backend rejects the session. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

function isApiErrorBody(body: unknown): body is ApiErrorBody {
  const error = (body as ApiErrorBody | null)?.error;
  return typeof error?.code === "string";
}

function toApiError(status: number, body: unknown): ApiError {
  const error = isApiErrorBody(body) ? body.error : null;
  const kind: ApiErrorKind =
    status === 401 ? "unauthorized" : status === 503 || status === 504 ? "unavailable" : "api";
  return new ApiError(
    kind,
    status,
    error?.code ?? null,
    error?.request_id ?? null,
    error?.details ?? [],
  );
}

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH";
  body?: unknown;
  signal?: AbortSignal;
};

/**
 * Call the backend as the signed-in user. `path` starts with "/" and is relative to
 * NEXT_PUBLIC_API_URL. An abort through `signal` is passed on as is (it is not an ApiError).
 */
export async function apiRequest(path: string, options: RequestOptions = {}): Promise<unknown> {
  const { method = "GET", body, signal } = options;
  if (!publicEnv) throw new ApiError("network");

  // getSession() refreshes an expired access token first.
  const { data, error: sessionError } = await getSupabase().auth.getSession();
  const token = data.session?.access_token;
  if (!token) {
    // A failed refresh because the auth server is unreachable is not a sign-out.
    if (sessionError) throw new ApiError("network");
    onUnauthorized?.();
    throw new ApiError("unauthorized");
  }

  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  // One controller for both reasons to give up: the time limit and the caller's signal.
  // (AbortSignal.any is missing in some browsers that Next.js supports.)
  const controller = new AbortController();
  const abort = () => controller.abort();
  const timer = setTimeout(abort, REQUEST_TIMEOUT_MS);
  signal?.addEventListener("abort", abort);
  if (signal?.aborted) abort(); // aborted before the listener existed

  let response: Response;
  let payload: unknown;
  try {
    response = await fetch(`${publicEnv.apiUrl}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    payload = await response.json().catch((cause: unknown) => {
      // A successful response cut off by an abort is not merely "not JSON". An error
      // response keeps its meaning from the status alone.
      if (response.ok && controller.signal.aborted) throw cause;
      return null;
    });
  } catch (cause) {
    if (signal?.aborted) throw cause;
    // Not aborted by the caller, so an aborted controller means that the time ran out.
    throw new ApiError(controller.signal.aborted ? "timeout" : "network");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }

  if (!response.ok) {
    if (response.status === 401) onUnauthorized?.();
    throw toApiError(response.status, payload);
  }
  return payload;
}
