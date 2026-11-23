Stage 9.0 — przed zatwierdzeniem dokumentacji wykonaj jeszcze jedną rundę kontroli.

1. **T4 / FIFO:** Nie opieraj poprawności testu na gwarancji FIFO PostgreSQL. Zmień T4 tak, aby weryfikował brak deadlocka, poprawność wyników i zakończenie obu transakcji. Nie wymagaj konkretnej kolejności wykonania, jeśli nie wynika ona z gwarantowanego zachowania PostgreSQL.

2. **Rola uprzywilejowana:** Doprecyzuj w dokumentacji, że wszystkie przyszłe zapisy do competencies wykonywane przez postgres lub service_role muszą najpierw zablokować odpowiedni career_goal. Zaznacz, że jest to obowiązkowy kontrakt architektury.

3. **Kolejność błędów:** Zweryfikuj, czy zaproponowana kolejność błędów jest spójna z RLS i rzeczywistą możliwością rozpoznania stanu rekordu. Nie zmieniaj jej bez uzasadnienia.

4. Sprawdź, czy wszystkie cztery dokumenty są ze sobą spójne po poprawkach.

Ograniczenia:
- Nie twórz migracji.
- Nie zmieniaj schematu bazy.
- Nie uruchamiaj Supabase ani testów wymagających bazy.
- Nie czytaj .env.
- Nie rób commitów ani pushów.

Na końcu przedstaw krótki raport zmian i zatrzymaj się. Nie rozpoczynaj Stage 9.1.




Zgłoszenie przyszło dwa razy z identyczną treścią, więc traktuję je jako jedno zadanie. Zaczynam od zlokalizowania wszystkich miejsc, których dotyczą cztery punkty.

  Searched for 2 patterns, read 2 files

Wprowadzam poprawki. Najpierw DATABASE-DESIGN.md (zdanie o cyklach, kontrakt L2, tabela błędów, założenia testowe, wiersze w 4.3.1 i 6.1).

● Update(docs\DATABASE-DESIGN.md)
Added 1 line, removed 1 line
- **Freezing.** Archiving or editing a goal needs the same row lock, so it is serialised with every competency write of that goal. A statement that waited for the lock checks the status again a
fter it is granted (a locking read in READ COMMITTED returns the latest version of the row, and the row is skipped if it no longer matches). A write that got the lock first commits before the a
rchiving proceeds; a write that comes later is refused.
- A statement that waited for the lock still uses the snapshot it took at its start for its own scan and for the `WITH CHECK` of the policies. The row trigger is therefore the authoritative che
ck; the policies stay as a second barrier.

**Why this is free of lock cycles.** All locks on competencies of one goal are taken in the order *goal row first, then competency rows*, and the first lock is exclusive among writers. The writ
er that holds the goal lock never waits for something held by a writer that waits for the goal lock, because the waiting writers have not locked or changed any competency yet. Unique-index chec
ks only wait for a transaction that has really changed a row (a lock-only tuple lock does not make a unique check wait), and only the goal-lock holder changes rows. The deadlocks that this desi
gn removes are described in `IMPLEMENTATION-PLAN.md` (tests T2, T4 and T5): two edits swapping names under a shared lock, a multi-row UPDATE against a single-row edit once the lock is exclusive
, and the order goal → competencies used by cascading deletion.
**Why this is free of lock cycles.** All locks on competencies of one goal are taken in the order *goal row first, then competency rows*, and the first lock is exclusive among writers. The writ
er that holds the goal lock never waits for something held by a writer that waits for the goal lock, because the waiting writers have not locked or changed any competency yet. Unique-index chec
ks only wait for a transaction that has really changed a row (a lock-only tuple lock does not make a unique check wait), and only the goal-lock holder changes rows. The cycles that this design
removes are reproduced by tests of `IMPLEMENTATION-PLAN.md`: two edits swapping names under a shared lock (T2), and the order goal → competencies used by cascading deletion (T5). T4 covers a mu
lti-row UPDATE against a single-row edit. Which of two waiting transactions PostgreSQL wakes first is not guaranteed, so T4 does not depend on it and asserts only outcomes that hold in every or
der.

**Invariants (approved):**


● Update(docs\DATABASE-DESIGN.md)
Added 4 lines, removed 1 line
**Invariants (approved):**

