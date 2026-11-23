

**Project:** SkillLoop  
**Version:** 1.1  
**Status:** Draft  
**Goal:** Build a functional MVP using an iterative development workflow.

---

## 1. Implementation Principles

Development must follow these principles:

1. Implement the smallest working version of each feature.
    
2. Work in small, clearly defined tasks.
    
3. Understand the implementation before accepting changes.
    
4. Write tests alongside application code.
    
5. Keep frontend and backend responsibilities separate.
    
6. Validate all AI-generated data.
    
7. Use Git commits to preserve working milestones.
    
8. Do not introduce technologies without a clear requirement.
    
9. Avoid unnecessary cloud infrastructure and costs.
    
10. Require human approval before accepting implementation changes.
    

Claude Code should propose a plan before making significant changes.

## 2. Development Workflow

Each implementation task follows this workflow:

```
Understand the requirement
          |
          v
Review relevant documentation
          |
          v
Create implementation plan
          |
          v
Human approval
          |
          v
Implement changes
          |
          v
Run automated tests
          |
          v
Review code and changes
          |
          v
Human approval
          |
          v
Git commit
```

If tests fail, investigate and correct the implementation before moving forward.

Claude Code must not silently expand the MVP scope.

## 3. Development Phases

### Phase 0 – Project Foundation

**Goal:** Prepare the development environment and project structure.

Tasks:

- Create the Git repository.
    
- Create the frontend application using Next.js, React and TypeScript.
    
- Create the backend application using Python and FastAPI.
    
- Configure Docker Compose.
    
- Configure environment variables.
    
- Add `.env.example`.
    
- Configure `.gitignore`.
    
- Add basic health endpoints.
    
- Verify that frontend and backend run locally.
    

Expected result:

- Frontend starts successfully.
    
- Backend starts successfully.
    
- Docker Compose runs both services.
    
- Backend health endpoint responds successfully.
    

Tests:

- Backend health endpoint test.
    
- Basic frontend startup verification.
    
- Docker Compose startup verification.
    

### Phase 1 – Database and Authentication

**Goal:** Establish persistent storage and secure user authentication.

Tasks:

- Configure Supabase project.
    
- Configure Supabase Auth.
    
- Create database migrations.
    
- Implement `profiles`.
    
- Implement `career_goals`.
    
- Implement initial database constraints.
    
- Configure Row Level Security.
    
- Implement FastAPI JWT validation.
    
- Implement profile endpoints.
    
- Implement career goal endpoints.
    

Expected result:

- Users can register and authenticate.
    
- Users can create and retrieve their career goal.
    
- User data is isolated.
    
- Data persists between sessions.
    

Tests:

- Authentication validation.
    
- Database integration tests.
    
- User ownership tests.
    
- RLS verification.
    
- Career goal constraint tests.
    

### Phase 2 – Competency Map

**Goal:** Generate and manage the user's competency map.

Phase 2 is delivered in two parts:

- **Phase 2A – Manual competency map (Stage 9).** The user adds, edits and archives competencies of the active goal by hand. No AI. Described in detail below.
- **Phase 2B – AI-generated competency map (a later stage).** The AI tasks of the original phase: the AI provider abstraction, the OpenAI integration, prompt management, competency map generation, validation of structured AI output, and the endpoints that review and save a generated map. It adds the `source` column.

Tasks of Phase 2A:

- Design and approve the competency model (Stage 9.0).
- Implement the `competencies` migration with Row Level Security and the write-synchronisation triggers (Stages 9.1a and 9.1b).
- Implement competency persistence and the endpoints `GET` and `POST` (Stage 9.2), `PATCH` and archive (Stage 9.3).
- Frontend competency map view and editing (a later stage, after the API is accepted).

Tasks of Phase 2B:

- Implement the AI provider abstraction.
- Configure the OpenAI API integration.
- Implement prompt management.
- Implement competency map generation.
- Validate structured AI output.
- Allow the user to review a generated map before it is saved.

Expected result:

- The user selects a professional role and seniority.
- The user builds the competency map by hand (Phase 2A); AI proposes one (Phase 2B).
- The user can review and modify the map.
- Approved competencies are saved in PostgreSQL.

Tests:

