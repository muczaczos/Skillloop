Ostatnia drobna korekta Stage 9.0:

Sprawdź test T5. Obecny scenariusz używa pomocniczej transakcji, która najpierw blokuje career_goal, a następnie ręcznie usuwa competencies.

Chciałbym, aby test dodatkowo sprawdzał rzeczywistą kaskadę ON DELETE CASCADE:
- transakcja H wykonuje rzeczywiste DELETE FROM public.career_goals WHERE id = ...;
- PostgreSQL sam usuwa powiązane competencies;
- równolegle transakcja U próbuje edytować jedną z tych kompetencji;
- test sprawdza blokowanie, brak deadlocka i końcowy stan danych.

Nie zmieniaj schematu ani migracji. Zmień tylko dokumentację i zatrzymaj się po raporcie. Nie rozpoczynaj Stage 9.1.



 Searched for 1 pattern

● Update(docs\IMPLEMENTATION-PLAN.md)
Added 2 lines, removed 1 line
|T2|Two edits that would swap names, multi-statement so that the cycle is deterministic: T1 renames R1 `a`→`z` and holds; T2 renames R2 `b`→`a`; T1 renames R3 `c`→`b`.|No `40P01`. T2 waits for
the goal lock; T1's second statement gets `23505`; after T1 ends T2 also gets `23505`.|
|T3|Archiving a goal against competency writes, both orders, for INSERT, edit and archive of a competency: the goal archiving is held and the write waits, then the write is held and the archivi
ng waits.|The write that waited is refused with `competencies_goal_not_active` (proves that the row security check is evaluated again after the wait); the write that came first is kept; afterwa
rds no write is possible.|
|T4|A multi-row UPDATE against a single-row edit of one of the same rows: helper H holds the goal lock; Z (one statement that updates several competencies, among them R2) and X (a single-row ed
it of R2) are both started while H holds the lock. Each is proven to be blocked by H (`pg_blocking_pids` contains H), which does not depend on which of them PostgreSQL wakes first. Then H commi
ts. Repeated many times, with both start orders.|No `40P01`. Both transactions end within the time limit (neither hangs). Each ends with success or with the error that its position in a serial
order gives. The final data equals the result of Z then X **or** of X then Z; the test accepts either. It does **not** assert which of them runs first.|
|T5|The order goal → competencies used by deletion cascades: a privileged helper locks the goal first (rule L2) and then deletes its competencies, while U edits one of them. U is started while
the helper holds the goal lock.|No `40P01`: U is blocked by the helper (proven) before it touches any competency row. After the helper commits, U changes zero rows, because the competency is go
ne.|
|T5a|The order goal → competencies of a deletion that follows rule L2, with an explicit deletion of the competencies: a privileged helper locks the goal first and then runs `DELETE FROM public.
competencies` for the goal, while U edits one of them. U is started while the helper holds the goal lock.|No `40P01`: U is blocked by the helper (proven) before it touches any competency row. A
fter the helper commits, U changes zero rows, because the competency is gone.|
|T5b|**The real cascade.** H, a privileged role, runs a real `DELETE FROM public.career_goals WHERE id = <goal>` and **no explicit DELETE on `competencies`**: PostgreSQL itself removes the comp
etencies through `ON DELETE CASCADE`. U, as `authenticated`, tries to edit one of those competencies. At most one transaction waits in each part, so no part depends on the order in which Postgr
eSQL wakes waiting transactions. **(1) H first:** H's DELETE statement completes (the cascade has run) and H keeps the transaction open; then U starts and is proven to be blocked by H; then H c
ommits. **(2) U first:** U's edit completes and U keeps the transaction open; then H's DELETE is started in a thread and is proven to be blocked by U; then U commits. **(3) Simultaneous:** U an
d H are started together with a `Barrier`, repeated many times with fresh data. This is a best effort to hit the interval in which the goal row is already deleted but the cascade has not yet ru
n; the test does not require that interval to be hit.|No `40P01` in any part, and every transaction ends within the time limit. **(1)** After H commits U changes zero rows (the competency is go
ne). **(2)** U's edit succeeds (one row); after U commits, H's DELETE ends and its cascade also removes the competency that U edited. **(3)** U changes one row or zero rows, depending on the or
der that happened; both are accepted. In every part the final state is the same, checked with the administration connection: the goal does not exist, none of its competencies exists, and they d
isappeared only through the cascade (no explicit DELETE on `competencies` was issued). The deletion does not fire the competency triggers, which exist for INSERT and UPDATE only.|
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

