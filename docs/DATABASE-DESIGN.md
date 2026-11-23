

**Project:** SkillLoop  
**Version:** 1.3

**Status:** Draft. `profiles`, `career_goals` and the `skillloop_api` role are implemented; all other entities are planned. The design of `competencies` (sections 3, 4.3, 4.3.1, 4.8 and 6.2.1) was approved in Stage 9.0 and stays **Planned** until the Stage 9.1 migration exists and its tests have passed.

**Database:** Supabase PostgreSQL

**Verified against:** the migrations in `supabase/migrations/` (`20261115160519_create_api_role`, `20261115172015_create_profiles`, `20261115173804_create_career_goals`) and the pgTAP tests in `supabase/tests/database/`, on 2026-11-20. The files were read and compared with this document; the pgTAP suite was not executed for this update, and the state of any remote Supabase project was not checked. Stage 9.0 (2026-11-23) changed only the planned sections about `competencies`: no migration was added and nothing was re-verified against a database.

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
|competencies|Competencies of a career goal (flat list; manual CRUD in Stage 9)|Planned|
|learning_sessions|Diagnostic or learning sessions|Planned|
|session_items|Questions, user responses and AI evaluations|Planned|
|recommendations|AI-generated learning recommendations|Planned|

### Entity relationships

- One user has one profile.
    
- One user can have one active career goal.
    
- One career goal contains many competencies (at most 50 active ones). A competency belongs to exactly one goal and to the owner of that goal; archiving the goal freezes its competencies.
    
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

**Planned change (Stage 9.1a):** the migration of `competencies` adds `UNIQUE (id, user_id)` to this table (constraint `career_goals_id_user_id_key`), as the target of the composite foreign key of `competencies`. No column, grant or policy of `career_goals` changes. Rule D13 is extended to the competencies of an archived goal (section 4.3).

**Tests:** `supabase/tests/database/10_profiles.test.sql` and `20_career_goals.test.sql` check the structure, constraints, triggers, privileges, Row Level Security, immutability of archived goals and the cascade.

### 4.3 competencies — Planned

Stores the competencies of one career goal: what a person in the goal's target role and seniority is expected to know.

**Status: Planned.** The design below was approved in Stage 9.0. No migration exists yet. This section changes to Implemented only after the Stage 9.1 migration exists and its pgTAP and integration tests have passed (see section 10 and `IMPLEMENTATION-PLAN.md`).

**Ownership model.** A competency belongs to exactly one career goal. There is no global competency catalogue, and a competency is never shared between goals or between users. The same name (for example "Docker") appears as separate rows in separate goals. The expected level and the importance describe what the goal requires, so they live in the same row as the competency.

|Column|Type|Constraints and defaults|
|---|---|---|
|id|uuid|Primary key; `DEFAULT gen_random_uuid()`|
|career_goal_id|uuid|`NOT NULL`; first column of the composite foreign key (below)|
|user_id|uuid|`NOT NULL`; owner; second column of the composite foreign key. Stored on the row so that Row Level Security does not need a join|
|name|text|`NOT NULL`; CHECK: 1–100 characters after trimming whitespace|
|description|text|Nullable; CHECK: NULL or at most 1000 characters|
|rationale|text|Nullable; CHECK: NULL or at most 1000 characters. Why the competency matters for the role|
|category|text|Nullable; CHECK: NULL or 1–60 characters after trimming. A flat grouping label, not a hierarchy|
|knowledge_type|text|`NOT NULL`; CHECK: `memory`, `application` or `documentation`|
|importance|text|`NOT NULL`; CHECK: `critical`, `high`, `medium` or `low`|
|expected_level|text|`NOT NULL`; CHECK: `beginner`, `intermediate` or `advanced` (`not_assessed` is not allowed)|
|display_order|integer|`NOT NULL DEFAULT 0`; CHECK: `>= 0`. A plain sortable value; there is no reorder endpoint in Stage 9|
|status|text|`NOT NULL DEFAULT 'active'`; CHECK: `active` or `archived`|
|created_at|timestamptz|`NOT NULL DEFAULT now()`|
|updated_at|timestamptz|`NOT NULL DEFAULT now()`; set by the trigger `competencies_set_updated_at` (the existing `public.set_updated_at()`) on every UPDATE|