- Competency CRUD tests (Phase 2A).
- User ownership tests (Phase 2A).
- Database concurrency tests (Phase 2A, Stage 9.1b).
- AI provider integration tests, structured output validation tests (Phase 2B).
- Frontend component tests (when the frontend part is built).

#### Stage 9 – Manual competency map (Phase 2A) in detail

Approved decisions: competencies belong to one career goal (no global catalogue); `user_id` plus a composite foreign key to `career_goals (id, user_id)`; no `current_level`, `source` or `expectations`; a flat text `category`; `knowledge_type` is `memory`, `application` or `documentation`; the name of an active competency is unique per goal ignoring case; at most 50 active competencies per goal; archiving is one-way; an archived goal freezes its competencies; the cascade from `auth.users` stays, and the account deletion policy is **not** approved; manual CRUD only; no saving of a whole map; no `SECURITY DEFINER` and no wider grants. Design details: `DATABASE-DESIGN.md` sections 4.3, 4.3.1, 4.8 and 6.2.1; API contract: `API-DESIGN.md` section 7.

Competencies stay **Planned** in the documentation until Stage 9.1 has passed its required tests.

|Stage|Scope|Status|
|---|---|---|
|9.0|Documentation: `DATABASE-DESIGN.md`, `API-DESIGN.md`, `MVP-SCOPE.md`, `IMPLEMENTATION-PLAN.md`|Done, awaiting acceptance|
|9.1a|Migration and pgTAP tests|Planned|
|9.1b|Integration tests with several connections (T1–T7)|Planned|
|9.2|Backend: list and add competencies|Planned|
|9.3|Backend: edit and archive competencies|Planned|

**Stage 9.1a – migration and pgTAP tests.** One migration, created with `supabase migration new create_competencies`: `UNIQUE (id, user_id)` on `career_goals`; the table `competencies` with its constraints and indexes; Row Level Security, grants and policies; `competencies_set_updated_at`; the statement trigger `competencies_lock_goal` and the row trigger `competencies_enforce_goal_rules` with their functions (design V3, `DATABASE-DESIGN.md` section 4.8). The header comment of the migration states the mandatory contract for privileged writes (rule L2): every write to `competencies` by `postgres` or `service_role` first locks the goal. Applying it and running the tests is local only (`supabase db reset`, `supabase test db`) and needs the user's approval at that moment. The test file is `supabase/tests/database/30_competencies.test.sql`. All data operations run as `authenticated` (`SET LOCAL ROLE` plus `request.jwt.claims`), not only as `postgres`. The tests are:

- Structure: exact columns (no `current_level`, `source`, `expectations`), primary key, defaults, the composite foreign key with `ON DELETE CASCADE`, `UNIQUE (id, user_id)` on `career_goals`, the indexes, Row Level Security enabled, the triggers and their kind (statement or row).
- Functions: `volatile`, `SECURITY INVOKER`, pinned `search_path`, `EXECUTE` not available to `anon`, `authenticated` or `skillloop_api`.
- Constraints: every CHECK (boundaries 100/101, 1000/1001, 60/61, bad enum values, `not_assessed` as `expected_level`, negative `display_order`).
- Grants: the column grants of section 6.2.1; no `DELETE`, `TRUNCATE` or table-wide `INSERT`/`UPDATE`; `anon` has nothing; **the grants of `career_goals` are unchanged** (still no table-wide `UPDATE` or `DELETE`).
- Locking capability: as `authenticated`, `SELECT … FOR NO KEY UPDATE` on the own active goal returns the row; on an archived goal and on another user's goal it returns no row.
- Isolation: user A cannot read, insert, update or archive anything of user B; an INSERT with another user's `user_id` or goal id is refused; an owner mismatch is refused by the composite key (checked as `postgres`).
- **No active goal:** the statement trigger does nothing and raises nothing; INSERT is refused with `23514` `competencies_goal_not_active`; UPDATE and archive change zero rows; SELECT still returns the competencies of the archived goal.
- **Archived goal or another user's goal:** INSERT is refused with `23514` `competencies_goal_not_active` (the trigger runs before the policy); UPDATE and archive change zero rows; with the trigger disabled inside the test transaction, the policy alone refuses the INSERT (`42501`) and the UPDATE (zero rows), so each barrier is tested separately.
- One-way archiving: `active` → `archived` works; changing or reactivating an archived competency changes zero rows; it stays visible to its owner.
- Frozen goal: after the goal is archived, no competency can be added, edited or archived, and all stay readable.
- Name uniqueness: `Docker`, `docker` and `  DOCKER ` collide (`23505`, `competencies_active_name_unique`); the same name in another goal is allowed; an archived competency releases the name; renaming into an existing name fails.
- **Limit of 50, one row at a time:** 50 pass, the 51st fails with `competencies_active_limit`; archiving one frees a slot; archived rows do not count (50 active plus many archived still refuses a new one); the limit is per goal (another user's goal has its own 50).
- **Multi-row INSERT:** one statement inserting 50 rows into an empty goal passes (this also proves that the row trigger sees the rows of the same statement); one statement that would create the 51st active competency fails with `competencies_active_limit` and **no row of that statement remains**; the same statement against a goal that already holds 49 fails as a whole; a statement that contains a duplicate name fails as a whole with `23505`.
- **Multi-row UPDATE:** one statement renaming several rows is atomic (if one row conflicts, no row changes); rows hidden by Row Level Security (another user's, archived, in an archived goal) are not changed and raise no error; archiving several rows in one statement frees that many slots; swapping two names in one statement fails with `23505`; a multi-row UPDATE cannot change `career_goal_id` or `user_id` (no grant).
- Statement trigger: fires also for a statement that changes zero rows; does nothing for a user without an active goal.
- Privileged role: as `postgres`, a write that follows the contract of rule L2 (it locks the goal first) works; a write that does not follow it is still refused for an archived goal and for the 51st active competency (the row trigger), so a broken contract cannot corrupt data.
- `updated_at` changes on UPDATE and `created_at` does not; deleting the user in `auth.users` removes the goals and the competencies (a statement of the current technical state, not of an approved policy).

The existing tests `00`, `10` and `20` must pass unchanged. If `20_career_goals.test.sql` turns out to assert an exact list of constraints, the missing assertion goes into `30_competencies.test.sql` instead of changing the old test without approval.

**Stage 9.1b – integration tests with several connections (required).** pgTAP runs in one session and cannot wait for a lock, so these tests are written in Python (`backend/tests/test_competencies_db_concurrency.py`) on plain SQL, in the style of `test_services_concurrency.py`: a thread keeps a transaction open, every wait is proven with `pg_blocking_pids`, `threading.Barrier` starts operations together, and no `sleep()` decides an order. Operations run as `authenticated` through the real `user_transaction()`; fixtures and observation use the administration connection. Every test has a time limit; a deadlock (`psycopg.errors.DeadlockDetected`, `40P01`) is a failure. They need the local Supabase stack and the user's approval to run. C0 first asserts that the sessions use `read committed`. **No test depends on the order in which PostgreSQL wakes waiting transactions**, because that order is not guaranteed. A wait is asserted only where one transaction has to wait for another that is known to hold the lock. Where several transactions wait, the tests assert only what holds in every order: no deadlock, every transaction ends within the time limit, and the final data equals the result of some serial order of the operations.

|Test|Scenario|Expected result|
|---|---|---|
|T1|The suspected cycle "A locks competency R and waits for the goal; B holds the goal and inserts a name that collides with R": helper H (playing B) locks the goal row (`FOR NO KEY UPDATE`) and keeps the transaction open; A updates competency R and waits for the goal (proven); H then inserts a name that collides with R. Variant T1b: H inserts a different name.|H gets `23505` at once and does not wait for A; after H ends, A succeeds; no `40P01`. T1b: A waits, H commits, A succeeds.|
|T2|Two edits that would swap names, multi-statement so that the cycle is deterministic: T1 renames R1 `a`→`z` and holds; T2 renames R2 `b`→`a`; T1 renames R3 `c`→`b`.|No `40P01`. T2 waits for the goal lock; T1's second statement gets `23505`; after T1 ends T2 also gets `23505`.|
|T3|Archiving a goal against competency writes, both orders, for INSERT, edit and archive of a competency: the goal archiving is held and the write waits, then the write is held and the archiving waits.|The write that waited is refused with `competencies_goal_not_active` (proves that the row security check is evaluated again after the wait); the write that came first is kept; afterwards no write is possible.|
|T4|A multi-row UPDATE against a single-row edit of one of the same rows: helper H holds the goal lock; Z (one statement that updates several competencies, among them R2) and X (a single-row edit of R2) are both started while H holds the lock. Each is proven to be blocked by H (`pg_blocking_pids` contains H), which does not depend on which of them PostgreSQL wakes first. Then H commits. Repeated many times, with both start orders.|No `40P01`. Both transactions end within the time limit (neither hangs). Each ends with success or with the error that its position in a serial order gives. The final data equals the result of Z then X **or** of X then Z; the test accepts either. It does **not** assert which of them runs first.|
|T5a|The order goal → competencies of a deletion that follows rule L2, with an explicit deletion of the competencies: a privileged helper locks the goal first and then runs `DELETE FROM public.competencies` for the goal, while U edits one of them. U is started while the helper holds the goal lock.|No `40P01`: U is blocked by the helper (proven) before it touches any competency row. After the helper commits, U changes zero rows, because the competency is gone.|
|T5b|**The real cascade.** H, a privileged role, runs a real `DELETE FROM public.career_goals WHERE id = <goal>` and **no explicit DELETE on `competencies`**: PostgreSQL itself removes the competencies through `ON DELETE CASCADE`. U, as `authenticated`, tries to edit one of those competencies. At most one transaction waits in each part, so no part depends on the order in which PostgreSQL wakes waiting transactions. **(1) H first:** H's DELETE statement completes (the cascade has run) and H keeps the transaction open; then U starts and is proven to be blocked by H; then H commits. **(2) U first:** U's edit completes and U keeps the transaction open; then H's DELETE is started in a thread and is proven to be blocked by U; then U commits. **(3) Simultaneous:** U and H are started together with a `Barrier`, repeated many times with fresh data. This is a best effort to hit the interval in which the goal row is already deleted but the cascade has not yet run; the test does not require that interval to be hit.|No `40P01` in any part, and every transaction ends within the time limit. **(1)** After H commits U changes zero rows (the competency is gone). **(2)** U's edit succeeds (one row); after U commits, H's DELETE ends and its cascade also removes the competency that U edited. **(3)** U changes one row or zero rows, depending on the order that happened; both are accepted. In every part the final state is the same, checked with the administration connection: the goal does not exist, none of its competencies exists, and they disappeared only through the cascade (no explicit DELETE on `competencies` was issued). The deletion does not fire the competency triggers, which exist for INSERT and UPDATE only.|
|T6|The pairs INSERT, edit, archive of a competency and archive of the goal, in both orders, plus a multi-row INSERT and a multi-row UPDATE as one side. Includes **the limit**: 49 active competencies, several concurrent INSERTs (with a `Barrier`, repeated), and the case "held INSERT that makes the 50th, then a second INSERT" for both commit and rollback; and "50 active, one is being archived, an INSERT waits".|The second operation waits (proven) and its result equals the serial result; **never more than 50 active competencies**; exactly one INSERT wins at 49; after the archiving commits the waiting INSERT succeeds; no `40P01`.|
|T7|A bounded stress run: many threads mix INSERT, edit, archive of a competency and archive of the goal for a few seconds.|No `40P01`; at most 50 active competencies; unique active names; no competency written after the archiving of its goal committed.|

These tests cannot prove the absence of deadlocks in general; T2, T5a and T5b reproduce the cycles that are known (T5b with the real cascade), T4 checks a further interleaving without depending on lock order, and T7 is a bounded search for others. If an assumption listed in `DATABASE-DESIGN.md` section 4.8 fails, the work stops and the problem is reported before anything is changed.

**Stage 9.2 – list and add.** `GET /career-goals/{goal_id}/competencies` and `POST /career-goals/{goal_id}/competencies`; the model, schemas, repository, service and router; the new errors `COMPETENCY_NAME_EXISTS` and `COMPETENCY_LIMIT_REACHED` (the service maps the database errors by constraint name, without a check-then-insert); the bounded retry of `40P01` and `40001` in the user transaction and the answer `503 SERVICE_UNAVAILABLE` when it fails; unit tests with the fakes of `db_fakes.py`, API tests, integration tests, an end-to-end test and an update of the OpenAPI contract test; the placeholder routes in `test_allow_header.py` are replaced by the real ones. Tests include: no active goal, archived goal, another user's goal, the 50th and 51st competency, a duplicate name, and the retry.

**Stage 9.3 – edit and archive.** `PATCH /competencies/{competency_id}` and `POST /competencies/{competency_id}/archive`; the errors `COMPETENCY_NOT_FOUND` and `COMPETENCY_ARCHIVED` and the precedence rules of `API-DESIGN.md` section 7; the same kinds of tests, including a competency of an archived goal and another user's competency.

**Out of scope for Stage 9:** AI generation, saving a whole map in one request, a reorder endpoint, filtering or pagination of the list, restoring an archived competency or goal, the account deletion policy, the frontend, the assessment tables.

### Phase 3 – Initial Diagnostic Assessment

**Goal:** Evaluate the user's existing knowledge.

Tasks:

- Implement diagnostic session management.
    
- Implement diagnostic question generation.
    
- Implement assessment state management.
    
- Implement answer submission.
    
- Implement follow-up question generation.
    
- Implement follow-up limits.
    
- Implement assessment evaluation.
    
- Store assessment history.
    
- Build diagnostic frontend workflow.
    

Expected result:

- User can start an initial diagnostic.
    
- AI generates questions related to selected competencies.
    
- User submits explanations.
    
- AI asks follow-up questions when necessary.
    
- AI evaluates answers and identifies knowledge gaps.
    
- Results are stored.
    

Tests:

- Assessment state transition tests.
    
- Follow-up workflow tests.
    
- AI response validation tests.
    
- Assessment persistence tests.
    
- Insufficient evidence handling tests.
    

### Phase 4 – Learning Loop

**Goal:** Allow users to continuously develop individual competencies.

Tasks:

- Implement learning sessions.
    
- Implement learning question generation.
    
- Implement text-based explanations.
    
- Implement AI evaluation.
    
- Implement structured feedback.
    
- Implement competency proficiency calculation.
    
- Implement learning history.
    
- Build frontend learning interface.
    

Expected result:

- User selects a competency.
    
- AI generates a learning question.
    
- User explains the topic.
    
- AI evaluates the explanation.
    
- Competency proficiency is updated based on assessment history.
    
- User can review previous learning sessions.
    

Tests:

- Learning session tests.
    
- Assessment calculation tests.
    
- Historical assessment tests.
    
- Feedback validation tests.
    
- Frontend workflow tests.
    

### Phase 5 – Voice Input

**Goal:** Allow users to explain competencies using their voice.

Tasks:

- Implement browser audio recording.
    
- Implement audio upload endpoint.
    
- Validate audio files.
    
- Integrate OpenAI Speech-to-Text.
    
- Pass transcriptions into the existing assessment workflow.
    
- Handle transcription errors.
    
- Build frontend recording controls.
    

Expected result:

- User can record an explanation.
    
- Backend transcribes the audio.
    
- The transcript follows the same assessment process as text input.
    
- Original audio is not permanently stored by default.
    

Tests:

- Audio validation tests.
    
- Transcription integration tests.
    
- Error handling tests.
    
- Voice-to-assessment workflow tests.
    

### Phase 6 – Recommendations and Progress

**Goal:** Help users decide what to learn next and understand their progress.

Tasks:

- Implement recommendation generation.
    
- Implement recommendation persistence.
    
- Implement recommendation status updates.
    
- Implement competency progress endpoints.
    
- Implement overall progress endpoints.
    
- Build progress dashboard.
    
- Build recommendation interface.
    

Expected result:

- User sees current competency levels.
    
- User can compare current and expected proficiency.
    
- AI recommends relevant competencies.
    
- User can accept or dismiss recommendations.
    
- Learning history remains accessible.
    

Tests:

- Recommendation logic tests.
    
- Progress calculation tests.
    
- API integration tests.
    
- User ownership tests.
    

### Phase 7 – Integration and MVP Stabilization

**Goal:** Verify that the complete MVP works reliably.

Tasks:

- Test complete registration and onboarding flow.
    
- Test competency map generation.
    
- Test initial diagnostic.
    
- Test text learning workflow.
    
- Test voice learning workflow.
    
- Test AI follow-up questions.
    
- Test recommendations.
    
- Test persistence across sessions.
    
- Verify responsive layouts.
    
- Improve loading and error states.
    
- Review security configuration.
    
- Fix identified defects.
    

Expected result:

A functional SkillLoop MVP that supports the complete learning cycle.

## 4. Frontend Development Principles

The frontend uses:

- Next.js.
    
- React.
    
- TypeScript.
    
- Responsive design.
    
- PWA capabilities.
    

Main application areas:

1. Authentication.
    
2. Onboarding.
    
3. Career goal selection.
    
4. Competency map.
    
5. Initial diagnostic.
    
6. Learning interface.
    
7. Progress dashboard.
    
8. Recommendations.
    
9. User settings.
    

The interface must work on desktop and mobile devices.

Desktop and mobile users share the same account and synchronized learning data.

## 5. Backend Development Principles

The backend uses:

- Python.
    
- FastAPI.
    
- Pydantic.
    
- Supabase PostgreSQL.
    
- Supabase Auth.
    
- OpenAI API.
    

Backend responsibilities:

- Authentication and authorization.
    
- Business logic.
    
- Database access.
    
- AI orchestration.
    
- Speech-to-Text integration.
    
- Input validation.
    
- Error handling.
    
- Logging.
    

AI provider communication must remain inside the backend.

## 6. Testing Strategy

Testing should be introduced from the beginning rather than postponed until the end.

### Backend

- Unit tests with pytest.
    
- API tests.
    
- Database integration tests.
    
- Authentication tests.
    
- AI response validation tests.
    
- Assessment workflow tests.
    

### Frontend

- Component tests.
    
- Form validation tests.
    
- User interaction tests.
    
- Critical workflow tests.
    

### End-to-End

Verify the complete user journey:

1. Register.
    
2. Select a professional role.
    
3. Generate competency map.
    
4. Edit and approve the map.
    
5. Complete diagnostic assessment.
    
6. Select a competency.
    
7. Explain a topic.
    
8. Receive AI feedback.
    
9. Review progress.
    
10. Follow a recommendation.
    

AI tests should use deterministic mocked responses for most automated tests. Real provider integration tests should be limited and controlled to manage cost.

## 7. CI/CD and Deployment

Production architecture:

- **Frontend:** Vercel.
    
- **Backend:** AWS container hosting.
    
- **Database and authentication:** Supabase.
    
- **AI provider:** OpenAI API.
    
- **CI/CD:** GitHub Actions.
    

AWS hosting service will be selected before production deployment.

### CI Pipeline

GitHub Actions should:

1. Install dependencies.
    
2. Run backend tests.
    
3. Run frontend tests.
    
4. Run linting and type checks.
    
5. Build frontend.
    
6. Build backend Docker image.
    

### CD Pipeline

Deployment should be introduced after the application passes CI.

Tasks:

- Configure production environment variables.
    
- Configure backend hosting on AWS.
    
- Configure frontend deployment on Vercel.
    
- Configure production Supabase access.
    
- Configure secrets securely.
    
- Verify health endpoints.
    
- Test production authentication and API communication.
    

## 8. Cost Control

The MVP should minimize unnecessary cloud expenses.

Principles:

- Use local Docker Compose for development.
    
- Use Supabase Free plan initially.
    
- Avoid unnecessary AWS resources during local development.
    
- Keep AI requests controlled.
    
- Use mocked AI responses in automated tests.
    
- Track actual AI usage.
    
- Deploy production infrastructure only when needed.
    

Cloud resources must be monitored to prevent unexpected costs.

## 9. Definition of Done

An implementation task is complete when:

- Requirements are satisfied.
    
- Code follows the project architecture.
    
- Relevant tests pass.
    
- No known critical errors remain.
    
- Changes have been reviewed.
    
- Documentation is updated when necessary.
    
- The user has approved the result.
    
- A Git commit records the completed milestone.
    

## 10. MVP Completion Criteria

SkillLoop MVP is considered complete when a user can:

1. Register and log in.
    
2. Select one professional goal and seniority level.
    
3. Generate and edit a competency map.
    
4. Complete an initial diagnostic assessment.
    
5. Learn a competency using text explanations.
    
6. Learn a competency using voice explanations.
    
7. Receive AI evaluation and feedback.
    
8. See current competency proficiency.
    
9. Review learning history.
    
10. Receive and manage AI learning recommendations.
    
11. Continue learning across desktop and mobile devices.
    

All core data must persist between sessions.

The application must enforce user data ownership and pass its critical automated tests.

---

**Document status:** Draft v1.1

**Next step:** Review the implementation plan and prepare the project repository for Phase 0.