These tests cannot prove the absence of deadlocks in general; T2 and T5 reproduce the cycles that are known, T4 checks a further interleaving without depending on lock order, and T7 is a bounde
d search for others. If an assumption listed in `DATABASE-DESIGN.md` section 4.8 fails, the work stops and the problem is reported before anything is changed.
These tests cannot prove the absence of deadlocks in general; T2, T5a and T5b reproduce the cycles that are known (T5b with the real cascade), T4 checks a further interleaving without depending
 on lock order, and T7 is a bounded search for others. If an assumption listed in `DATABASE-DESIGN.md` section 4.8 fails, the work stops and the problem is reported before anything is changed.

**Stage 9.2 – list and add.** `GET /career-goals/{goal_id}/competencies` and `POST /career-goals/{goal_id}/competencies`; the model, schemas, repository, service and router; the new errors `COM
PETENCY_NAME_EXISTS` and `COMPETENCY_LIMIT_REACHED` (the service maps the database errors by constraint name, without a check-then-insert); the bounded retry of `40P01` and `40001` in the user
transaction and the answer `503 SERVICE_UNAVAILABLE` when it fails; unit tests with the fakes of `db_fakes.py`, API tests, integration tests, an end-to-end test and an update of the OpenAPI con
tract test; the placeholder routes in `test_allow_header.py` are replaced by the real ones. Tests include: no active goal, archived goal, another user's goal, the 50th and 51st competency, a du
plicate name, and the retry.


● Update(docs\DATABASE-DESIGN.md)
Added 1 line, removed 1 line
- **Freezing.** Archiving or editing a goal needs the same row lock, so it is serialised with every competency write of that goal. A statement that waited for the lock checks the status again a
fter it is granted (a locking read in READ COMMITTED returns the latest version of the row, and the row is skipped if it no longer matches). A write that got the lock first commits before the a
rchiving proceeds; a write that comes later is refused.
- A statement that waited for the lock still uses the snapshot it took at its start for its own scan and for the `WITH CHECK` of the policies. The row trigger is therefore the authoritative che
ck; the policies stay as a second barrier.

**Why this is free of lock cycles.** All locks on competencies of one goal are taken in the order *goal row first, then competency rows*, and the first lock is exclusive among writers. The writ
er that holds the goal lock never waits for something held by a writer that waits for the goal lock, because the waiting writers have not locked or changed any competency yet. Unique-index chec
ks only wait for a transaction that has really changed a row (a lock-only tuple lock does not make a unique check wait), and only the goal-lock holder changes rows. The cycles that this design
removes are reproduced by tests of `IMPLEMENTATION-PLAN.md`: two edits swapping names under a shared lock (T2), and the order goal → competencies used by cascading deletion (T5). T4 covers a mu
lti-row UPDATE against a single-row edit. Which of two waiting transactions PostgreSQL wakes first is not guaranteed, so T4 does not depend on it and asserts only outcomes that hold in every or
der.
**Why this is free of lock cycles.** All locks on competencies of one goal are taken in the order *goal row first, then competency rows*, and the first lock is exclusive among writers. The writ
er that holds the goal lock never waits for something held by a writer that waits for the goal lock, because the waiting writers have not locked or changed any competency yet. Unique-index chec
ks only wait for a transaction that has really changed a row (a lock-only tuple lock does not make a unique check wait), and only the goal-lock holder changes rows. The cycles that this design
removes are reproduced by tests of `IMPLEMENTATION-PLAN.md`: two edits swapping names under a shared lock (T2), and the order goal → competencies used by deletion (T5a, an explicit deletion tha
t follows rule L2, and T5b, the real `ON DELETE CASCADE`). T4 covers a multi-row UPDATE against a single-row edit. Which of two waiting transactions PostgreSQL wakes first is not guaranteed, so
 T4 does not depend on it and asserts only outcomes that hold in every order.