- **L1. Lock order: the goal row first, then competency rows. Never the reverse.** For `authenticated` the statement trigger enforces it. No transaction may lock or change a competency before l
ocking the goal row.
- **L2. A privileged role** (`postgres`, `service_role`; it has no `auth.uid()`, so the statement trigger does nothing for it) **must lock the goal row first**, in the same transaction, before
it writes to `competencies` (`SELECT … FROM career_goals WHERE id = … FOR NO KEY UPDATE`). The row trigger still enforces the status and the limit for it. This also applies to any future code,
for example a job that writes both a goal and its competencies.
- **L2. Mandatory architecture contract for privileged writes.** A privileged role (`postgres`, `service_role`) has no `auth.uid()`, so the statement trigger does nothing for it. **Every write
to `competencies` made by `postgres` or `service_role`, now or in the future, must first lock the career goal in the same transaction**: `SELECT 1 FROM public.career_goals WHERE id = <goal id>
FOR NO KEY UPDATE`, before the first statement that touches `competencies`. The rule covers INSERT, UPDATE and DELETE and every kind of writer: data migrations, backfills, seed and repair scrip
ts, administrative tools and future background jobs. A write that spans several goals locks all of them first, in ascending `id` order. Reading is not restricted.
  - This is a **mandatory contract of the architecture**, not a recommendation. The database does not enforce it. Breaking it does not corrupt data, because the row trigger still enforces the s
tatus of the goal and the limit of 50 for these roles. It only loses deadlock freedom: a privileged writer that locks a competency row before the goal can deadlock with an `authenticated` write
r that already holds the goal.
  - The backend does not use these roles (section 6.1), so today the contract binds migrations and operators. It binds any new code that introduces such a role.
  - Every migration or script that writes to `competencies` states in its header that it follows this contract. A change that adds a privileged writer needs a review against this rule.
- **L3. The triggers must stay enabled for application writes.** `ALTER TABLE … DISABLE TRIGGER` and `session_replication_role = replica` are not used by the application or by data migrations.
An application role cannot do either.
- **L4. The statement lock relies on at most one active goal per user and on writes being allowed only in the active goal** (`one_active_goal_per_user` and the policies of section 6.2.1). If mu
ltiple active goals are ever introduced (section 9), this design must be revised first.
- **Not required with this design:** limiting a request to one write statement. The lock is held until the transaction ends, so several statements in one transaction stay safe for `authenticate
d`.

● Update(docs\DATABASE-DESIGN.md)
Added 2 lines, removed 2 lines
|SQLSTATE and constraint|Meaning|Backend result|
|---|---|---|
|`23514`, `competencies_active_limit`|50 active competencies already|`409 COMPETENCY_LIMIT_REACHED`|
|`23514`, `competencies_goal_not_active`|The goal is archived, missing or not the user's|The service reads the goal: archived → `409 GOAL_ARCHIVED`; missing → `404 GOAL_NOT_FOUND`|
|`23514`, `competencies_goal_not_active`|The goal is archived, missing or not the user's. The trigger cannot tell these apart, because the lock query cannot see an archived or a foreign goal.|T
he transaction is rolled back, then the service reads the goal in a new transaction. **Add a competency** (the goal id comes from the path): archived → `409 GOAL_ARCHIVED`; not visible → `404 G
OAL_NOT_FOUND`. **Edit or archive a competency:** the UPDATE had found the row, so the goal exists and is the user's; the cause is a race with the archiving of the goal → `409 GOAL_ARCHIVED` (c
onfirmed by the read)|
|`23505`, `competencies_active_name_unique`|The active name is taken|`409 COMPETENCY_NAME_EXISTS`|
|Zero rows from an UPDATE|Missing, not the user's, archived, or in an archived goal|The service reads the competency and the goal and answers `404` or `409` (section 4.3.1)|
|Zero rows from an UPDATE|Missing, not the user's, archived, or in an archived goal|The service reads, in this order: (1) the competency: not visible → `404 COMPETENCY_NOT_FOUND`; (2) its statu
s is `archived` → `409 COMPETENCY_ARCHIVED`; (3) its goal: archived → `409 GOAL_ARCHIVED`. If the goal is active as well, the state is inconsistent (the row should have been updated) and the an
swer is `500`, as for goals (section 4.3.1)|
|`40P01` (deadlock), `40001` (serialization failure)|Not expected for `authenticated` writes with this design; kept as a safety net|**Retryable.** The backend repeats the whole user transaction
 a small, bounded number of times (the number is fixed in Stage 9.2). If it still fails, the answer is `503 SERVICE_UNAVAILABLE`.|
|`57014`|Statement timeout, for example a long wait for the lock|`504 DATABASE_TIMEOUT`|


● Update(docs\DATABASE-DESIGN.md)
Added 1 line, removed 1 line
3. The `WITH CHECK` of an INSERT policy runs after the `BEFORE ROW` trigger, so an INSERT into an archived or a foreign goal fails with `23514`, not `42501`.
4. The row count query in the row trigger sees the rows that the same multi-row INSERT has already inserted.
5. A tuple lock taken before the trigger does not make another transaction's unique-index check wait (test T1).
6. The lock queue on the goal row is first-in-first-out, which test T4 uses to set up its interleaving. If it is not, T4 reports a setup failure instead of passing.
6. No test depends on the order in which PostgreSQL grants a lock to waiting transactions: that order is not guaranteed. Where several transactions wait, the tests assert only what holds in eve
ry order: no deadlock, every transaction ends within the time limit, and the final data equals the result of some serial order of the operations. A wait is asserted (with `pg_blocking_pids`) on
ly where one transaction deterministically has to wait for another that is known to hold the lock.
7. `deadlock_timeout` detection and the `statement_timeout` of 15 s bound every test.

