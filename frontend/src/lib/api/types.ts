// Request and response types of the FastAPI backend (docs/API-DESIGN.md).

export type Language = "en" | "pl";

/** GET /profile */
export type Profile = {
  id: string;
  display_name: string | null;
  preferred_language: Language;
  created_at: string;
  updated_at: string;
};

export type ValidationDetail = {
  field: string;
  message: string;
};

/** The body of every error response (section 13). */
export type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    request_id: string;
    details?: ValidationDetail[];
  };
};