**Invariants (approved):**


● Update(docs\DATABASE-DESIGN.md)
Added 2 lines

**Consequences.** All writes to the competencies of one goal are serialised. The MVP has one active goal per user, so this only affects the same user's own concurrent requests (double clicks, s
everal tabs). Even a statement that changes zero rows takes the goal lock. The lock writes a mark on the goal row (WAL), which is negligible at this scale. A waiting statement is bounded by the
 `statement_timeout` of 15 s of `skillloop_api` (SQLSTATE `57014`, reported as `504 DATABASE_TIMEOUT`). Reads are never blocked. Concurrent `PATCH` or archiving of the goal waits for a running
competency write and vice versa.

**Deletion.** The triggers of this section exist for INSERT and UPDATE only. Clients have no DELETE grant, so competencies disappear only through the cascade `auth.users` → `career_goals` → `co
mpetencies` (section 8; not an approved deletion policy) or through a privileged role, which is bound by rule L2. In the cascade the goal row is deleted first and the competencies afterwards, a
s an action of the same statement, which is the order *goal, then competencies* that this design requires. A concurrent edit by `authenticated` waits for the goal lock before it touches any com
petency row, so it cannot hold a competency row that the cascade needs. A real cascade is tested in T5b.

**Errors and how the backend handles them (implemented in Stages 9.2–9.3):**

|SQLSTATE and constraint|Meaning|Backend result|

● Update(docs\DATABASE-DESIGN.md)
Added 2 lines, removed 1 line
4. The row count query in the row trigger sees the rows that the same multi-row INSERT has already inserted.
5. A tuple lock taken before the trigger does not make another transaction's unique-index check wait (test T1).
6. No test depends on the order in which PostgreSQL grants a lock to waiting transactions: that order is not guaranteed. Where several transactions wait, the tests assert only what holds in eve
ry order: no deadlock, every transaction ends within the time limit, and the final data equals the result of some serial order of the operations. A wait is asserted (with `pg_blocking_pids`) on
ly where one transaction deterministically has to wait for another that is known to hold the lock.
7. `deadlock_timeout` detection and the `statement_timeout` of 15 s bound every test.
7. A real `DELETE` of a goal removes its competencies through `ON DELETE CASCADE` after the goal row has been deleted and in the same statement, and a concurrent edit by `authenticated` is bloc
ked at the statement trigger until the deleting transaction ends, without a deadlock (test T5b).
8. `deadlock_timeout` detection and the `statement_timeout` of 15 s bound every test.

If an assumption does not hold, the work stops and the problem is reported. The fallbacks are a variant without the `UPDATE` guard, or `SECURITY DEFINER`; the latter only with a separate approv
al.


  Ran 1 shell command

Korekta T5: rzeczywista kaskada ON DELETE CASCADE

Zmieniłem tylko dokumentację: docs/IMPLEMENTATION-PLAN.md i docs/DATABASE-DESIGN.md. Nie tworzyłem migracji, nie ruszałem schematu, nie uruchamiałem Supabase i nie czytałem .env. Nie robiłem commitów ani pushów i nie zaczynam Stage 9.1.

Zmiany

IMPLEMENTATION-PLAN.md, test T5 rozdzielony na dwa:
- T5a to dotychczasowy scenariusz: pomocnik najpierw blokuje cel zgodnie z regułą L2, a potem ręcznie usuwa kompetencje. Ten test sprawdza kontrakt dla ról uprzywilejowanych.
- T5b to rzeczywista kaskada. H wykonuje DELETE FROM public.career_goals WHERE id = <cel> i nie wykonuje żadnego jawnego DELETE na competencies. Kompetencje usuwa sam PostgreSQL przez ON DELETE CASCADE. U (rola authenticated) próbuje w tym czasie edytować jedną z nich. T5b ma trzy części:

