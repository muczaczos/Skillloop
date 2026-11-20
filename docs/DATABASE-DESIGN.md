

**Project:** SkillLoop  
**Version:** 1.2

**Status:** Draft. `profiles`, `career_goals` and the `skillloop_api` role are implemented; all other entities are planned.

**Database:** Supabase PostgreSQL

**Verified against:** the migrations in `supabase/migrations/` (`20261115160519_create_api_role`, `20261115172015_create_profiles`, `20261115173804_create_career_goals`) and the pgTAP tests in `supabase/tests/database/`, on 2026-11-20. The files were read and compared with this document; the pgTAP suite was not executed for this update, and the state of any remote Supabase project was not checked.

---

### Legend

- **Implemented** – exists in a migration and is covered by the pgTAP tests in `supabase/tests/database/`. The migrations are the source of truth; this document describes them.
- **Planned** – designed here, but no migration exists yet. It may still change.
- A part of this document without a label describes design intent.

## 1. Database Design Goals

The database must support the core SkillLoop learning cycle:

1. A user creates an account.
    
2. The user selects one professional goal and seniority level.
    
3. AI generates a competency map.
    
4. The user reviews and edits the map.
    
5. The user completes an initial diagnostic assessment.
    
6. The user learns individual competencies by explaining topics in their own words.
    
7. AI evaluates explanations and provides feedback.
    
8. AI recommends what the user should learn next.
    
9. The user can review their progress and learning history.
    

The database must preserve historical assessment results even when the user modifies their competency map.

## 2. Technology

- **Database:** PostgreSQL
    
- **Hosting:** Supabase
    
- **Authentication:** Supabase Auth
    
- **Authorization:** PostgreSQL Row Level Security (RLS)
    
- **Backend:** FastAPI
    

Supabase Auth manages authentication and user identities. Application data is stored in PostgreSQL.

FastAPI is responsible for validating authenticated requests, enforcing business rules, and communicating with AI services.

## 3. Core Data Model

The MVP uses the following main entities:

|Entity|Responsibility|Status|
|---|---|---|
|profiles|Additional user information|Implemented|
|career_goals|User's professional development goal|Implemented|
|competencies|Competency map belonging to a goal|Planned|
|learning_sessions|Diagnostic or learning sessions|Planned|
|session_items|Questions, user responses and AI evaluations|Planned|
|recommendations|AI-generated learning recommendations|Planned|

### Entity relationships

- One user has one profile.
    
- One user can have one active career goal.
    
- One career goal contains many competencies.
    
- One career goal can have many learning sessions.
    
- One learning session contains one or more session items.
    
- Each session item can be associated with a competency.
    
- One user can have many learning recommendations.
    

A user has one active career goal in the MVP. Multiple competencies can be developed simultaneously within that goal.

## 4. Tables

Sections 4.1 and 4.2 describe what the migrations create. Sections 4.3–4.6 are **Planned**.

### 4.1 profiles — Implemented

Stores additional information about the user. Migration: `20261115172015_create_profiles.sql`.

|Column|Type|Constraints and defaults|
|---|---|---|
|id|uuid|Primary key; foreign key to `auth.users(id)` with `ON DELETE CASCADE`; no default (the value comes from `auth.users`)|
|display_name|text|Nullable; `profiles_display_name_check`: NULL or 1–100 characters|
|preferred_language|text|`NOT NULL DEFAULT 'en'`; `profiles_preferred_language_check`: `'en'` or `'pl'`|
|created_at|timestamptz|`NOT NULL DEFAULT now()`|
|updated_at|timestamptz|`NOT NULL DEFAULT now()`; set by the trigger `profiles_set_updated_at` on every UPDATE|

**Creation of the profile:**

- A profile is created **only** by the trigger `on_auth_user_created` (`AFTER INSERT ON auth.users`, for each row), which calls `public.handle_new_user()`. That is, it is created when the user registers in Supabase Auth. There is no separate onboarding step that creates it.
- `handle_new_user()` is `SECURITY DEFINER` with `search_path` set to empty, because the role that inserts into `auth.users` has no privileges on `public.profiles`. It inserts only `id` (the other columns take their defaults), uses `ON CONFLICT (id) DO NOTHING`, and deliberately reads nothing from `raw_user_meta_data`, which the client controls. It must never raise an error: a failure would block registration.
- `EXECUTE` on `handle_new_user()` is revoked from `public`, `anon` and `authenticated`.
- The migration also contains a one-time backfill that creates profiles for the users that already existed when it was applied.

