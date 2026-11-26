
**Project:** SkillLoop  
**Version:** 1.1  
**Status:** Draft  
**API Style:** REST  
**Backend:** Python + FastAPI  
**Frontend:** Next.js + React + TypeScript  
**API Version:** v1

---

## 1. Purpose

This document defines the REST API contract between the SkillLoop frontend and backend.

It describes:

- Available API endpoints.
    
- HTTP methods.
    
- Request and response structures.
    
- Authentication requirements.
    
- Error handling.
    
- Data validation.
    
- AI operation orchestration.
    

The API must support the complete MVP learning cycle.

## 2. API Principles

The API must follow these principles:

1. Use REST conventions.
    
2. Use JSON for request and response bodies, except file uploads.
    
3. Use consistent naming conventions.
    
4. Validate all incoming data using Pydantic.
    
5. Authenticate protected endpoints using Supabase JWT.
    
6. Enforce user data ownership on the backend.
    
7. Keep AI provider communication inside the backend.
    
8. Return predictable error structures.
    
9. Use UUID identifiers for application entities.
    
10. Version the API using `/api/v1`.
    

Base URL during local development:

```
http://localhost:8000/api/v1
```

Production URL will be configured through environment variables.

## 3. Authentication

Authentication is handled by Supabase Auth.

The frontend obtains an access token after successful authentication and sends it with protected API requests.

Request header:

```
Authorization: Bearer <supabase_access_token>
```

FastAPI must:

1. Extract the JWT.
    
2. Validate its signature and claims.
    
3. Identify the authenticated user.
    
4. Reject invalid or expired tokens.
    
5. Use the authenticated user ID for all ownership checks.
    

The backend must never trust a user ID supplied by the frontend as proof of ownership.

### Authentication Endpoints

Authentication operations are primarily handled directly by Supabase Auth.

FastAPI does not implement its own password management or token generation.

## 4. API Endpoint Overview

|Area|Method|Endpoint|Description|
|---|---|---|---|
|Profile|GET|`/profile`|Get user profile|
|Profile|PATCH|`/profile`|Update user profile|
|Career Goal|POST|`/career-goals`|Create career goal|
|Career Goal|GET|`/career-goals/active`|Get active career goal|
|Career Goal|PATCH|`/career-goals/{id}`|Update career goal|
|Career Goal|POST|`/career-goals/{id}/archive`|Archive career goal|
|Competencies|GET|`/career-goals/{id}/competencies`|List competencies (implemented, Stage 9.2)|
|Competencies|POST|`/career-goals/{id}/competencies/generate`|Generate competency map (deferred: AI stage, not in Stage 9)|
|Competencies|POST|`/career-goals/{id}/competencies`|Add competency (implemented, Stage 9.2)|
|Competencies|PATCH|`/competencies/{id}`|Update competency (implemented, Stage 9.3)|
|Competencies|POST|`/competencies/{id}/archive`|Archive competency (implemented, Stage 9.3)|
|Assessment|POST|`/assessments/diagnostic`|Start initial diagnostic|
|Assessment|POST|`/assessments/{id}/items`|Generate next diagnostic question|
|Assessment|GET|`/assessments/{id}`|Get assessment state|
|Assessment|POST|`/assessments/{id}/items/{item_id}/response`|Submit response|
|Assessment|POST|`/assessments/{id}/items/{item_id}/skip`|Skip follow-up question|
|Assessment|GET|`/assessments/{id}/results`|Get diagnostic results|
|Learning|POST|`/learning/sessions`|Start learning session|
|Learning|GET|`/learning/sessions/{id}`|Get learning session|
|Learning|POST|`/learning/sessions/{id}/items`|Generate learning question|
|Learning|POST|`/learning/sessions/{id}/items/{item_id}/response`|Submit learning response|
|Recommendations|GET|`/recommendations`|Get recommendations|
|Recommendations|POST|`/recommendations/generate`|Generate recommendations|
|Recommendations|PATCH|`/recommendations/{id}`|Update recommendation status|
|Progress|GET|`/progress`|Get overall progress|
|Progress|GET|`/progress/competencies`|Get competency progress|
|Speech|POST|`/speech/transcriptions`|Transcribe audio|