If an assumption does not hold, the work stops and the problem is reported. The fallbacks are a variant without the `UPDATE` guard, or `SECURITY DEFINER`; the latter only with a separate approv
al.

● Update(docs\DATABASE-DESIGN.md)
Added 1 line, removed 1 line
|**One INSERT of several competencies** (not exposed by the MVP API, possible through the Data API)|The statement lock is taken once. The row trigger counts the active competencies **including
the rows that the same statement already inserted**. The first row that would be the 51st active competency raises `23514` `competencies_active_limit`, and **the whole statement is rolled back:
 no row is inserted**. Exactly 50 active rows pass. A duplicate name inside the statement raises `23505` and also rolls back the whole statement.|The MVP API adds one competency per request; th
e limit is reported as `409 COMPETENCY_LIMIT_REACHED`|
|**One UPDATE of several competencies** (not exposed by the MVP API, possible through the Data API)|The statement lock is taken before any row is read or locked. Rows are processed in an unspec
ified order and the statement is atomic: if one row fails (for example `23505`), no row is changed. Rows that Row Level Security hides (another user's, archived, or in an archived goal) are sil
ently left out. The unique index is not deferrable, so swapping two names in a single statement fails with `23505` even if the final state would be valid. Archiving several rows is allowed and
frees slots.|The MVP API changes one competency per request|
|**A goal is archived while a competency statement is waiting for the lock**|After the lock is granted the row trigger checks the status again and refuses with `23514` `competencies_goal_not_ac
tive`. This also covers UPDATE, which would otherwise have been silently filtered.|`409 GOAL_ARCHIVED`|
|**A privileged role** (`postgres`, `service_role`) writes to `competencies`|No statement lock (no `auth.uid()`). The row trigger still enforces status and limit. The role must lock the goal fi
rst (section 4.8, rule L2).|Not used by the backend|
|**A privileged role** (`postgres`, `service_role`) writes to `competencies`|No statement lock (no `auth.uid()`). The row trigger still enforces status and limit, but deadlock freedom now depen
ds on the writer: the role **must lock the goal first**. This is a mandatory contract of the architecture (section 4.8, rule L2).|Not used by the backend|

### 4.4 learning_sessions — Planned


● Update(docs\DATABASE-DESIGN.md)
Added 1 line, removed 1 line
|Situation|Result in the database|Result in the API (Stages 9.2–9.3)|
|---|---|---|
|**The user has no active goal.** INSERT of a competency|The statement trigger finds no goal to lock and does nothing; the row trigger finds no active goal and refuses with `23514`, constraint
`competencies_goal_not_active`. Nothing is written.|The service reads the goal: the user's own archived goal → `409 GOAL_ARCHIVED`; no such goal → `404 GOAL_NOT_FOUND`|
|**The user has no active goal.** UPDATE or archive of a competency|Zero rows: the UPDATE policy requires an active goal. No error is raised.|The service reads the competency: not visible → `40
4 COMPETENCY_NOT_FOUND`; visible and itself archived → `409 COMPETENCY_ARCHIVED`; visible in an archived goal → `409 GOAL_ARCHIVED`|
|**The user has no active goal.** UPDATE or archive of a competency|Zero rows: the UPDATE policy requires an active goal. No error is raised.|The service reads the competency and, if needed, it
s goal, in this order: not visible → `404 COMPETENCY_NOT_FOUND`; visible and itself archived → `409 COMPETENCY_ARCHIVED`; otherwise the goal is archived → `409 GOAL_ARCHIVED` (section 4.8)|
|**The target goal is archived** (the user may have another, active goal)|INSERT: `23514` `competencies_goal_not_active`, as above. UPDATE: zero rows. The statement trigger locks the user's act
ive goal, which is harmless.|As in the two rows above|
|**The target goal belongs to another user**|Indistinguishable from a missing goal: the lock query cannot see it. INSERT: `23514` `competencies_goal_not_active`. UPDATE of another user's compet
ency: zero rows. The Row Level Security `WITH CHECK` is a second barrier (`42501`) if the trigger were absent.|`404 GOAL_NOT_FOUND` or `404 COMPETENCY_NOT_FOUND`; never a hint that the resource
 exists|
|**One INSERT of several competencies** (not exposed by the MVP API, possible through the Data API)|The statement lock is taken once. The row trigger counts the active competencies **including
the rows that the same statement already inserted**. The first row that would be the 51st active competency raises `23514` `competencies_active_limit`, and **the whole statement is rolled back:
 no row is inserted**. Exactly 50 active rows pass. A duplicate name inside the statement raises `23505` and also rolls back the whole statement.|The MVP API adds one competency per request; th
e limit is reported as `409 COMPETENCY_LIMIT_REACHED`|

● Update(docs\DATABASE-DESIGN.md)
Added 1 line, removed 1 line
|`skillloop_api`|The only role the backend logs in with (through the Supavisor session pooler)|None on its own|
|`authenticated`|Acting on behalf of a signed-in user; Row Level Security applies to it|The minimum granted per table, see 6.2|
|`anon`|Unauthenticated requests (relevant to the Data API)|None|
|`service_role`|Keeps Supabase's default privileges; the migrations do not change them|Not used by the backend, which refuses any `DATABASE_URL` that does not log in as `skillloop_api`|
|`service_role`|Keeps Supabase's default privileges; the migrations do not change them|Not used by the backend, which refuses any `DATABASE_URL` that does not log in as `skillloop_api`. Any wri
te by `postgres` or `service_role` to `competencies` must follow the mandatory contract of section 4.8 (rule L2)|

**The role `skillloop_api`:**


Teraz IMPLEMENTATION-PLAN.md: przebudowa T4 bez zależności od kolejności, doprecyzowanie T5, zdanie o niezależności testów od kolejności wybudzania oraz kontrakt L2 w 9.1a.

● Update(docs\IMPLEMENTATION-PLAN.md)
Added 2 lines, removed 2 lines
|T1|The suspected cycle "A locks competency R and waits for the goal; B holds the goal and inserts a name that collides with R": helper H (playing B) locks the goal row (`FOR NO KEY UPDATE`) an
d keeps the transaction open; A updates competency R and waits for the goal (proven); H then inserts a name that collides with R. Variant T1b: H inserts a different name.|H gets `23505` at once
 and does not wait for A; after H ends, A succeeds; no `40P01`. T1b: A waits, H commits, A succeeds.|
|T2|Two edits that would swap names, multi-statement so that the cycle is deterministic: T1 renames R1 `a`→`z` and holds; T2 renames R2 `b`→`a`; T1 renames R3 `c`→`b`.|No `40P01`. T2 waits for
the goal lock; T1's second statement gets `23505`; after T1 ends T2 also gets `23505`.|
|T3|Archiving a goal against competency writes, both orders, for INSERT, edit and archive of a competency: the goal archiving is held and the write waits, then the write is held and the archivi
ng waits.|The write that waited is refused with `competencies_goal_not_active` (proves that the row security check is evaluated again after the wait); the write that came first is kept; afterwa
rds no write is possible.|
|T4|A multi-row UPDATE against a single-row edit: H holds the goal lock; Z (the multi-row statement, rows in a known physical order) and X (a single-row edit of the second row) both wait; H com
mits.|No `40P01`; Z finishes first and X afterwards. The test checks the order of the waiting transactions and reports a setup failure if the lock queue is not first-in-first-out.|
|T5|The order goal → competencies used by deletion cascades: a privileged helper locks the goal and then deletes its competencies, while U edits a competency.|No `40P01`: U waits for the goal b
efore it touches any competency row.|
|T4|A multi-row UPDATE against a single-row edit of one of the same rows: helper H holds the goal lock; Z (one statement that updates several competencies, among them R2) and X (a single-row ed
it of R2) are both started while H holds the lock. Each is proven to be blocked by H (`pg_blocking_pids` contains H), which does not depend on which of them PostgreSQL wakes first. Then H commi
ts. Repeated many times, with both start orders.|No `40P01`. Both transactions end within the time limit (neither hangs). Each ends with success or with the error that its position in a serial
order gives. The final data equals the result of Z then X **or** of X then Z; the test accepts either. It does **not** assert which of them runs first.|
|T5|The order goal → competencies used by deletion cascades: a privileged helper locks the goal first (rule L2) and then deletes its competencies, while U edits one of them. U is started while
the helper holds the goal lock.|No `40P01`: U is blocked by the helper (proven) before it touches any competency row. After the helper commits, U changes zero rows, because the competency is go
ne.|
|T6|The pairs INSERT, edit, archive of a competency and archive of the goal, in both orders, plus a multi-row INSERT and a multi-row UPDATE as one side. Includes **the limit**: 49 active compet
encies, several concurrent INSERTs (with a `Barrier`, repeated), and the case "held INSERT that makes the 50th, then a second INSERT" for both commit and rollback; and "50 active, one is being
archived, an INSERT waits".|The second operation waits (proven) and its result equals the serial result; **never more than 50 active competencies**; exactly one INSERT wins at 49; after the arc
hiving commits the waiting INSERT succeeds; no `40P01`.|
|T7|A bounded stress run: many threads mix INSERT, edit, archive of a competency and archive of the goal for a few seconds.|No `40P01`; at most 50 active competencies; unique active names; no c
ompetency written after the archiving of its goal committed.|


● Update(docs\IMPLEMENTATION-PLAN.md)
Added 1 line, removed 1 line
|T6|The pairs INSERT, edit, archive of a competency and archive of the goal, in both orders, plus a multi-row INSERT and a multi-row UPDATE as one side. Includes **the limit**: 49 active compet
encies, several concurrent INSERTs (with a `Barrier`, repeated), and the case "held INSERT that makes the 50th, then a second INSERT" for both commit and rollback; and "50 active, one is being
archived, an INSERT waits".|The second operation waits (proven) and its result equals the serial result; **never more than 50 active competencies**; exactly one INSERT wins at 49; after the arc
hiving commits the waiting INSERT succeeds; no `40P01`.|
|T7|A bounded stress run: many threads mix INSERT, edit, archive of a competency and archive of the goal for a few seconds.|No `40P01`; at most 50 active competencies; unique active names; no c
ompetency written after the archiving of its goal committed.|

These tests cannot prove the absence of deadlocks in general; T2, T4 and T5 reproduce the cycles that are known, and T7 is a bounded search for others. If an assumption listed in `DATABASE-DESI
GN.md` section 4.8 fails, the work stops and the problem is reported before anything is changed.
These tests cannot prove the absence of deadlocks in general; T2 and T5 reproduce the cycles that are known, T4 checks a further interleaving without depending on lock order, and T7 is a bounde
d search for others. If an assumption listed in `DATABASE-DESIGN.md` section 4.8 fails, the work stops and the problem is reported before anything is changed.

**Stage 9.2 – list and add.** `GET /career-goals/{goal_id}/competencies` and `POST /career-goals/{goal_id}/competencies`; the model, schemas, repository, service and router; the new errors `COM
PETENCY_NAME_EXISTS` and `COMPETENCY_LIMIT_REACHED` (the service maps the database errors by constraint name, without a check-then-insert); the bounded retry of `40P01` and `40001` in the user
transaction and the answer `503 SERVICE_UNAVAILABLE` when it fails; unit tests with the fakes of `db_fakes.py`, API tests, integration tests, an end-to-end test and an update of the OpenAPI con
tract test; the placeholder routes in `test_allow_header.py` are replaced by the real ones. Tests include: no active goal, archived goal, another user's goal, the 50th and 51st competency, a du
plicate name, and the retry.


● Update(docs\IMPLEMENTATION-PLAN.md)
Added 1 line, removed 1 line

The existing tests `00`, `10` and `20` must pass unchanged. If `20_career_goals.test.sql` turns out to assert an exact list of constraints, the missing assertion goes into `30_competencies.test
.sql` instead of changing the old test without approval.

**Stage 9.1b – integration tests with several connections (required).** pgTAP runs in one session and cannot wait for a lock, so these tests are written in Python (`backend/tests/test_competenc
ies_db_concurrency.py`) on plain SQL, in the style of `test_services_concurrency.py`: a thread keeps a transaction open, every wait is proven with `pg_blocking_pids`, `threading.Barrier` starts
 operations together, and no `sleep()` decides an order. Operations run as `authenticated` through the real `user_transaction()`; fixtures and observation use the administration connection. Eve
ry test has a time limit; a deadlock (`psycopg.errors.DeadlockDetected`, `40P01`) is a failure. They need the local Supabase stack and the user's approval to run. C0 first asserts that the sess
ions use `read committed`.
**Stage 9.1b – integration tests with several connections (required).** pgTAP runs in one session and cannot wait for a lock, so these tests are written in Python (`backend/tests/test_competenc
ies_db_concurrency.py`) on plain SQL, in the style of `test_services_concurrency.py`: a thread keeps a transaction open, every wait is proven with `pg_blocking_pids`, `threading.Barrier` starts
 operations together, and no `sleep()` decides an order. Operations run as `authenticated` through the real `user_transaction()`; fixtures and observation use the administration connection. Eve
ry test has a time limit; a deadlock (`psycopg.errors.DeadlockDetected`, `40P01`) is a failure. They need the local Supabase stack and the user's approval to run. C0 first asserts that the sess
ions use `read committed`. **No test depends on the order in which PostgreSQL wakes waiting transactions**, because that order is not guaranteed. A wait is asserted only where one transaction h
as to wait for another that is known to hold the lock. Where several transactions wait, the tests assert only what holds in every order: no deadlock, every transaction ends within the time limi
t, and the final data equals the result of some serial order of the operations.

|Test|Scenario|Expected result|
|---|---|---|

● Update(docs\IMPLEMENTATION-PLAN.md)
Added 1 line, removed 1 line
|9.2|Backend: list and add competencies|Planned|
|9.3|Backend: edit and archive competencies|Planned|

**Stage 9.1a – migration and pgTAP tests.** One migration, created with `supabase migration new create_competencies`: `UNIQUE (id, user_id)` on `career_goals`; the table `competencies` with its
 constraints and indexes; Row Level Security, grants and policies; `competencies_set_updated_at`; the statement trigger `competencies_lock_goal` and the row trigger `competencies_enforce_goal_r
ules` with their functions (design V3, `DATABASE-DESIGN.md` section 4.8). Applying it and running the tests is local only (`supabase db reset`, `supabase test db`) and needs the user's approval
 at that moment. The test file is `supabase/tests/database/30_competencies.test.sql`. All data operations run as `authenticated` (`SET LOCAL ROLE` plus `request.jwt.claims`), not only as `postg
res`. The tests are:
**Stage 9.1a – migration and pgTAP tests.** One migration, created with `supabase migration new create_competencies`: `UNIQUE (id, user_id)` on `career_goals`; the table `competencies` with its
 constraints and indexes; Row Level Security, grants and policies; `competencies_set_updated_at`; the statement trigger `competencies_lock_goal` and the row trigger `competencies_enforce_goal_r
ules` with their functions (design V3, `DATABASE-DESIGN.md` section 4.8). The header comment of the migration states the mandatory contract for privileged writes (rule L2): every write to `comp
etencies` by `postgres` or `service_role` first locks the goal. Applying it and running the tests is local only (`supabase db reset`, `supabase test db`) and needs the user's approval at that m
oment. The test file is `supabase/tests/database/30_competencies.test.sql`. All data operations run as `authenticated` (`SET LOCAL ROLE` plus `request.jwt.claims`), not only as `postgres`. The
tests are:

- Structure: exact columns (no `current_level`, `source`, `expectations`), primary key, defaults, the composite foreign key with `ON DELETE CASCADE`, `UNIQUE (id, user_id)` on `career_goals`, t
he indexes, Row Level Security enabled, the triggers and their kind (statement or row).
- Functions: `volatile`, `SECURITY INVOKER`, pinned `search_path`, `EXECUTE` not available to `anon`, `authenticated` or `skillloop_api`.

  Searched for 1 pattern

● Update(docs\IMPLEMENTATION-PLAN.md)
Added 1 line
- **Multi-row INSERT:** one statement inserting 50 rows into an empty goal passes (this also proves that the row trigger sees the rows of the same statement); one statement that would create th
e 51st active competency fails with `competencies_active_limit` and **no row of that statement remains**; the same statement against a goal that already holds 49 fails as a whole; a statement t
hat contains a duplicate name fails as a whole with `23505`.
- **Multi-row UPDATE:** one statement renaming several rows is atomic (if one row conflicts, no row changes); rows hidden by Row Level Security (another user's, archived, in an archived goal) a
re not changed and raise no error; archiving several rows in one statement frees that many slots; swapping two names in one statement fails with `23505`; a multi-row UPDATE cannot change `caree
r_goal_id` or `user_id` (no grant).
- Statement trigger: fires also for a statement that changes zero rows; does nothing for a user without an active goal.
- Privileged role: as `postgres`, a write that follows the contract of rule L2 (it locks the goal first) works; a write that does not follow it is still refused for an archived goal and for the
 51st active competency (the row trigger), so a broken contract cannot corrupt data.
- `updated_at` changes on UPDATE and `created_at` does not; deleting the user in `auth.users` removes the goals and the competencies (a statement of the current technical state, not of an appro
ved policy).

The existing tests `00`, `10` and `20` must pass unchanged. If `20_career_goals.test.sql` turns out to assert an exact list of constraints, the missing assertion goes into `30_competencies.test
.sql` instead of changing the old test without approval.

● Update(docs\API-DESIGN.md)
Added 3 lines, removed 1 line
- **Ownership.** A competency or goal of another user is indistinguishable from a missing one (`404`). The user id is never taken from the request.
- **No active goal.** Without an active goal no competency can be added, edited or archived. Reading the competencies of the user's own archived goal still works.
- **Frozen competencies.** When a goal is archived, its competencies can no longer be added, edited or archived. They stay readable.
- **Error precedence for a competency that cannot be changed.** A competency that is itself archived answers `409 COMPETENCY_ARCHIVED`; otherwise a competency of an archived goal answers `409 G
OAL_ARCHIVED`; a competency that does not exist or is not the user's answers `404 COMPETENCY_NOT_FOUND`.
- **Error precedence.** The order follows what the backend is able to recognise, given Row Level Security (the owner can read their own competencies and goals in every status, and cannot see an
yone else's). Request validation (`422`) always comes first.
  - **Edit or archive a competency.** The backend finds out why nothing was changed by reading, in this order: (1) the competency is not visible (missing or another user's) → `404 COMPETENCY_NO
T_FOUND`; (2) it is itself archived → `409 COMPETENCY_ARCHIVED`; (3) its goal is archived → `409 GOAL_ARCHIVED`. A competency that is archived and sits in an archived goal therefore answers `CO
MPETENCY_ARCHIVED`: the resource that was addressed is reported before its parent. A rename that collides with another name is reported (`409 COMPETENCY_NAME_EXISTS`) only for a competency that
 can be changed, so it never hides the answers above.
  - **Add a competency.** (1) The goal is not visible (missing, another user's) → `404 GOAL_NOT_FOUND`; (2) the goal is archived → `409 GOAL_ARCHIVED`; (3) the goal already has 50 active compet
encies → `409 COMPETENCY_LIMIT_REACHED`; (4) the name is taken → `409 COMPETENCY_NAME_EXISTS`. The database evaluates the goal and the limit in a trigger before it checks the unique index, so a
 request that would break both rules answers `COMPETENCY_LIMIT_REACHED`. The database cannot tell a missing goal from an archived one in its own error, so the backend reads the goal after a fai
lure to choose between (1) and (2).
- **One resource per request.** The API adds, changes or archives one competency per request. Statements that touch several rows are not part of the API. They remain possible through the Data A
PI, and the database handles them atomically (`DATABASE-DESIGN.md`, section 4.3.1): the whole statement succeeds or nothing changes.
- **Concurrency.** Writes to the competencies of one goal are serialised in the database (`DATABASE-DESIGN.md`, section 4.8), so concurrent requests cannot exceed the limit or write into a goal
 that has just been archived. A deadlock (`40P01`) or a serialization failure (`40001`) is treated as **retryable**: the backend repeats the whole transaction a small, bounded number of times a
nd answers `503 SERVICE_UNAVAILABLE` if it still fails. A statement that waits longer than the statement timeout answers `504 DATABASE_TIMEOUT`.


Wszystkie edycje zastosowane. Teraz kontrola spójności między czterema dokumentami: kody błędów, liczby, nazwy testów i obiektów, statusy oraz odwołania do sekcji.

  Ran 2 shell commands

Kontrola znalazła jedną niespójność: stopka IMPLEMENTATION-PLAN.md nadal mówi "Draft v1.0", a nagłówek ma wersję 1.1. Poprawiam.

● Update(docs\IMPLEMENTATION-PLAN.md)
Added 1 line, removed 1 line

---

**Document status:** Draft v1.0
**Document status:** Draft v1.1

**Next step:** Review the implementation plan and prepare the project repository for Phase 0.
 No newline at end of file

Stage 9.0: druga runda kontroli zakończona

Zmieniłem tylko cztery pliki w docs/. Nie tworzyłem migracji, nie ruszałem schematu, nie uruchamiałem Supabase ani testów z bazą, nie czytałem .env i nie robiłem commitów ani pushów. Nie zaczynam Stage 9.1.

Zgłoszenie przyszło dwa razy z tą samą treścią, więc wykonałem je raz.

1. T4 bez zależności od FIFO

Test nie wymaga już żadnej kolejności.
- Nowy scenariusz: pomocnik H trzyma blokadę celu. Z (instrukcja wielowierszowa) i X (edycja jednego wiersza) są startowane, gdy H ją trzyma. Test dowodzi tylko, że każde z nich jest zablokowane przez H (pg_blocking_pids). Potem H commituje. Test powtarzam wiele razy, w obu kolejnościach startu.
- Asercje: brak 40P01, obie transakcje kończą się w limicie czasu, a wynik końcowy jest równy wynikowi Z→X lub X→Z. Test nie sprawdza, które wykonało się pierwsze.
- Usunięte: założenie FIFO (punkt 6 w 4.8) i wymóg "znanej kolejności fizycznej" wierszy.
- Zasada ogólna: dopisałem ją do planu testów i do 4.8: żaden test nie zależy od kolejności wybudzania czekających transakcji, a oczekiwanie jest asercją tylko tam, gdzie jedna transakcja na pewno czeka na drugą, o której wiadomo, że trzyma blokadę.
- Skutek uboczny: T4 przestał deterministycznie odtwarzać cykl z V1. Cykle znane z analizy odtwarzają T2 i T5, które od kolejności nie zależą. Dopisałem to w opisie testów, a T5 doprecyzowałem (U jest blokowany przez pomocnika, potem zmienia zero wierszy).

2. Rola uprzywilejowana: obowiązkowy kontrakt

Reguła L2 w 4.8 jest teraz opisana jako obowiązkowy kontrakt architektury.
- Zakres: każdy zapis do competencies przez postgres lub service_role, teraz i w przyszłości: INSERT, UPDATE i DELETE. Dotyczy to migracji danych, backfilli, skryptów seed i naprawczych, narzędzi administracyjnych oraz przyszłych zadań w tle.
- Wymaganie: najpierw SELECT 1 FROM public.career_goals WHERE id = … FOR NO KEY UPDATE w tej samej transakcji. Przy wielu celach blokuje się wszystkie w kolejności rosnącego id.
- Czego baza nie wymusza: złamanie kontraktu nie psuje danych, bo trigger wierszowy nadal pilnuje statusu celu i limitu 50. Traci się tylko odporność na deadlocki.
- Egzekwowanie: nagłówek każdej migracji lub skryptu zapisującego do competencies ma deklarować zgodność z kontraktem, a dodanie takiego pisarza wymaga przeglądu względem tej reguły.
- Odwołania: kontrakt jest przywołany w 6.1 (wiersz service_role) i 4.3.1. W planie 9.1a dodałem jego komentarz w nagłówku migracji oraz test pgTAP, że zapis zgodny z kontraktem działa, a zapis niezgodny nadal jest odrzucany dla zarchiwizowanego celu i dla 51. kompetencji.

3. Kolejność błędów

Zweryfikowałem ją względem RLS i tego, co backend faktycznie może rozpoznać. Wynik: kolejność merytorycznie bez zmian. Poprawiłem tylko jej opis.
- PATCH i archive: polityka UPDATE ukrywa kompetencje zarchiwizowane i kompetencje zarchiwizowanego celu, więc UPDATE zwraca zero wierszy. Właściciel może jednak czytać swoje kompetencje i cele w każdym statusie (polityki SELECT). Backend rozpoznaje więc stan odczytem:
  a. niewidoczna → 404 COMPETENCY_NOT_FOUND (cudza i nieistniejąca są nieodróżnialne),
  b. sama zarchiwizowana → 409 COMPETENCY_ARCHIVED,
  c. cel zarchiwizowany → 409 GOAL_ARCHIVED.

  Kompetencja zarchiwizowana w zarchiwizowanym celu zwraca COMPETENCY_ARCHIVED: zasób, do którego się odwołano, jest raportowany przed rodzicem. Poprzednio 404 stał w opisie na końcu, mimo że logicznie jest rozstrzygany pierwszy. Dlatego przepisałem go jako kolejność ustalania.
- POST: 422 → cel niewidoczny (404) → cel zarchiwizowany (409 GOAL_ARCHIVED) → limit → nazwa zajęta. Kolejność limitu przed nazwą wynika z tego, że trigger wierszowy działa przed kontrolą indeksu unikalnego (zweryfikowane w kodzie PostgreSQL 17). Żądanie łamiące obie reguły dostaje więc COMPETENCY_LIMIT_REACHED. Dopisałem to jawnie.
- Wyścig: trigger nie odróżnia celu zarchiwizowanego od nieistniejącego (blokada go nie widzi). Dlatego po błędzie competencies_goal_not_active backend czyta cel w nowej transakcji. Dla edycji i archiwizacji kompetencji wiersz był widoczny, więc cel istnieje i jest użytkownika, a przyczyną jest archiwizacja celu → 409 GOAL_ARCHIVED.
- Konflikt nazwy przy zmianie: nie ukrywa wcześniejszych odpowiedzi, bo zarchiwizowana kompetencja jest odfiltrowana, zanim dojdzie do kontroli unikalności.

4. Spójność czterech dokumentów

Sprawdziłem skryptowo:
- Kody błędów: ten sam zbiór czterech nowych kodów (COMPETENCY_NOT_FOUND, _ARCHIVED, _NAME_EXISTS, _LIMIT_REACHED) w DATABASE-DESIGN, API-DESIGN i IMPLEMENTATION-PLAN. MVP-SCOPE kodów nie zawiera.
- Limity i liczby: 50 aktywnych, 100/1000/1000/60 znaków, wartości enumów i domyślne display_order są zgodne w DATABASE-DESIGN, API-DESIGN i planie testów (granice 100/101, 1000/1001, 60/61).
- Nazwy obiektów: triggery, funkcje, indeksy i ograniczenia mają te same nazwy w opisach i testach. Odwołania do T1, T3, T4 i T5 w 4.8 odpowiadają tabeli testów.
- Statusy: wszystkie sekcje kompetencji pozostają Planned, nigdzie nie ma "Implemented".
- Wersje: nagłówki i stopki są zgodne po jednej poprawce. Stopka IMPLEMENTATION-PLAN.md nadal mówiła Draft v1.0 przy wersji 1.1 w nagłówku, więc ją poprawiłem.
- Wyszukanie pozostałości: nie ma już żadnych wzmianek o FIFO, o "known physical order" ani o "setup failure".
- Format: zakończenia linii pozostały LF. Ostrzeżenie gita o LF→CRLF dotyczy jego konfiguracji autocrlf.

Nie renderowałem dokumentów, więc sprawdzenie dotyczy treści i odwołań. Fakty o PostgreSQL pochodzą z dokumentacji i kodu źródłowego, a nie z uruchomienia. Wszystkie założenia o zachowaniu bazy nadal czekają na testy 9.1.

Zmienione pliki: docs/DATABASE-DESIGN.md, docs/API-DESIGN.md, docs/IMPLEMENTATION-PLAN.md (docs/MVP-SCOPE.md nie wymagał zmian w tej rundzie). W katalogu głównym nadal leży nieśledzony folder Obsidian/, którego nie tworzyłem i nie ruszam.

Proponowany komunikat commita (commit robisz Ty): docs: design competencies model and write synchronisation for Stage 9

Zatrzymuję się i czekam na Twoją akceptację przed Stage 9.1.