**Access by the client (role `authenticated`, protected by Row Level Security):**

- The client can read only its own row and update only `display_name` and `preferred_language` of its own row. `id`, `created_at` and `updated_at` can never be written by a client.
- The client **cannot INSERT or DELETE** profiles: there is no grant and no policy for those commands.

**Deletion:** deleting a user from `auth.users` deletes the profile (`ON DELETE CASCADE`). This is how the migration is written today; it is **not an approved data deletion policy**. See section 8.

### 4.2 career_goals — Implemented

Stores the user's professional development goal. Migration: `20261115173804_create_career_goals.sql`.

|Column|Type|Constraints and defaults|
|---|---|---|
|id|uuid|Primary key; `DEFAULT gen_random_uuid()`|
|user_id|uuid|`NOT NULL`; foreign key to `auth.users(id)` with `ON DELETE CASCADE`|
|target_role|text|`NOT NULL`; `career_goals_target_role_check`: 1–100 characters after trimming whitespace|
|seniority|text|`NOT NULL`; `career_goals_seniority_check`: `'junior'`, `'mid'` or `'senior'`|
|description|text|Nullable; `career_goals_description_check`: NULL or at most 2000 characters|
|status|text|`NOT NULL DEFAULT 'active'`; `career_goals_status_check`: `'active'` or `'archived'`|
|created_at|timestamptz|`NOT NULL DEFAULT now()`|
|updated_at|timestamptz|`NOT NULL DEFAULT now()`; set by the trigger `career_goals_set_updated_at` on every UPDATE|

**Indexes:**

|Index|Definition|Purpose|
|---|---|---|
|`one_active_goal_per_user`|unique on `(user_id)` where `status = 'active'`|At most one active goal per user|
|`career_goals_user_id_status_idx`|on `(user_id, status)`|Lookups by owner and status|

**Business rules (enforced in the database, not only in the application):**

- A user can have **at most one active goal**. A second active goal is refused by the unique index (SQLSTATE `23505`, which the API reports as `409 ACTIVE_GOAL_EXISTS`) until the existing one is archived. Because the index does the check, concurrent requests are safe. Creating a new goal never silently replaces or deletes the previous one.
- A user can have **any number of archived goals**; the unique index covers active goals only.
- **Archiving** is a change of `status` from `active` to `archived`. A goal is always created active: `status` cannot be set on INSERT.
- An **archived goal is immutable and can never become active again.** The UPDATE policy applies only to rows whose current status is `active`, so an UPDATE of an archived goal (editing it or restoring it) changes zero rows and raises no error. The migrations and tests call this rule D13.
- An archived goal **stays stored and stays visible to its owner**: the SELECT policy does not filter by status. The history tables that will refer to goals are **Planned**.
- The client **cannot delete goals**: there is no `DELETE` grant and no `DELETE` policy.

**Access by the client (role `authenticated`, protected by Row Level Security):**

- SELECT: only rows with `user_id = auth.uid()`.
- INSERT: only the columns `user_id`, `target_role`, `seniority` and `description`, and only when `user_id = auth.uid()` and `status = 'active'`.
- UPDATE: only the columns `target_role`, `seniority`, `description` and `status`, only on the user's own **active** goals. `id`, `user_id`, `created_at` and `updated_at` can never be written by a client.

**Deletion:** deleting a user from `auth.users` deletes all of the user's goals, archived ones included (`ON DELETE CASCADE`). As with `profiles`, this is the current implementation and **not an approved data deletion policy**. See section 8.

**Tests:** `supabase/tests/database/10_profiles.test.sql` and `20_career_goals.test.sql` check the structure, constraints, triggers, privileges, Row Level Security, immutability of archived goals and the cascade.

### 4.3 competencies — Planned

Stores the competency map generated for a career goal.