**Planned keys, constraints and indexes:**

|Object|Definition|Purpose|
|---|---|---|
|`career_goals_id_user_id_key`|`UNIQUE (id, user_id)` added to `career_goals` by the same migration|Target of the composite foreign key. Redundant with the primary key, but PostgreSQL requires a unique constraint on the referenced columns|
|`competencies_goal_owner_fkey`|`FOREIGN KEY (career_goal_id, user_id) REFERENCES career_goals (id, user_id) ON DELETE CASCADE`|The database guarantees that `user_id` is the owner of the parent goal. A client cannot attach a competency to another user's goal, even if a policy were wrong|
|`competencies_active_name_unique`|unique index on `(career_goal_id, lower(btrim(name)))` where `status = 'active'`|At most one active competency with a given name per goal, ignoring case and surrounding whitespace. Archived competencies release their name|
|`competencies_goal_status_order_idx`|on `(career_goal_id, status, display_order)`|Listing and counting the competencies of a goal|
|CHECK constraints|`competencies_name_check`, `competencies_description_check`, `competencies_rationale_check`, `competencies_category_check`, `competencies_knowledge_type_check`, `competencies_importance_check`, `competencies_expected_level_check`, `competencies_display_order_check`, `competencies_status_check`|As in the column table|
|Triggers|`competencies_lock_goal` (statement level), `competencies_enforce_goal_rules` (row level), `competencies_set_updated_at`|Limit, freezing and synchronisation: see section 4.8|

There is no separate foreign key from `user_id` to `auth.users`: the composite key already guarantees that the user exists, and deleting a user reaches the competencies through `auth.users` → `career_goals` → `competencies` (see section 8). `lower()` depends on the database collation and is not a full Unicode case folding; for names of technologies this is accepted.

**Deliberately not stored (approved in Stage 9.0):**

- `current_level`: the user's current proficiency is calculated from assessment history (section 5). Storing it in this table would create a second source of truth. It will be derived when the assessment tables exist.
- `source` (`ai_generated` or `user_added`): in Stage 9 every competency is added manually. The column will be added together with AI generation.
- `expectations` (a separate "expected knowledge and skills" field): `description` and `rationale` cover it. A separate field needs a concrete demonstrated need.
- A hierarchy (`parent_id`, a categories table): `category` is plain text.
- A global catalogue or a copy-from-template mechanism.

**Knowledge types.** `knowledge_type` describes **what the role requires of the person**. It is not a statement about what the user has demonstrated.

|Value|Meaning|What it does not mean|
|---|---|---|
|`memory`|Knowledge the person in this role should be able to recall without looking anything up: concepts, terminology, mental models (for example what a container is, or the classes of HTTP status codes). Assessed through recall and explanation questions.|It does not require knowledge of exact syntax or option names.|
|`application`|Knowledge the role requires the person to apply at work: to know how and when to use a tool or technique (for example to choose a deployment strategy or to write a Dockerfile). In the MVP it is assessed only through text or voice explanations, so an assessment can show that the user is able to explain how they would apply it.|It does **not** mean demonstrated practical proficiency. The system must not claim practical competence from a theoretical explanation (`AI-ASSESSMENT-DESIGN.md`). Exercises and code execution are deferred features.|
|`documentation`|Knowledge the person does not have to memorise, but must know exists, where to find, and how to read and interpret in official documentation (for example the flags of a command or the arguments of a provider). Assessed through orientation and interpretation, not recall of details.|It does not mean the details can be ignored: the person must be able to find them.|

**Expected level.** `expected_level` is the proficiency the goal requires for this competency, for the goal's role and seniority.