This is the proposed MVP endpoint set. Some endpoints may be combined or simplified during implementation if that does not weaken the API contract.

Competency endpoints: `GET` and `POST` (Stage 9.2), `PATCH` and `archive` (Stage 9.3) are implemented, and `generate` is deferred to the AI stage. Details are in section 7.

## 5. Profile API

### GET `/profile`

Returns the authenticated user's profile.

Response `200`:

```
{
  "id": "uuid",
  "display_name": "User",
  "preferred_language": "en",
  "created_at": "2026-11-12T10:00:00Z",
  "updated_at": "2026-11-12T10:00:00Z"
}
```

Errors: `401`, `404 PROFILE_NOT_FOUND`, `503`, `504`.

### PATCH `/profile`

Updates the user's profile.

Request:

```
{
  "display_name": "New display name",
  "preferred_language": "en"
}
```

Fields that are left out are not changed; at least one field is required. `display_name` may be
`null` to clear it; `preferred_language` (`en` or `pl`) may not be `null`. Unknown fields are
rejected.

Response `200`: the full profile, as returned by `GET /profile`.

Errors: `401`, `404 PROFILE_NOT_FOUND`, `422`, `503`, `504`.

## 6. Career Goal API

### POST `/career-goals`

Creates a professional development goal.

Request:

```
{
  "target_role": "DevOps Engineer",
  "seniority": "junior",
  "description": "Prepare for a Junior DevOps Engineer position."
}
```

`seniority` is `junior`, `mid` or `senior`. `description` is optional. `status` and `user_id`
cannot be sent: a goal is always created active and always belongs to the caller.

Response `201`:

```
{
  "id": "uuid",
  "target_role": "DevOps Engineer",
  "seniority": "junior",
  "description": "Prepare for a Junior DevOps Engineer position.",
  "status": "active",
  "created_at": "2026-11-12T10:00:00Z",
  "updated_at": "2026-11-12T10:00:00Z"
}
```

A user can have only one active career goal: a second one is refused with `409
ACTIVE_GOAL_EXISTS`.

Errors: `401`, `409 ACTIVE_GOAL_EXISTS`, `422`, `503`, `504`.

### GET `/career-goals/active`

Returns the current active goal (same body as above, with status `200`).

Without an active goal (none was created, or it was archived) the answer is `404
NO_ACTIVE_GOAL`.

Errors: `401`, `404 NO_ACTIVE_GOAL`, `503`, `504`.

### PATCH `/career-goals/{id}`

Updates an active career goal. Fields that are left out are not changed; `description` may be
`null` to clear it; `target_role` and `seniority` may not be `null`; at least one field is
required.

The goal must belong to the authenticated user. A goal of another user is indistinguishable from
a missing one (`404 GOAL_NOT_FOUND`). An archived goal cannot be changed (`409 GOAL_ARCHIVED`).

Response `200`: the updated goal.

Errors: `401`, `404 GOAL_NOT_FOUND`, `409 GOAL_ARCHIVED`, `422`, `503`, `504`.

### POST `/career-goals/{id}/archive`

Archives the active career goal and returns it (`200`, status `archived`). An archived goal is
immutable and never becomes active again.

Historical sessions, assessments and competencies must remain available.

Errors: `401`, `404 GOAL_NOT_FOUND`, `409 GOAL_ARCHIVED`, `422` (invalid id), `503`, `504`.

## 7. Competency API

**Status: manual map implemented, AI generation deferred.** The contract below was approved in Stage 9.0. Stage 9 delivers the **manual** competency map only.

|Endpoint|Status|
|---|---|
|`GET /career-goals/{goal_id}/competencies`|**Implemented** (Stage 9.2)|
|`POST /career-goals/{goal_id}/competencies`|**Implemented** (Stage 9.2)|
|`PATCH /competencies/{competency_id}`|**Implemented** (Stage 9.3)|
|`POST /competencies/{competency_id}/archive`|**Implemented** (Stage 9.3)|
|`POST /career-goals/{goal_id}/competencies/generate` and saving a whole map|Deferred to the AI stage|