|Column|Type|Description|
|---|---|---|
|id|UUID|Primary key|
|career_goal_id|UUID|Parent career goal|
|name|TEXT|Competency name|
|description|TEXT|Competency description|
|rationale|TEXT|Why this competency matters for the selected role|
|knowledge_type|TEXT|memory, application or documentation|
|importance|TEXT|critical, high, medium or low|
|expected_level|TEXT|Expected proficiency: beginner, intermediate or advanced|
|current_level|TEXT|Current assessed proficiency|
|display_order|INTEGER|Position in the competency list|
|status|TEXT|active or archived|
|source|TEXT|ai_generated or user_added|
|created_at|TIMESTAMPTZ|Creation timestamp|
|updated_at|TIMESTAMPTZ|Last update timestamp|

**Knowledge types:**

- `memory` — knowledge the user should understand and recall without documentation.
    
- `application` — knowledge the user should be able to apply in practice.
    
- `documentation` — knowledge the user should know how to find and use in documentation.
    

**Proficiency levels:**

|Level|Meaning|
|---|---|
|not_assessed|Insufficient assessment data to determine the user's proficiency|
|beginner|Basic concepts are partially understood|
|intermediate|The user understands the topic and can explain it|
|advanced|The user demonstrates deep and consistent understanding|

**Important distinction:**

- `expected_level` describes the proficiency required for the selected role and seniority.
    
- `current_level` describes the user's currently assessed proficiency.
    
- `not_assessed` is a valid state for `current_level`, but not for `expected_level`.
    

**Business rules:**

- Users can edit, add and archive competencies.
    
- Archiving a competency must not delete its historical assessments.
    
- Each competency belongs to exactly one career goal.
    
- Expected proficiency is not the same as the user's current proficiency.
    
- Current proficiency is calculated from assessment history, not manually entered by the user.
    
- New assessments can update current proficiency over time.
    

### 4.4 learning_sessions — Planned

Represents a diagnostic or learning session.

|Column|Type|Description|
|---|---|---|
|id|UUID|Primary key|
|user_id|UUID|Session owner|
|career_goal_id|UUID|Related career goal|
|session_type|TEXT|initial_diagnostic or learning|
|status|TEXT|in_progress, completed or abandoned|
|started_at|TIMESTAMPTZ|Session start|
|completed_at|TIMESTAMPTZ|Session completion|
|created_at|TIMESTAMPTZ|Creation timestamp|

**Business rules:**

- An initial diagnostic can be divided into multiple stages.
    
- A user can leave a session and continue later.
    
- Abandoned sessions remain in the database.
    
- A learning session can focus on one competency.
    

### 4.5 session_items — Planned

Stores individual questions, user explanations and AI evaluations.

|Column|Type|Description|
|---|---|---|
|id|UUID|Primary key|
|learning_session_id|UUID|Parent session|
|competency_id|UUID|Related competency|
|question|TEXT|Question or explanation prompt|
|response_text|TEXT|User's answer or transcribed explanation|
|input_type|TEXT|text or voice|
|assessment_status|TEXT|assessed or not_assessed|
|evaluation|JSONB|Structured AI evaluation|
|score|SMALLINT|Optional overall score, 0–100|
|ai_model|TEXT|Model used for evaluation|
|prompt_version|TEXT|Evaluation prompt version|
|created_at|TIMESTAMPTZ|Creation timestamp|
|evaluated_at|TIMESTAMPTZ|Evaluation timestamp|

**Evaluation structure:**

The `evaluation` JSONB column stores structured feedback, for example:

- `technical_correctness`
    
- `conceptual_understanding`
    
- `completeness`
    
- `clarity`
    
- `strengths`
    
- `knowledge_gaps`
    
- `feedback`
    
- `suggested_next_steps`
    

The exact JSON schema will be defined separately in the AI Service specification.

**Important rules:**

- Text and voice responses use the same evaluation structure.
    
- Voice recordings are not permanently stored by default.
    
- The transcribed text is stored as the user's response.
    
- An unanswered question must not automatically be treated as incorrect.
    
- `not_assessed` means there is insufficient information to evaluate the user's knowledge.
    
- AI evaluation results should be retained as historical records rather than overwritten by future evaluations.
    

### 4.6 recommendations — Planned

Stores learning recommendations generated by AI.