|Level|Meaning|
|---|---|
|beginner|Basic concepts are understood and can be used with guidance|
|intermediate|The person understands the topic and can explain it and apply it in typical situations|
|advanced|The person demonstrates deep and consistent understanding, including less common cases|

`not_assessed` is a valid value of the user's **current** proficiency (section 5) and is not valid for `expected_level`. The current level is not stored in this table.

**Business rules:**

- A user can add, edit and archive competencies of their active goal. Nobody deletes a competency through the application.
- **Archiving a competency is one-way.** An archived competency is immutable and never becomes active again. It stays stored and stays visible to its owner.
- **An archived goal freezes its competencies** (the same principle as rule D13 for goals): they stay readable, but they cannot be edited or archived and no competency can be added. The status values of the competencies are not rewritten when a goal is archived.
- **At most 50 active competencies per goal.** Archived competencies do not count; archiving one frees a slot. The number of archived competencies is not limited in the MVP (accepted).
- **The name of an active competency is unique within its goal**, ignoring case and surrounding whitespace.
- A competency cannot be moved to another goal or user.
- Archiving a competency must not delete its historical assessments. Foreign keys from future tables to `competencies` are decided together with the deletion policy (section 8.3, decision 1).
- Expected proficiency is not the same as current proficiency; the second is calculated from assessment history, not entered by the user.

#### 4.3.1 Write behaviour in special situations — Planned

These cases apply to every client that writes through the role `authenticated`: the backend and, because the schema is exposed (section 6.3), the Data API. They are tested in Stage 9.1 (`IMPLEMENTATION-PLAN.md`). SQLSTATE `23514` is a `check_violation`; the constraint name is in the error and is what the backend reads.