The four implemented endpoints are in `backend/app/api/competencies.py`; the rules are applied by `competency_service` and the database. The data model, the rules and the database behaviour in special situations are in `DATABASE-DESIGN.md` (sections 4.3, 4.3.1 and 4.8).

Conventions are those of section 6: identifiers come from the path, the user from the verified token; the request models reject unknown fields (`user_id`, `career_goal_id`, `status`, `id`); a resource of another user is indistinguishable from a missing one (`404`).

### The competency object

```
{
  "id": "uuid",
  "career_goal_id": "uuid",
  "name": "Docker",
  "description": "Understanding containerization.",
  "rationale": "Docker is commonly used to package and deploy applications.",
  "category": "Containers",
  "knowledge_type": "application",
  "importance": "high",
  "expected_level": "intermediate",
  "display_order": 0,
  "status": "active",
  "created_at": "2026-11-23T10:00:00Z",
  "updated_at": "2026-11-23T10:00:00Z"
}
```

The response never contains `user_id`. Field limits: `name` 1–100 characters, `description` and `rationale` at most 1000, `category` 1–60, `display_order` an integer `>= 0`. Surrounding whitespace is ignored for the length and for the uniqueness of `name`. `knowledge_type` is `memory`, `application` or `documentation`; `importance` is `critical`, `high`, `medium` or `low`; `expected_level` is `beginner`, `intermediate` or `advanced`.

`knowledge_type` states what the role **requires**, not what the user has shown. In particular `application` does not mean proven practical proficiency (`DATABASE-DESIGN.md`, section 4.3).

### GET `/career-goals/{goal_id}/competencies` (implemented, Stage 9.2)

Returns every competency of the goal, active and archived, in the order `display_order`, `created_at`, `id`. It also works for an archived goal (its competencies are frozen, not hidden). There is no pagination or filtering in the MVP; the number of archived competencies is not limited (accepted). The list may be empty. An empty list is only returned for a goal that belongs to the user; otherwise the answer is `404 GOAL_NOT_FOUND`.

Response `200`:

```
{
  "competencies": [ { ...competency object... } ]
}
```

Errors: `401`, `404 GOAL_NOT_FOUND`, `422` (invalid id), `503`, `504`.

### POST `/career-goals/{goal_id}/competencies` (implemented, Stage 9.2)

Adds **one** competency to an **active** goal of the user.

Request:

```
{
  "name": "Terraform",
  "description": "Infrastructure as Code.",
  "rationale": "Infrastructure is usually defined as code.",
  "category": "IaC",
  "knowledge_type": "application",
  "importance": "high",
  "expected_level": "intermediate",
  "display_order": 4
}
```

Required: `name`, `knowledge_type`, `importance`, `expected_level`. Optional: `description`, `rationale`, `category`, `display_order` (default `0`). The competency is always created `active`.

Response `201`: the competency object (status `active`, no `user_id`). There is no `Location` header.

Rules:

- At most **50 active** competencies per goal; the 51st is refused with `409 COMPETENCY_LIMIT_REACHED`. Archived competencies do not count.
- The name of an active competency is unique within the goal, ignoring case: otherwise `409 COMPETENCY_NAME_EXISTS`. An archived competency releases its name.
- A goal that is archived: `409 GOAL_ARCHIVED`. A goal that is missing or belongs to another user: `404 GOAL_NOT_FOUND`. This also applies when the user has no active goal.

Errors: `401`, `404 GOAL_NOT_FOUND`, `409 GOAL_ARCHIVED`, `409 COMPETENCY_NAME_EXISTS`, `409 COMPETENCY_LIMIT_REACHED`, `422`, `503`, `504`.

### PATCH `/competencies/{competency_id}` (implemented, Stage 9.3)