|Column|Type|Description|
|---|---|---|
|id|UUID|Primary key|
|user_id|UUID|Recommendation owner|
|career_goal_id|UUID|Related career goal|
|competency_id|UUID|Recommended competency|
|reason|TEXT|Why this competency is recommended|
|priority|TEXT|high, medium or low|
|status|TEXT|pending, accepted, completed or dismissed|
|created_at|TIMESTAMPTZ|Creation timestamp|
|updated_at|TIMESTAMPTZ|Last update timestamp|

Recommendations should consider:

- Importance of the competency for the selected role.
    
- Expected proficiency.
    
- Previous diagnostic results.
    
- Recent learning session evaluations.
    
- Existing knowledge gaps.
    

The user remains in control and can choose a different competency.

### 4.7 Shared functions and triggers — Implemented

Both functions are created in `20261115172015_create_profiles.sql`. Both are trigger functions and are not meant to be called by any API role.

|Function|Trigger(s)|Behaviour|
|---|---|---|
|`public.set_updated_at()`|`profiles_set_updated_at` (`BEFORE UPDATE` on `profiles`), `career_goals_set_updated_at` (`BEFORE UPDATE` on `career_goals`), both `FOR EACH ROW`|Sets `new.updated_at = clock_timestamp()`. `clock_timestamp()` is used instead of `now()` because `now()` is frozen inside a transaction: with `now()`, several updates in one transaction would get the same value. On INSERT the trigger does not run; `created_at` and `updated_at` take their `now()` default.|
|`public.handle_new_user()`|`on_auth_user_created` (`AFTER INSERT` on `auth.users`, `FOR EACH ROW`)|Creates the user's profile; see section 4.1.|

**Hardening of the functions (as implemented):**

|Function|`SECURITY DEFINER`|`search_path`|`EXECUTE` revoked from|
|---|---|---|---|
|`set_updated_at()`|No (runs with the privileges of the caller)|`''` (empty)|`public`, `anon`, `authenticated`|
|`handle_new_user()`|Yes: the role that inserts into `auth.users` has no privileges on `public.profiles`|`''` (empty)|`public`, `anon`, `authenticated`|

PostgreSQL grants `EXECUTE` on new functions to `PUBLIC` by default, and that cannot be switched off per schema, so every function needs an explicit `REVOKE`. The baseline test `00_security_baseline.test.sql` enforces this for every function in `public`, present and future: none may be executable by `anon`, `authenticated` or `skillloop_api`, and every `SECURITY DEFINER` function must pin its `search_path`.

`set_updated_at()` is written to be reused by the triggers of future tables. Those triggers are **Planned**.

## 5. Proficiency and Assessment Principles

SkillLoop distinguishes between three concepts:

1. **Expected proficiency** — what is required for the selected role and seniority.
    
2. **Current proficiency** — what the system has evidence the user currently understands.
    
3. **Assessment status** — whether sufficient evidence exists to determine current proficiency.
    

The current proficiency scale consists of four states:

- `not_assessed` — insufficient assessment data.
    
- `beginner` — basic concepts are partially understood.
    
- `intermediate` — the user understands the topic and can explain it.
    
- `advanced` — the user demonstrates deep and consistent understanding.
    

**Assessment rules:**

- Current proficiency is calculated from the user's assessment history.
    
- Each AI evaluation is stored as a historical record.
    
- New evaluations can change the current proficiency level.
    
- Missing answers do not automatically lower proficiency.
    
- The system must distinguish between insufficient evidence and demonstrated knowledge gaps.
    
- The user should be able to see their current level and historical progress.
    

The exact proficiency calculation algorithm will be specified separately. It should consider assessment quality and history rather than relying on a single answer.

## 6. Data Ownership and Security

Every user must have access only to their own application data.

**Required security rules:**

- Enable Row Level Security on all user-owned tables.
    
- Use `auth.uid()` to verify ownership.
    
- Users must not be able to access another user's goals, competencies, sessions, responses or recommendations.
    
- FastAPI must validate Supabase JWTs on authenticated endpoints.
    
- Never expose the Supabase service role key in frontend code.
    
- AI API keys must remain on the backend.
    

Ownership should be verified through the relationship between the record and its owning user, not simply by trusting a user ID supplied by the frontend.