|Situation|Result in the database|Result in the API (Stages 9.2–9.3)|
|---|---|---|
|**The user has no active goal.** INSERT of a competency|The statement trigger finds no goal to lock and does nothing; the row trigger finds no active goal and refuses with `23514`, constraint `competencies_goal_not_active`. Nothing is written.|The service reads the goal: the user's own archived goal → `409 GOAL_ARCHIVED`; no such goal → `404 GOAL_NOT_FOUND`|
|**The user has no active goal.** UPDATE or archive of a competency|Zero rows: the UPDATE policy requires an active goal. No error is raised.|The service reads the competency and, if needed, its goal, in this order: not visible → `404 COMPETENCY_NOT_FOUND`; visible and itself archived → `409 COMPETENCY_ARCHIVED`; otherwise the goal is archived → `409 GOAL_ARCHIVED` (section 4.8)|
|**The target goal is archived** (the user may have another, active goal)|INSERT: `23514` `competencies_goal_not_active`, as above. UPDATE: zero rows. The statement trigger locks the user's active goal, which is harmless.|As in the two rows above|
|**The target goal belongs to another user**|Indistinguishable from a missing goal: the lock query cannot see it. INSERT: `23514` `competencies_goal_not_active`. UPDATE of another user's competency: zero rows. The Row Level Security `WITH CHECK` is a second barrier (`42501`) if the trigger were absent.|`404 GOAL_NOT_FOUND` or `404 COMPETENCY_NOT_FOUND`; never a hint that the resource exists|
|**One INSERT of several competencies** (not exposed by the MVP API, possible through the Data API)|The statement lock is taken once. The row trigger counts the active competencies **including the rows that the same statement already inserted**. The first row that would be the 51st active competency raises `23514` `competencies_active_limit`, and **the whole statement is rolled back: no row is inserted**. Exactly 50 active rows pass. A duplicate name inside the statement raises `23505` and also rolls back the whole statement.|The MVP API adds one competency per request; the limit is reported as `409 COMPETENCY_LIMIT_REACHED`|
|**One UPDATE of several competencies** (not exposed by the MVP API, possible through the Data API)|The statement lock is taken before any row is read or locked. Rows are processed in an unspecified order and the statement is atomic: if one row fails (for example `23505`), no row is changed. Rows that Row Level Security hides (another user's, archived, or in an archived goal) are silently left out. The unique index is not deferrable, so swapping two names in a single statement fails with `23505` even if the final state would be valid. Archiving several rows is allowed and frees slots.|The MVP API changes one competency per request|
|**A goal is archived while a competency statement is waiting for the lock**|After the lock is granted the row trigger checks the status again and refuses with `23514` `competencies_goal_not_active`. This also covers UPDATE, which would otherwise have been silently filtered.|`409 GOAL_ARCHIVED`|
|**A privileged role** (`postgres`, `service_role`) writes to `competencies`|No statement lock (no `auth.uid()`). The row trigger still enforces status and limit, but deadlock freedom now depends on the writer: the role **must lock the goal first**. This is a mandatory contract of the architecture (section 4.8, rule L2).|Not used by the backend|

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

### 4.8 Synchronisation of competency writes (design V3) — Planned

**Status: Planned.** Approved in Stage 9.0, not implemented. Nothing in this section is verified against a running database yet; the facts about PostgreSQL come from its documentation and source (REL_17_STABLE; the local Supabase stack uses PostgreSQL 17), and the assumptions that still need a test are listed at the end.

**The problem.** Two rules about one goal must hold under concurrent requests: at most 50 active competencies, and the competencies of an archived goal are frozen. Neither can be a CHECK constraint (each needs other rows or the state of the parent). Counting before inserting is a check-then-insert race under READ COMMITTED, the default level, which the backend does not change: two requests can both count 49 and both insert. Further, a competency insert that starts just before the goal is archived must not slip into the frozen goal.

**The mechanism.** Every write to `competencies` first takes a row lock on the **goal row** (`FOR NO KEY UPDATE`), and keeps it until the end of the transaction. The lock acts as a mutex per goal.

|Object|Fires|Behaviour|
|---|---|---|
|`competencies_lock_goal`, function `competencies_lock_active_goal()`|`BEFORE INSERT OR UPDATE`, `FOR EACH STATEMENT`|Locks the caller's active goal: `SELECT 1 FROM career_goals WHERE user_id = (select auth.uid()) AND status = 'active' FOR NO KEY UPDATE`. There is at most one such row (`one_active_goal_per_user`). If there is none, it does nothing and raises nothing. It runs before any row of the statement is read or locked, and also when the statement changes zero rows.|
|`competencies_enforce_goal_rules`, function `competencies_enforce_goal_rules()`|`BEFORE INSERT OR UPDATE`, `FOR EACH ROW`|INSERT: locks the target goal `FOR NO KEY UPDATE` (taking a lock the transaction already holds is a no-op). If no row is found or its status is not `active`, it raises `23514` with the constraint name `competencies_goal_not_active`. If the new row is active and the goal already has 50 or more active competencies, it raises `23514` with `competencies_active_limit`. UPDATE: the same lock and the same goal check, without the count (an UPDATE cannot make an archived competency active).|

Both functions are `SECURITY INVOKER` (they run as `authenticated`), explicitly `VOLATILE` (required: only a volatile function gets a fresh snapshot for each query it runs, and only a volatile `BEFORE ROW` trigger sees rows inserted earlier by the same statement), have `search_path` set to empty, and have `EXECUTE` revoked from `public`, `anon` and `authenticated`. **No `SECURITY DEFINER` is used and no existing grant is widened.** The row limit of 50 is a constant in the function; changing it needs a new migration.

**Why `authenticated` is allowed to take the lock.** PostgreSQL requires `UPDATE` privilege on at least one column, in addition to `SELECT`, for `SELECT … FOR NO KEY UPDATE` (documentation of `SELECT`; the executor accepts a privilege on any one column when no column is named). `authenticated` already has column-level `UPDATE` on `career_goals (target_role, seniority, description, status)` and table-wide `SELECT` (section 6.2). The `USING` expressions of the `UPDATE` policies are applied to such a lock query as well, so `career_goals_update_own` (own and active) filters it: an archived goal and another user's goal are **invisible** to the lock and return no row. The trigger therefore cannot tell "archived" from "does not exist"; both are refused, and the backend tells them apart afterwards by reading the goal (the `SELECT` policy shows the owner their own archived goals). A pgTAP test asserts that the grants of `career_goals` stay unchanged.

**Lock modes (PostgreSQL documentation).**

|Requested mode|Conflicts with|
|---|---|
|`FOR KEY SHARE`|`FOR UPDATE`|
|`FOR SHARE`|`FOR NO KEY UPDATE`, `FOR UPDATE`|
|`FOR NO KEY UPDATE`|`FOR SHARE`, `FOR NO KEY UPDATE`, `FOR UPDATE`|
|`FOR UPDATE`|all of them|

A plain `UPDATE` of a goal that does not change a key column takes `FOR NO KEY UPDATE`; so do archiving and editing a goal. The foreign-key check of the composite key takes `FOR KEY SHARE` on the goal, which does not conflict. Row locks are held until the transaction ends and never block plain reads.

**How the rules hold.**

- **Limit of 50.** All writers to one goal queue on the goal lock. The count runs as a separate query after the lock is granted, so it sees every row committed by the previous holder. The number of active competencies grows only through INSERT (nothing can reactivate a competency), and INSERTs are serialised, so 50 cannot be exceeded. In one multi-row INSERT the statement lock is taken once and the row trigger sees the earlier rows of the same statement.
- **Freezing.** Archiving or editing a goal needs the same row lock, so it is serialised with every competency write of that goal. A statement that waited for the lock checks the status again after it is granted (a locking read in READ COMMITTED returns the latest version of the row, and the row is skipped if it no longer matches). A write that got the lock first commits before the archiving proceeds; a write that comes later is refused.
- A statement that waited for the lock still uses the snapshot it took at its start for its own scan and for the `WITH CHECK` of the policies. The row trigger is therefore the authoritative check; the policies stay as a second barrier.

**Why this is free of lock cycles.** All locks on competencies of one goal are taken in the order *goal row first, then competency rows*, and the first lock is exclusive among writers. The writer that holds the goal lock never waits for something held by a writer that waits for the goal lock, because the waiting writers have not locked or changed any competency yet. Unique-index checks only wait for a transaction that has really changed a row (a lock-only tuple lock does not make a unique check wait), and only the goal-lock holder changes rows. The cycles that this design removes are reproduced by tests of `IMPLEMENTATION-PLAN.md`: two edits swapping names under a shared lock (T2), and the order goal → competencies used by deletion (T5a, an explicit deletion that follows rule L2, and T5b, the real `ON DELETE CASCADE`). T4 covers a multi-row UPDATE against a single-row edit. Which of two waiting transactions PostgreSQL wakes first is not guaranteed, so T4 does not depend on it and asserts only outcomes that hold in every order.

**Invariants (approved):**

- **L1. Lock order: the goal row first, then competency rows. Never the reverse.** For `authenticated` the statement trigger enforces it. No transaction may lock or change a competency before locking the goal row.
- **L2. Mandatory architecture contract for privileged writes.** A privileged role (`postgres`, `service_role`) has no `auth.uid()`, so the statement trigger does nothing for it. **Every write to `competencies` made by `postgres` or `service_role`, now or in the future, must first lock the career goal in the same transaction**: `SELECT 1 FROM public.career_goals WHERE id = <goal id> FOR NO KEY UPDATE`, before the first statement that touches `competencies`. The rule covers INSERT, UPDATE and DELETE and every kind of writer: data migrations, backfills, seed and repair scripts, administrative tools and future background jobs. A write that spans several goals locks all of them first, in ascending `id` order. Reading is not restricted.
  - This is a **mandatory contract of the architecture**, not a recommendation. The database does not enforce it. Breaking it does not corrupt data, because the row trigger still enforces the status of the goal and the limit of 50 for these roles. It only loses deadlock freedom: a privileged writer that locks a competency row before the goal can deadlock with an `authenticated` writer that already holds the goal.
  - The backend does not use these roles (section 6.1), so today the contract binds migrations and operators. It binds any new code that introduces such a role.
  - Every migration or script that writes to `competencies` states in its header that it follows this contract. A change that adds a privileged writer needs a review against this rule.
- **L3. The triggers must stay enabled for application writes.** `ALTER TABLE … DISABLE TRIGGER` and `session_replication_role = replica` are not used by the application or by data migrations. An application role cannot do either.
- **L4. The statement lock relies on at most one active goal per user and on writes being allowed only in the active goal** (`one_active_goal_per_user` and the policies of section 6.2.1). If multiple active goals are ever introduced (section 9), this design must be revised first.
- **Not required with this design:** limiting a request to one write statement. The lock is held until the transaction ends, so several statements in one transaction stay safe for `authenticated`.

**Consequences.** All writes to the competencies of one goal are serialised. The MVP has one active goal per user, so this only affects the same user's own concurrent requests (double clicks, several tabs). Even a statement that changes zero rows takes the goal lock. The lock writes a mark on the goal row (WAL), which is negligible at this scale. A waiting statement is bounded by the `statement_timeout` of 15 s of `skillloop_api` (SQLSTATE `57014`, reported as `504 DATABASE_TIMEOUT`). Reads are never blocked. Concurrent `PATCH` or archiving of the goal waits for a running competency write and vice versa.

**Deletion.** The triggers of this section exist for INSERT and UPDATE only. Clients have no DELETE grant, so competencies disappear only through the cascade `auth.users` → `career_goals` → `competencies` (section 8; not an approved deletion policy) or through a privileged role, which is bound by rule L2. In the cascade the goal row is deleted first and the competencies afterwards, as an action of the same statement, which is the order *goal, then competencies* that this design requires. A concurrent edit by `authenticated` waits for the goal lock before it touches any competency row, so it cannot hold a competency row that the cascade needs. A real cascade is tested in T5b.

**Errors and how the backend handles them (implemented in Stages 9.2–9.3):**

|SQLSTATE and constraint|Meaning|Backend result|
|---|---|---|
|`23514`, `competencies_active_limit`|50 active competencies already|`409 COMPETENCY_LIMIT_REACHED`|
|`23514`, `competencies_goal_not_active`|The goal is archived, missing or not the user's. The trigger cannot tell these apart, because the lock query cannot see an archived or a foreign goal.|The transaction is rolled back, then the service reads the goal in a new transaction. **Add a competency** (the goal id comes from the path): archived → `409 GOAL_ARCHIVED`; not visible → `404 GOAL_NOT_FOUND`. **Edit or archive a competency:** the UPDATE had found the row, so the goal exists and is the user's; the cause is a race with the archiving of the goal → `409 GOAL_ARCHIVED` (confirmed by the read)|
|`23505`, `competencies_active_name_unique`|The active name is taken|`409 COMPETENCY_NAME_EXISTS`|
|Zero rows from an UPDATE|Missing, not the user's, archived, or in an archived goal|The service reads, in this order: (1) the competency: not visible → `404 COMPETENCY_NOT_FOUND`; (2) its status is `archived` → `409 COMPETENCY_ARCHIVED`; (3) its goal: archived → `409 GOAL_ARCHIVED`. If the goal is active as well, the state is inconsistent (the row should have been updated) and the answer is `500`, as for goals (section 4.3.1)|
|`40P01` (deadlock), `40001` (serialization failure)|Not expected for `authenticated` writes with this design; kept as a safety net|**Retryable.** The backend repeats the whole user transaction a small, bounded number of times (the number is fixed in Stage 9.2). If it still fails, the answer is `503 SERVICE_UNAVAILABLE`.|
|`57014`|Statement timeout, for example a long wait for the lock|`504 DATABASE_TIMEOUT`|

**Assumptions that must be proven by the Stage 9.1 tests (none is assumed to hold):**

1. A locking read through Row Level Security is evaluated again after the wait, so a goal archived in the meantime is no longer returned (test T3).
2. `auth.uid()` works inside the statement trigger for `authenticated`, and `authenticated` can lock its own goal and cannot lock an archived or a foreign one.
3. The `WITH CHECK` of an INSERT policy runs after the `BEFORE ROW` trigger, so an INSERT into an archived or a foreign goal fails with `23514`, not `42501`.
4. The row count query in the row trigger sees the rows that the same multi-row INSERT has already inserted.
5. A tuple lock taken before the trigger does not make another transaction's unique-index check wait (test T1).
6. No test depends on the order in which PostgreSQL grants a lock to waiting transactions: that order is not guaranteed. Where several transactions wait, the tests assert only what holds in every order: no deadlock, every transaction ends within the time limit, and the final data equals the result of some serial order of the operations. A wait is asserted (with `pg_blocking_pids`) only where one transaction deterministically has to wait for another that is known to hold the lock.
7. A real `DELETE` of a goal removes its competencies through `ON DELETE CASCADE` after the goal row has been deleted and in the same statement, and a concurrent edit by `authenticated` is blocked at the statement trigger until the deleting transaction ends, without a deadlock (test T5b).
8. `deadlock_timeout` detection and the `statement_timeout` of 15 s bound every test.

If an assumption does not hold, the work stops and the problem is reported. The fallbacks are a variant without the `UPDATE` guard, or `SECURITY DEFINER`; the latter only with a separate approval.

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
    

The table `competencies` stores only the **expected** level of a competency (section 4.3). The current proficiency is not stored there (approved in Stage 9.0): it will be derived from the assessment history when the assessment tables exist.

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
|`service_role`|Keeps Supabase's default privileges; the migrations do not change them|Not used by the backend, which refuses any `DATABASE_URL` that does not log in as `skillloop_api`. Any write by `postgres` or `service_role` to `competencies` must follow the mandatory contract of section 4.8 (rule L2)|

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

#### 6.2.1 competencies — Planned

Approved in Stage 9.0; no migration exists yet. The pattern is that of `career_goals`: `REVOKE ALL` from `public`, `anon` and `authenticated` first, then only the minimum is granted to `authenticated`. `anon` and `skillloop_api` get nothing. There is no change to the grants of any existing table.

|Command|Grant to `authenticated`|Policy|
|---|---|---|
|SELECT|whole table|`competencies_select_own`: `user_id = (select auth.uid())`, any status, also in an archived goal|
|INSERT|columns `career_goal_id`, `user_id`, `name`, `description`, `rationale`, `category`, `knowledge_type`, `importance`, `expected_level`, `display_order`|`competencies_insert_own`: `WITH CHECK` `user_id = (select auth.uid())`, `status = 'active'`, and an active goal of the same user with `id = career_goal_id` exists|
|UPDATE|columns `name`, `description`, `rationale`, `category`, `knowledge_type`, `importance`, `expected_level`, `display_order`, `status`|`competencies_update_own`: `USING` `user_id = (select auth.uid())`, `status = 'active'`, and an active goal of the same user exists; `WITH CHECK` `user_id = (select auth.uid())` and an active goal of the same user exists|
|DELETE|none|none|

Notes:

- `status` is not insertable: a competency is always created `active` (the column default). `id`, `career_goal_id`, `user_id`, `created_at` and `updated_at` can never be changed by a client after creation, so a competency cannot be moved to another goal or user.
- `USING` of the UPDATE policy applies to the existing row: an archived competency, and any competency of an archived goal, is invisible to UPDATE (zero rows). `WITH CHECK` sees the new row; with the `status` CHECK constraint it allows `active` → `archived`. Nothing allows `archived` → `active`.
- The "active goal exists" part is an `EXISTS` subquery on `career_goals`. It is evaluated with the snapshot of the statement. The trigger of section 4.8 repeats the check after the lock is granted, which is what closes the race with archiving.
- INSERT: the row trigger runs before the `WITH CHECK`, so an INSERT into an archived or a foreign goal fails in the trigger (`23514`), not in the policy (`42501`). To test the policy as a separate barrier, the pgTAP test disables the trigger inside its own rolled-back transaction.
- Two or three independent barriers protect each command: the grant, the policy, and for the goal-related rules the trigger and the composite foreign key. The tests check them separately.
- The baseline test `00_security_baseline.test.sql` already requires Row Level Security on every table in `public`, no privileges for `anon`, no `TRUNCATE`, `REFERENCES` or `TRIGGER` for `authenticated`, and no executable functions for the API roles; the new table and functions must pass it unchanged.

### 6.3 Data API (PostgREST) — Implemented configuration, scope not decided

`supabase/config.toml` has `[api] enabled = true` with `schemas = ["public", "graphql_public"]`. The tables in `public` can therefore be reached through the Data API as well as through the backend. The database does not rely on the Data API being disabled: Row Level Security and the grants above are the barrier for both paths, and `anon` has no access. The migration of `career_goals` says the same.

This reflects the **local** configuration. The configuration of any remote Supabase project was not checked and is **unverified**. Whether the Data API should stay exposed is an open decision (section 8.3). Because the Data API accepts every statement that the grants and policies allow, the rules for competencies (sections 4.3.1 and 4.8) are written as database rules, so they also hold for multi-row statements sent through it.

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
|competencies|`(career_goal_id, status, display_order)` and the unique partial index `competencies_active_name_unique` (section 4.3)|
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
- `competencies`: the keys, constraints and indexes are specified in section 4.3; `expected_level` is restricted to beginner, intermediate or advanced.
- `current_level` is not stored in `competencies` (section 4.3); the scale of the assessments belongs to the open decision 5 in section 8.3.
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

- Archiving a career goal does not delete its competencies or sessions. It freezes its competencies: they stay readable and cannot be changed (section 4.3).
- Archiving a competency is one-way. Archived competencies are never deleted by clients and never become active again.
- Archiving a competency does not delete previous assessments.
- New evaluations create new historical records.
- Recommendations can be dismissed or completed without deleting them.
- Deleting a user account must follow an explicit data deletion policy. What happens to the planned tables on deletion (for example the rule of their foreign keys to `auth.users` and to each other) has not been decided. For `competencies` the planned behaviour is the same cascade as for goals (`auth.users` → `career_goals` → `competencies`). It is a technical consequence of the planned foreign keys and **not an approved policy**.

### 8.3 Open decisions

These are **not decided** by this document or by the migrations.

1. **Account and data deletion policy.** The current cascade is not an approved policy. No endpoint deletes an account. The decision determines what happens to goals, and later to sessions, answers and evaluations, when an account is removed, and the foreign-key rules of the planned tables, including the cascade through `career_goals` to `competencies` and the foreign keys of future tables to `competencies`.
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
|4. Create `competencies`|Planned. The design was approved in Stage 9.0; the migration is Stage 9.1a and its tests are Stages 9.1a and 9.1b (`IMPLEMENTATION-PLAN.md`)|
|5. Create `learning_sessions`|Planned|
|6. Create `session_items`|Planned; blocked by open decision 5 in section 8.3|
|7. Create `recommendations`|Planned|
|8. Add foreign keys, constraints and indexes|Implemented for `profiles` and `career_goals`; Planned for the other tables|
|9. Enable and test Row Level Security|Implemented for `profiles` and `career_goals` (pgTAP: 22 + 35 + 49 assertions); to be repeated for every new table|
|10. Connect FastAPI to Supabase|Implemented in the backend (`backend/app/db/`); the database-facing tests need a local Supabase stack|

---

**Document status:** Draft v1.3

**Next step:** Resolve the open decisions in section 8.3, in particular decision 5 (assessment scale and statuses), before creating the migrations of the planned tables. The migration of `competencies` (Stage 9.1) does not depend on decision 5 and waits only for the acceptance of the Stage 9.0 documentation; decision 5 still blocks `session_items`.