┌────────────────┬──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬───────────────────────────────────────────────────────────┐
│     Część      │                                                           Przebieg                                                           │                     Oczekiwany wynik                      │
├────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────┤
│ (1) H pierwszy │ Instrukcja DELETE kończy się (kaskada już zadziałała), H trzyma transakcję otwartą. Potem startuje U i jest udowodnione, że  │ U zmienia 0 wierszy                                       │
│                │ blokuje go H. Na końcu H commituje.                                                                                          │                                                           │
├────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────┤
│ (2) U pierwszy │ Edycja U kończy się i U trzyma transakcję. Potem startuje DELETE H w wątku i jest udowodnione, że blokuje go U. Na końcu U   │ Edycja U się udaje (1 wiersz). DELETE H kończy się, a     │
│                │ commituje.                                                                                                                   │ kaskada usuwa też edytowaną kompetencję                   │
├────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────┤
│ (3)            │ U i H startują razem przez Barrier, wiele powtórzeń ze świeżymi danymi. To próba trafienia w okno, w którym wiersz celu jest │ U zmienia 1 lub 0 wierszy, obie wartości akceptowane      │
│ jednocześnie   │  już usunięty, a kaskada jeszcze nie zadziałała. Test nie wymaga trafienia w to okno.                                        │                                                           │
└────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┴───────────────────────────────────────────────────────────┘

We wszystkich trzech częściach obowiązują te same asercje:
- brak 40P01,
- każda transakcja kończy się w limicie czasu,
- stan końcowy (sprawdzany z połączenia administracyjnego): celu nie ma, żadnej jego kompetencji nie ma, a zniknęły one wyłącznie przez kaskadę,
- usunięcie nie uruchamia triggerów kompetencji, bo istnieją tylko dla INSERT i UPDATE.

W częściach (1) i (2) zawsze czeka co najwyżej jedna transakcja, więc test nie zależy od kolejności wybudzania. Ta kolejność nie jest gwarantowana i zgodnie z poprzednią korektą nie wolno na niej opierać testów. W części (3) przyjmuję oba wyniki. Zdanie o testach znanych cykli wymienia teraz T2, T5a i T5b.

DATABASE-DESIGN.md (sekcja 4.8):
- Nowy akapit "Deletion": triggery działają tylko dla INSERT i UPDATE, klient nie ma DELETE, a kompetencje znikają przez kaskadę auth.users → career_goals → competencies albo przez rolę uprzywilejowaną związaną regułą L2. W kaskadzie wiersz celu jest usuwany pierwszy, a kompetencje potem, w tej samej instrukcji. To jest kolejność "cel, potem kompetencje", której wymaga projekt. Równoległa edycja z authenticated czeka na blokadę celu, zanim dotknie jakiegokolwiek wiersza kompetencji, więc nie może zająć wiersza potrzebnego kaskadzie.
- Nowe założenie nr 7 do udowodnienia testem T5b. Dotychczasowe nr 7 (deadlock_timeout) stało się nr 8.
- Odwołanie do T5 w uzasadnieniu braku cykli wskazuje teraz T5a i T5b.

Czego dokumentacja nie rozstrzyga

Opieram się na tym, jak PostgreSQL wykonuje kaskadę: usuwa wiersz rodzica, a następnie wykonuje usunięcie wierszy dzieci jako akcję tej samej instrukcji. To jest założenie nr 7, a nie ustalony fakt. Z kodu PostgreSQL czytałem wcześniej tylko kontrolę FK (ri_triggers.c), a funkcji kaskady nie. T5b ma to potwierdzić. Jeśli tak nie jest, zatrzymuję się i raportuję.

Test używa DELETE FROM public.career_goals zgodnie z Twoim opisem. Prawdziwa ścieżka produkcyjna (usunięcie użytkownika z auth.users) przechodzi tym samym mechanizmem, ale dodatkowym krokiem. Nie dodawałem jej do testu, bo o to nie prosiłeś. Mogę ją dopisać jako wariant, jeśli chcesz.

W katalogu głównym nadal leży nieśledzony folder Obsidian/, którego nie tworzyłem i nie ruszam.

Proponowany komunikat commita (commit robisz Ty): docs: design competencies model and write synchronisation for Stage 9

Zatrzymuję się i czekam na Twoją akceptację przed Stage 9.1.