Status of these rules: Row Level Security is **Implemented** on `profiles` and `career_goals`. The baseline test makes it a rule for every table in schema `public`, so a future table without RLS fails the test. The rules about JWT validation and secrets are enforced by the backend and the deployment, not by the database.

### 6.1 Roles and the access model — Implemented

Migration: `20261115160519_create_api_role.sql`. Backend: `backend/app/db/session.py` and `backend/app/db/pool.py`.

|Role|Used for|Access to application tables|
|---|---|---|
|`skillloop_api`|The only role the backend logs in with (through the Supavisor session pooler)|None on its own|
|`authenticated`|Acting on behalf of a signed-in user; Row Level Security applies to it|The minimum granted per table, see 6.2|
|`anon`|Unauthenticated requests (relevant to the Data API)|None|
|`service_role`|Keeps Supabase's default privileges; the migrations do not change them|Not used by the backend, which refuses any `DATABASE_URL` that does not log in as `skillloop_api`|

**The role `skillloop_api`:**

- `LOGIN`, `NOINHERIT`; not a superuser; no `BYPASSRLS`, `CREATEROLE`, `CREATEDB` or `REPLICATION`.
- No privilege on any table in `public` and no `USAGE` on schema `auth`. A query run without the user context below fails with "permission denied" (it fails closed).
- A member of `authenticated` only, with `INHERIT FALSE`, `SET TRUE` and no `ADMIN`. It can switch to `authenticated` but does not inherit its privileges. It cannot switch to `postgres`, `service_role` or any other role, and no role can switch to it or inherit from it.
- The password is **not** stored in git. It is set per environment with `ALTER ROLE skillloop_api PASSWORD '<secret>'`.
- Role-level settings `statement_timeout = '15s'` and `idle_in_transaction_session_timeout = '30s'`. They are set on `skillloop_api` and not on `authenticated`, because role-level settings of the target role are not applied by `SET ROLE`; the settings of `skillloop_api` stay in force after the switch. A query that exceeds the statement timeout fails with SQLSTATE `57014`, which the API reports as `504 DATABASE_TIMEOUT`.

**What the backend does for every user request** (one transaction, `user_transaction()`):

1. `BEGIN`.
2. `SET LOCAL ROLE authenticated`: Row Level Security now applies.
3. `set_config('request.jwt.claims', '{"sub": "<user id>", "role": "authenticated"}', true)`. The user id comes from the verified Supabase access token, never from the request body. `auth.uid()` reads this setting.
4. The backend checks that `auth.uid()` equals the user and `current_user` is `authenticated`; otherwise the request fails.
5. The service code runs, then `COMMIT` (or `ROLLBACK` on any error).

Everything is transaction-local (`SET LOCAL`, `set_config(..., true)`), so nothing about one user can remain on a pooled connection that serves the next one. The connection pool additionally resets and verifies each returned connection, and refuses connections that are not logged in as `skillloop_api`.

**Default privileges:** `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public` revokes all privileges on tables, sequences and functions from `anon` and `authenticated`. Supabase would otherwise grant them. The migrations do not change the default privileges of `service_role`. This is only a safety net: every migration must still `REVOKE` explicitly on each new table and then `GRANT` the minimum.

**Tests:** `supabase/tests/database/00_security_baseline.test.sql` (22 assertions) checks the attributes and membership of `skillloop_api`, the default privileges, that a freshly created table, sequence and function are not accessible to `anon`, `authenticated` or `skillloop_api`, and invariants for every object in `public`: RLS enabled on every table, no privileges for `anon`, no `TRUNCATE`, `REFERENCES` or `TRIGGER` for `authenticated`, no accessible sequences, no executable functions and pinned `search_path` for `SECURITY DEFINER` functions. The path `skillloop_api` → `SET ROLE` itself cannot be exercised there (the test runs as `postgres`) and is covered by the backend integration tests.

### 6.2 Table privileges and policies — Implemented

For both tables, `REVOKE ALL` is applied first to `public`, `anon` and `authenticated`, and then only the minimum is granted to `authenticated`. `anon` and `skillloop_api` have no privileges on either table.

**`profiles`** (`20261115172015_create_profiles.sql`)