Updates one **active** competency in an **active** goal. Fields that are left out are not changed; at least one field is required. `description`, `rationale` and `category` may be `null` to clear them; `name`, `knowledge_type`, `importance`, `expected_level` and `display_order` may not be `null`. `status`, `career_goal_id` and `user_id` cannot be sent: a competency is archived only through the archive endpoint and never moves to another goal.

Request (any subset of the fields of `POST`):

```
{
  "importance": "critical",
  "category": null
}
```

Values follow the same rules as in `POST`; blank text in `description`, `rationale` or `category` also clears the field.

Response `200`: the updated competency.

Errors: `401`, `404 COMPETENCY_NOT_FOUND`, `409 COMPETENCY_ARCHIVED`, `409 GOAL_ARCHIVED`, `409 COMPETENCY_NAME_EXISTS`, `422`, `503`, `504`.

### POST `/competencies/{competency_id}/archive` (implemented, Stage 9.3)

Archives one active competency and returns it (`200`, status `archived`). Archiving is one-way: an archived competency is immutable and never becomes active again. Its historical assessment data is never deleted. Archiving frees one of the 50 slots and releases the name.

Errors: `401`, `404 COMPETENCY_NOT_FOUND`, `409 COMPETENCY_ARCHIVED`, `409 GOAL_ARCHIVED`, `422` (invalid id), `503`, `504`.

### Rules that apply to all competency endpoints