|Command|Grant to `authenticated`|Policy|
|---|---|---|
|SELECT|whole table|`profiles_select_own`: `id = (select auth.uid())`|
|UPDATE|columns `display_name`, `preferred_language`|`profiles_update_own`: `USING` and `WITH CHECK` both `id = (select auth.uid())`|
|INSERT|none|none (rows are created by the trigger only)|
|DELETE|none|none|

**`career_goals`** (`20261115173804_create_career_goals.sql`)

|Command|Grant to `authenticated`|Policy|
|---|---|---|
|SELECT|whole table|`career_goals_select_own`: `user_id = (select auth.uid())`, any status|
|INSERT|columns `user_id`, `target_role`, `seniority`, `description`|`career_goals_insert_own`: `WITH CHECK` `user_id = (select auth.uid()) and status = 'active'`|
|UPDATE|columns `target_role`, `seniority`, `description`, `status`|`career_goals_update_own`: `USING` `user_id = (select auth.uid()) and status = 'active'`; `WITH CHECK` `user_id = (select auth.uid())`|
|DELETE|none|none|

Notes:

- Two independent barriers protect each command: the missing grant and the missing policy. The tests check both.
- `USING` of the UPDATE policy is evaluated against the existing row, so only active goals can be updated. `WITH CHECK` sees only the new row; together with the `status` CHECK constraint it allows `active` → `archived` and keeps the row owned by the user.
- Every policy writes the check as `(select auth.uid())`, as a sub-select.
- Column grants mean that `id`, `created_at` and `updated_at` (and `user_id` on UPDATE) can never be written by a client.

### 6.3 Data API (PostgREST) — Implemented configuration, scope not decided

`supabase/config.toml` has `[api] enabled = true` with `schemas = ["public", "graphql_public"]`. The tables in `public` can therefore be reached through the Data API as well as through the backend. The database does not rely on the Data API being disabled: Row Level Security and the grants above are the barrier for both paths, and `anon` has no access. The migration of `career_goals` says the same.

This reflects the **local** configuration. The configuration of any remote Supabase project was not checked and is **unverified**. Whether the Data API should stay exposed is an open decision (section 8.3).

## 7. Indexes and Constraints

### 7.1 Indexes

**Implemented** (`20261115173804_create_career_goals.sql`; primary keys also create their own unique indexes):

|Table|Index|Definition|
|---|---|---|
|career_goals|`one_active_goal_per_user`|unique on `(user_id)` where `status = 'active'`|
|career_goals|`career_goals_user_id_status_idx`|on `(user_id, status)`|

**Recommended for the planned tables (Planned, no migration yet):**

|Table|Index|
|---|---|
|competencies|career_goal_id, status|
|learning_sessions|user_id, created_at|
|learning_sessions|career_goal_id, session_type|
|session_items|learning_session_id|
|session_items|competency_id, created_at|
|recommendations|user_id, status, created_at|

### 7.2 Constraints

**Implemented:**

- Foreign keys to `auth.users(id)` with `ON DELETE CASCADE` from `profiles.id` and `career_goals.user_id` (see section 8).
- `NOT NULL` and default values as listed in sections 4.1 and 4.2.
- On `profiles`: `profiles_preferred_language_check` (`'en'` or `'pl'`) and `profiles_display_name_check` (NULL or 1–100 characters).
- On `career_goals`: `career_goals_target_role_check`, `career_goals_seniority_check`, `career_goals_description_check` and `career_goals_status_check`.
- At most one active goal per user, enforced by the partial unique index above:

```
create unique index one_active_goal_per_user
  on public.career_goals (user_id)
  where status = 'active';
```

**Recommended for the planned tables (Planned):**

- Foreign keys for all entity relationships.
- `expected_level` restricted to beginner, intermediate or advanced.
- `current_level` restricted to not_assessed, beginner, intermediate or advanced.
- `score` within a range that is still to be confirmed (this document says 0–100; see the open decision 5 in section 8.3).
- Controlled values for status, session type and knowledge type.

## 8. Data Lifecycle

### 8.1 Current behaviour — Implemented