- **Ownership.** A competency or goal of another user is indistinguishable from a missing one (`404`). The user id is never taken from the request.
- **No active goal.** Without an active goal no competency can be added, edited or archived. Reading the competencies of the user's own archived goal still works.
- **Frozen competencies.** When a goal is archived, its competencies can no longer be added, edited or archived. They stay readable.
- **Error precedence.** The order follows what the backend is able to recognise, given Row Level Security (the owner can read their own competencies and goals in every status, and cannot see anyone else's). Request validation (`422`) always comes first.
  - **Edit or archive a competency.** The backend finds out why nothing was changed by reading, in this order: (1) the competency is not visible (missing or another user's) → `404 COMPETENCY_NOT_FOUND`; (2) it is itself archived → `409 COMPETENCY_ARCHIVED`; (3) its goal is archived → `409 GOAL_ARCHIVED`. A competency that is archived and sits in an archived goal therefore answers `COMPETENCY_ARCHIVED`: the resource that was addressed is reported before its parent. A rename that collides with another name is reported (`409 COMPETENCY_NAME_EXISTS`) only for a competency that can be changed, so it never hides the answers above.
  - **Add a competency.** (1) The goal is not visible (missing, another user's) → `404 GOAL_NOT_FOUND`; (2) the goal is archived → `409 GOAL_ARCHIVED`; (3) the goal already has 50 active competencies → `409 COMPETENCY_LIMIT_REACHED`; (4) the name is taken → `409 COMPETENCY_NAME_EXISTS`. The database evaluates the goal and the limit in a trigger before it checks the unique index, so a request that would break both rules answers `COMPETENCY_LIMIT_REACHED`. The database cannot tell a missing goal from an archived one in its own error, so the backend reads the goal after a failure to choose between (1) and (2).
- **One resource per request.** The API adds, changes or archives one competency per request. Statements that touch several rows are not part of the API. They remain possible through the Data API, and the database handles them atomically (`DATABASE-DESIGN.md`, section 4.3.1): the whole statement succeeds or nothing changes.
- **Concurrency.** Writes to the competencies of one goal are serialised in the database (`DATABASE-DESIGN.md`, section 4.8), so concurrent requests cannot exceed the limit or write into a goal that has just been archived. A deadlock (`40P01`) or a serialization failure (`40001`) is treated as **retryable**: the backend repeats the whole transaction a small, bounded number of times and answers `503 SERVICE_UNAVAILABLE` if it still fails. A statement that waits longer than the statement timeout answers `504 DATABASE_TIMEOUT`.

### Deferred: AI generation and saving a whole map

These parts are **not** part of Stage 9 and are kept only as a design for the AI stage (Phase 2B).

`POST /career-goals/{goal_id}/competencies/generate` requests an AI-generated competency map. The generated map is returned as a proposal that is **not** saved; the user must be able to review and edit it before it is stored. When it is implemented, the proposal will also carry the `source` of each competency (a column that does not exist yet) and the endpoint that saves an approved map will be specified together with it. Sketch of the request and of the response:

```
{ "additional_context": "I have experience with Linux, Docker and Python." }
```

```
{
  "competencies": [
    {
      "name": "Linux",
      "description": "Understanding Linux operating systems.",
      "rationale": "Linux is widely used in server infrastructure.",
      "knowledge_type": "application",
      "importance": "critical",
      "expected_level": "intermediate"
    }
  ]
}
```

## 8. Assessment API

The assessment API supports the initial diagnostic process.

An assessment is a workflow containing questions, user responses, optional follow-up questions and final results.

### POST `/assessments/diagnostic`

Starts an initial diagnostic for the active career goal.

Request:

```
{
  "career_goal_id": "uuid"
}
```

Response:

```
{
  "id": "uuid",
  "session_type": "initial_diagnostic",
  "status": "in_progress",
  "current_stage": 1
}
```

### POST `/assessments/{id}/items`

Requests the next diagnostic question.

Response:

```
{
  "item_id": "uuid",
  "competency_id": "uuid",
  "question": "Explain the difference between a Docker image and a container.",
  "status": "awaiting_response"
}
```

The backend selects the next competency and generates a relevant question.

### POST `/assessments/{id}/items/{item_id}/response`

Submits a user response.

Request:

```
{
  "response_text": "An image is a template used to create containers.",
  "input_type": "text"
}
```

Response when a follow-up is required:

```
{
  "assessment_status": "awaiting_follow_up",
  "follow_up": {
    "question": "Can multiple containers be created from the same image?"
  }
}
```

Response when assessment is completed:

```
{
  "assessment_status": "completed",
  "evaluation": {
    "technical_correctness": {
      "score": 3,
      "reason": "The explanation is technically correct."
    },
    "conceptual_understanding": {
      "score": 2,
      "reason": "The basic concept is understood."
    },
    "completeness": {
      "score": 2,
      "reason": "Some important details are missing."
    },
    "clarity": {
      "score": 4,
      "reason": "The explanation is clear."
    }
  },
  "feedback": "Good starting explanation. You should explore how containers use images."
}
```

The backend must preserve the complete assessment history, including follow-up questions and answers.

### POST `/assessments/{id}/items/{item_id}/skip`

Allows the user to skip a follow-up question.

The system must preserve the distinction between insufficient evidence and incorrect knowledge.

### GET `/assessments/{id}`

Returns the current assessment state.

This endpoint allows users to continue an unfinished assessment after leaving the application.

### GET `/assessments/{id}/results`

Returns the completed diagnostic results, including:

- Assessed competencies.
    
- Individual assessment results.
    
- Current proficiency levels.
    
- Identified knowledge gaps.
    
- Suggested next steps.
    

## 9. Learning API

The learning API supports the ongoing learning cycle.

### POST `/learning/sessions`

Starts a learning session.

Request:

```
{
  "career_goal_id": "uuid",
  "competency_id": "uuid"
}
```

Response:

```
{
  "id": "uuid",
  "competency_id": "uuid",
  "status": "in_progress"
}
```

### POST `/learning/sessions/{id}/items`

Generates a question or explanation prompt for the selected competency.

Response:

```
{
  "item_id": "uuid",
  "question": "Explain how Docker volumes work and when you would use them."
}
```

### POST `/learning/sessions/{id}/items/{item_id}/response`

Submits the user's explanation.

The response follows the same assessment workflow as the diagnostic process:

- Analyze response.
    
- Request follow-up if necessary.
    
- Evaluate the explanation.
    
- Store assessment history.
    
- Update competency proficiency.
    
- Generate recommendations when appropriate.
    

### GET `/learning/sessions/{id}`

Returns the learning session and its current state.

## 10. Recommendation API

### GET `/recommendations`

Returns recommendations for the authenticated user.

Optional query parameters:

- `status`
    
- `limit`
    
- `offset`
    

Response:

```
{
  "items": [
    {
      "id": "uuid",
      "competency_id": "uuid",
      "competency_name": "Docker",
      "reason": "Your recent explanations show gaps in understanding Docker networking.",
      "priority": "high",
      "status": "pending"
    }
  ],
  "total": 1
}
```

### POST `/recommendations/generate`

Requests updated recommendations based on current competency data and assessment history.

### PATCH `/recommendations/{id}`

Updates recommendation status.

Request:

```
{
  "status": "accepted"
}
```

Allowed statuses:

- `pending`
    
- `accepted`
    
- `completed`
    
- `dismissed`
    

## 11. Progress API

### GET `/progress`

Returns an overview of the user's learning progress.

Response:

```
{
  "career_goal": {
    "target_role": "DevOps Engineer",
    "seniority": "junior"
  },
  "competencies_total": 10,
  "competencies_assessed": 7,
  "competency_levels": {
    "not_assessed": 3,
    "beginner": 2,
    "intermediate": 4,
    "advanced": 1
  },
  "completed_sessions": 12
}
```

### GET `/progress/competencies`

Returns detailed competency progress.

Response:

```
{
  "items": [
    {
      "competency_id": "uuid",
      "name": "Docker",
      "expected_level": "intermediate",
      "current_level": "beginner",
      "last_assessed_at": "2026-11-12T10:00:00Z"
    }
  ]
}
```

Current proficiency must be calculated from assessment history according to the rules defined in `AI-ASSESSMENT-DESIGN.md`.

## 12. Speech API

### POST `/speech/transcriptions`

Accepts an audio file and returns its transcription.

Content type:

```
Content-Type: multipart/form-data
```

Form field:

- `file` — audio recording.
    

Response:

```
{
  "transcription": "Explain how Docker containers work.",
  "language": "en"
}
```

The transcription is then submitted through the normal assessment workflow.

The backend must validate file size, format and duration before processing.

Audio is not permanently stored by default.

## 13. Error Handling

All API errors must use a consistent response structure.

This is decision D11. Every error response has exactly this shape, including those of unknown
paths (`404`) and wrong methods (`405`). The body is `{"error": {"code", "message",
"request_id"}}`; only request validation errors (`422`) add `details`.

Example:

```
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The submitted data is invalid.",
    "details": [
      {
        "field": "seniority",
        "message": "The value is not one of the allowed options."
      }
    ],
    "request_id": "uuid"
  }
}
```

Messages are fixed texts. They never repeat submitted values and never contain internals.
Clients should branch on `code`, not on `message`. `request_id` is generated per error response
and appears in the server log.

Error codes in use:

|Status|Code|When|
|---|---|---|
|401|`UNAUTHORIZED`|Missing, invalid or expired token, or the token's user no longer exists. Sent with `WWW-Authenticate: Bearer`.|
|403|`FORBIDDEN`|Reserved for HTTP 403 raised by the framework.|
|404|`NOT_FOUND`|Unknown path.|
|404|`PROFILE_NOT_FOUND`|The profile does not exist.|
|404|`GOAL_NOT_FOUND`|The goal does not exist or belongs to another user.|
|404|`NO_ACTIVE_GOAL`|The user has no active goal.|
|405|`METHOD_NOT_ALLOWED`|The path does not serve the method. `Allow` lists every method it serves.|
|409|`ACTIVE_GOAL_EXISTS`|The user already has an active goal.|
|409|`GOAL_ARCHIVED`|The goal is archived and can no longer be changed.|
|409|`CONFLICT`|Another conflict with the current state of a resource.|
|422|`VALIDATION_ERROR`|Invalid request; `details` lists the problems.|
|503|`SERVICE_UNAVAILABLE`|The database or the keys used to verify tokens are temporarily unavailable.|
|504|`DATABASE_TIMEOUT`|The database exceeded its statement timeout. Possible on every endpoint that uses the database.|
|500|`INTERNAL_ERROR`|An unexpected database failure. The response for other unhandled exceptions is not yet specified.|

Error codes of the competency endpoints. `COMPETENCY_NAME_EXISTS` and `COMPETENCY_LIMIT_REACHED` are in use since Stage 9.2 (`POST /career-goals/{goal_id}/competencies`):

|Status|Code|When|
|---|---|---|
|409|`COMPETENCY_NAME_EXISTS`|An active competency with this name (ignoring case) already exists in the goal.|
|409|`COMPETENCY_LIMIT_REACHED`|The goal already has 50 active competencies.|

In use since Stage 9.3 (`PATCH /competencies/{competency_id}` and `POST /competencies/{competency_id}/archive`):

|Status|Code|When|
|---|---|---|
|404|`COMPETENCY_NOT_FOUND`|The competency does not exist or belongs to another user.|
|409|`COMPETENCY_ARCHIVED`|The competency is archived and can no longer be changed.|

`GOAL_NOT_FOUND` and `GOAL_ARCHIVED` are reused. A deadlock or a serialization failure in the database is retried by the backend; if it still fails, the answer is `503 SERVICE_UNAVAILABLE` (section 7).

Recommended HTTP status codes:

|Status|Meaning|
|---|---|
|200|Successful request|
|201|Resource created|
|204|Successful request without response body|
|400|Invalid operation|
|401|Authentication required|
|403|Insufficient permissions|
|404|Resource not found|
|409|Resource conflict|
|422|Request validation error|
|429|Rate limit exceeded|
|500|Internal server error|
|502|AI provider returned an invalid or unsuccessful response|
|503|External service temporarily unavailable|
|504|External service timeout|

Error messages must not expose API keys, internal stack traces or sensitive information.

## 14. Security

All protected endpoints must:

- Require a valid Supabase JWT.
    
- Verify resource ownership.
    
- Validate request payloads.
    
- Apply appropriate rate limits.
    
- Avoid exposing internal AI provider details.
    
- Protect sensitive application data.
    

The frontend must never communicate directly with OpenAI using a secret API key.

The backend must never trust a user ID supplied in the request body.

## 15. API Documentation

FastAPI should generate OpenAPI documentation automatically.

Development documentation:

```
http://localhost:8000/docs
```

Alternative documentation:

```
http://localhost:8000/redoc
```

All endpoint request and response models should be defined using Pydantic.

The OpenAPI specification should remain synchronized with the actual implementation.

The specification documents, for every endpoint: the success response, the error responses it
can really return (using the unified error models of section 13, including `422` instead of
FastAPI's default validation error), the `HTTPBearer` security scheme (Supabase access token,
JWT) and the `WWW-Authenticate` header of `401`. `405` and its `Allow` header are described in
the general description of the API, because they come from routing, not from an operation.

`backend/tests/test_openapi_contract.py` guards this: it checks the document statically and
checks that real responses validate against the documented models. It needs no database.

## 16. Out of Scope for MVP

The following API capabilities are postponed:

- Practical exercise submission.
    
- Code execution.
    
- File and document uploads for competency analysis.
    
- Job offer import.
    
- Spaced repetition scheduling.
    
- Gamification.
    
- Knowledge graph queries.
    
- Social features.
    
- Real-time voice conversations.
    

## 17. Implementation Order

Suggested implementation sequence:

1. Configure API versioning and common response models.
    
2. Implement authentication and authorization dependencies.
    
3. Implement profile endpoints.
    
4. Implement career goal endpoints.
    
5. Implement competency endpoints (Stage 9: manual CRUD; AI generation in a later stage).
    
6. Implement diagnostic assessment endpoints.
    
7. Implement learning session endpoints.
    
8. Implement recommendation endpoints.
    
9. Implement progress endpoints.
    
10. Implement Speech-to-Text endpoint.
    
11. Add endpoint tests.
    
12. Verify generated OpenAPI documentation.
    

---

**Document status:** Draft v1.1

**Next step:** Define the implementation plan, including development phases, dependencies, testing and deployment milestones.