- **Archiving a goal** changes its `status` to `archived`. The row stays stored and stays visible to its owner (the SELECT policy does not filter by status). Goal history is therefore preserved.
- **An archived goal is immutable.** It cannot be edited and can never become active again (the UPDATE policy applies to active goals only). Several archived goals can exist next to one active goal.
- **Clients cannot delete goals or profiles.** There is no `DELETE` grant and no `DELETE` policy on either table.
- **Deleting a user from `auth.users` deletes that user's rows** in `profiles` and in `career_goals`, archived goals included, through `ON DELETE CASCADE`. The pgTAP tests check both cascades.

The cascade is **the current technical state of the migrations. It is not an approved data deletion policy** (see open decision 1). It also sits uneasily with the principle below that learning history is preserved: when a user is deleted, the history goes with the user.

### 8.2 Design intent for the planned tables — Planned

The MVP should preserve learning history.

- Archiving a career goal does not delete its competencies or sessions.
- Archiving a competency does not delete previous assessments.
- New evaluations create new historical records.
- Recommendations can be dismissed or completed without deleting them.
- Deleting a user account must follow an explicit data deletion policy. What happens to the planned tables on deletion (for example the rule of their foreign keys to `auth.users` and to each other) has not been decided.

### 8.3 Open decisions

These are **not decided** by this document or by the migrations.

1. **Account and data deletion policy.** The current cascade is not an approved policy. No endpoint deletes an account. The decision determines what happens to goals, and later to sessions, answers and evaluations, when an account is removed, and the foreign-key rules of the planned tables.
2. **Formal approval of D13** (an archived goal is immutable and never becomes active again). It is implemented and tested and is referred to as D13 in migrations and tests, but this repository has no decision record for it.
3. **Scope of the Data API (PostgREST).** The local configuration exposes schema `public`. Whether it should stay exposed, and the state of any remote project, is undecided and unverified.
4. **Restricting `preferred_language` to `en` and `pl`.** The database enforces it with a CHECK constraint, so adding a language needs a migration. Whether the list and the database-level enforcement are intended is not confirmed.
5. **Inconsistent assessment scale and statuses.** This document defines `session_items.score` as 0–100 and `assessment_status` as `assessed` or `not_assessed`. `AI-ASSESSMENT-DESIGN.md` and `AI-SERVICE-DESIGN.md` use dimension scores from 0 to 4, define the overall result without a scale, and use the status `insufficient_evidence` next to the workflow statuses `in_progress`, `awaiting_follow_up` and `completed`. This must be settled before the migration of `session_items`.

## 9. Future Extensions

The database design should leave room for future features without implementing them in the MVP.

Potential extensions:

- Practical exercises and submitted solutions.
    
- Code and technical documentation uploads.
    
- Spaced repetition and review scheduling.
    
- XP, levels, streaks and badges.
    
- Knowledge graph and competency relationships.
    
- Daily learning plans and weekly rest days.
    
- Multiple career goals.
    
- Job offer analysis.
    

These features should be introduced only when their requirements are defined.

## 10. Implementation Order

Status of the suggested sequence:

|Step|Status|
|---|---|
|1. Create Supabase project and configure authentication|Implemented for the **local** Supabase stack only: the local configuration in `supabase/config.toml` and the local Supabase Auth (GoTrue) service. A remote Supabase project is not covered by this document; whether one exists, or how it is configured, was not checked.|
|1a. Create the `skillloop_api` role and safe default privileges|Implemented (`20261115160519_create_api_role.sql`)|
|2. Create `profiles`|Implemented (`20261115172015_create_profiles.sql`)|
|3. Create `career_goals`|Implemented (`20261115173804_create_career_goals.sql`)|
|4. Create `competencies`|Planned|
|5. Create `learning_sessions`|Planned|
|6. Create `session_items`|Planned; blocked by open decision 5 in section 8.3|
|7. Create `recommendations`|Planned|
|8. Add foreign keys, constraints and indexes|Implemented for `profiles` and `career_goals`; Planned for the other tables|
|9. Enable and test Row Level Security|Implemented for `profiles` and `career_goals` (pgTAP: 22 + 35 + 49 assertions); to be repeated for every new table|
|10. Connect FastAPI to Supabase|Implemented in the backend (`backend/app/db/`); the database-facing tests need a local Supabase stack|

---

**Document status:** Draft v1.2

**Next step:** Resolve the open decisions in section 8.3, in particular decision 5 (assessment scale and statuses), before creating the migrations of the planned tables.
