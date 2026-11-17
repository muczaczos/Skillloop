Zgadzam się na dodanie dwóch deterministycznych testów współbieżności wykrywających luki M15 i M16.

Zakres:

1. M15 – test sprawdzający, że druga archiwizacja czeka na zakończenie pierwszej transakcji, a następnie otrzymuje GoalArchivedError.
2. M16 – test sprawdzający, że druga próba utworzenia celu czeka na zakończenie pierwszej transakcji, a następnie otrzymuje ActiveGoalExistsError, a nie surowy UniqueViolation.

Zasady pracy:

- Najpierw przeanalizuj istniejące testy i zaproponuj dokładny plan zmian.
- Nie zmieniaj kodu produkcyjnego.
- Nie zmieniaj timeoutów ani istniejących testów bez uzasadnienia.
- Testy mają deterministycznie kontrolować kolejność transakcji, bez polegania na sleep.
- Po mojej akceptacji implementuj oba testy.
- Uruchom nowe testy wielokrotnie, następnie cały zestaw testów z lokalną bazą.
- Sprawdź Ruff, git diff i stan bazy.
- Nie dotykaj katalogu services/, nie wykonuj commitów ani pushy.
- Jeśli okaże się, że test wymaga zmian w kodzie produkcyjnym, zatrzymaj się i przedstaw uzasadnienie.

Na razie tylko analiza i plan. Poczekaj na moją akceptację przed zmianami.

Czytam istniejącą infrastrukturę testów współbieżności (tylko odczyt), żeby plan opierał się na tych samych pomocnikach.

Ran 2 shell commands

Plan: dwa deterministyczne testy współbieżności (luki M15 i M16)

Nic nie zmieniłem ani nie uruchamiałem. Czytałem tests/test_services_concurrency.py (pomocniki i testy 1–6).

1. Dlaczego obecne testy 1 i 3 nie wykrywają M15/M16

Oba używają run_in_parallel z Barrier, który tylko startuje wątki razem i nie wymusza kolejności instrukcji. Zmutowany kod (sprawdzenie przed zapisem) różni się od poprawnego wyłącznie przy rzadkim przeplocie, więc 0/25 wykryć to wynik oczekiwany. Test 2 (update kontra otwarta archiwizacja) działa inaczej i dlatego wykrywa M14 pewnie.

2. Mechanizm, którego użyję (już istniejący, bez zmian)

- held_transaction(pool, user, action): prawdziwa user_transaction w osobnym wątku. Wykonuje action, trzyma transakcję otwartą (blokady zachowane) aż do release(), potem robi COMMIT.
- start_waiter(call): uruchamia wywołanie serwisu w osobnym wątku.
- wait_until_blocked_by(admin, holder_pid): odpytuje pg_stat_activity + pg_blocking_pids jako postgres, aż któryś backend czeka na blokadzie trzymanej przez holder_pid. Pauza 20 ms jest tylko odstępem między zapytaniami, a limit TIMEOUT zamienia brak blokady w błąd testu.

Kolejność jest więc wymuszona blokadą w bazie, a nie czasem: druga operacja wystartuje dopiero po zatwierdzeniu transakcji trzymającej blokadę, ale dopiero po potwierdzeniu przez bazę, że jest zablokowana.

3. Test M15: test_3b_archive_waits_for_open_archive_then_goal_archived

Umieszczony po teście 3.

def test_3b_archive_waits_for_open_archive_then_goal_archived(
pool4: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
user = users[0]
goal = new_goal(pool4, user)

    with held_transaction(
        pool4, user, lambda conn: career_goals.archive_active(conn, user.id, goal.id)
    ) as held:
        executor, future = start_waiter(
            lambda: career_goal_service.archive_goal(pool4, user, goal.id)
        )
        with executor:
            wait_until_blocked_by(admin, held.pid)
            assert not future.done()
            held.release()
            with pytest.raises(GoalArchivedError):
                future.result(timeout=TIMEOUT)

    assert admin_rows(admin, user) == [("Backend Engineer", "mid", "API", "archived")]
    assert admin_active_count(admin, user) == 0

Przebieg przy poprawnym kodzie: druga archive_active blokuje się na wierszu, po COMMIT widzi status = 'archived', zwraca None, serwis klasyfikuje to jako GoalArchivedError.
Przy M15 (kontrola przed UPDATE): wstępny get_by_id widzi jeszcze aktywny cel, archive_active blokuje się (więc oczekiwanie na blokadę jest spełnione), a po COMMIT zwraca None, które serwis oddaje bez wyjątku. pytest.raises kończy się niepowodzeniem. Wykrycie jest deterministyczne.

4. Test M16: test_1b_create_waits_for_open_create_then_active_goal_exists

Umieszczony po teście 1.

def test_1b_create_waits_for_open_create_then_active_goal_exists(
pool4: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
user = users[0]

    with held_transaction(
        pool4,
        user,
        lambda conn: career_goals.insert(
            conn, user.id, target_role="Backend Engineer", seniority="mid", description="API"
        ),
    ) as held:
        executor, future = start_waiter(
            lambda: career_goal_service.create_goal(pool4, user, CREATE)
        )
        with executor:
            wait_until_blocked_by(admin, held.pid)
            assert not future.done()
            held.release()
            with pytest.raises(ActiveGoalExistsError) as exc_info:
                future.result(timeout=TIMEOUT)

    # The loser was stopped by the database constraint, not by a check made beforehand.
    cause = exc_info.value.__cause__
    assert isinstance(cause, psycopg.errors.UniqueViolation)
    assert cause.diag.constraint_name == "one_active_goal_per_user"
    assert admin_active_count(admin, user) == 1

Przebieg przy poprawnym kodzie: drugi INSERT czeka na niezatwierdzony wiersz z indeksu unikalnego, po COMMIT dostaje 23505, a serwis tłumaczy to na ActiveGoalExistsError z przyczyną UniqueViolation (nazwa ograniczenia one_active_goal_per_user).
Przy M16: wstępne get_active nie widzi niezatwierdzonego wiersza, INSERT blokuje się, a po COMMIT wychodzi surowy UniqueViolation bez tłumaczenia, więc pytest.raises(ActiveGoalExistsError) pada. Wykrycie też jest deterministyczne.

Asercja o **cause** dodatkowo dowodzi, że to baza (indeks) powstrzymała drugi zapis. Jest ona zgodna z kodem serwisu (raise ActiveGoalExistsError from exc).

5. Zakres zmian

- Jeden plik: backend/tests/test_services_concurrency.py, wyłącznie dopisanie dwóch funkcji i dwóch nagłówków sekcji (# --- 1b. …, # --- 3b. …).
- Bez nowych importów: psycopg, pytest, ActiveGoalExistsError, GoalArchivedError i career_goals są już zaimportowane, a psycopg.errors jest dostępne przez import psycopg.
- Istniejące testy 1–6, pomocniki i TIMEOUT zostają bez żadnej zmiany. Testy 1 i 3 pozostają jako sprawdzenie wyników, które muszą być prawdziwe w każdym przeplocie.
- Kod produkcyjny bez zmian. Nie przewiduję takiej potrzeby: zachowanie obu przebiegów wynika z kodu, który już jest.

6. Weryfikacja po implementacji

1. Nowe testy 25 razy bezpośrednio do PostgreSQL oraz 10 razy przez Supavisor. Oczekuję 100% przejść.
1. Dowód, że wykrywają luki: M15 i M16 ponownie, z użyciem mojego skryptu (kopia, SHA-256, przywrócenie w finally): nowe testy powinny padać w każdym z 25 powtórzeń. Dla kontroli M14 nadal ma być wykrywana przez test 2.
1. Cały zestaw z lokalną bazą (bezpośrednio, plus raz przez Supavisor).
1. ruff check, ruff format --check, git diff (powinna zmienić się wyłącznie ta jedna plik testowy), liczby wierszy w bazie (0/0/0), services/ nietknięty. Bez commitów i pushy.

1. Ryzyka

- Warunek czekania: test zakłada, że drugi INSERT czeka na blokadzie z wait_event_type = 'Lock'. Tak działa już test 4 (create_waits_for_archive_then_succeeds) w tym samym mechanizmie, i przechodzi także przez Supavisor.
- Uwaga do punktu 2 weryfikacji: żeby udowodnić wykrycie M15 i M16, muszę znów tymczasowo zmutować career_goal_service.py, tą samą bezpieczną procedurą co poprzednio (kopia, SHA-256, przywrócenie w finally). Twoje „nie zmieniaj kodu produkcyjnego” rozumiem jako zakaz trwałej zmiany i potrzebuję potwierdzenia, że ta tymczasowa mutacja jest dozwolona. Bez niej mogę udowodnić tylko, że testy przechodzą na poprawnym kodzie, a nie że łapią luki.
- Kolejna możliwa luka, której ten plan nie zamyka: update_goal i create_goal przy innych przeplotach (np. archiwizacja podczas otwartego update) mają już testy 2, 4 i 5a. Nie wykryłem dodatkowych luk w tym zestawie.

8. Decyzje do potwierdzenia

1. Nazwy i miejsca testów: test*1b*… po teście 1, test*3b*… po teście 3. Zgoda?
1. Asercja na **cause** (UniqueViolation z nazwą ograniczenia) w teście M16. Zgoda?
1. Zgoda na tymczasową mutację career_goal_service.py (M15 i M16, z przywróceniem i kontrolą SHA-256) jako dowód, że testy wykrywają luki?
1. Liczby powtórzeń: 25 bezpośrednio, 10 przez Supavisor. Zgoda?

Po Twojej akceptacji dopiszę tylko te dwa testy i wykonam weryfikację z pkt 6.

✻ Churned for 45s · done 20:04

❯ ok jest akceptacja

Ran 1 shell command

Zmiana to wyłącznie dopisanie (0 usuniętych linii, dwa fragmenty), ruff czysty. Uruchamiam nowe testy: 25 razy bezpośrednio i 10 razy przez Supavisor.

Ran 1 shell command

To błąd mojego polecenia (zsh nie rozdzielił dwóch identyfikatorów testów), a nie testów. Używam selektora -k.

Ran 1 shell command

Nowe testy przechodzą na poprawnym kodzie (25/25 bezpośrednio, 10/10 przez Supavisor). Teraz dowód, że wykrywają luki: tymczasowa mutacja M15/M16 z kopią, SHA-256 i przywróceniem w finally.

Ran 1 shell command

Mutacje wykrywane i plik przywrócony (SHA 366190487ddc przed i po). Walidacja końcowa: cały zestaw z bazą, Ruff, git diff, stan bazy.

Ran 1 shell command

────────────────────────────────────────────────── 1 new message ───────────────────────────────────────────────────

Dwa deterministyczne testy współbieżności: gotowe

Luki M15 i M16 są zamknięte. Kod produkcyjny nie został zmieniony, nie commitowałem, nie dotykałem services/, nie łączyłem się ze zdalnym Supabase.

Co dodałem

Jedyna zmiana w repozytorium to backend/tests/test_services_concurrency.py: +58 linii, 0 usuniętych. Dopisałem dwie funkcje i dwa nagłówki sekcji, a istniejące testy 1–6, pomocniki i TIMEOUT nie były zmieniane. Brak nowych importów.

- test_3b_archive_waits_for_open_archive_then_goal_archived (M15): transakcja trzymająca archiwizację, druga archive_goal czeka na blokadzie, po COMMIT dostaje GoalArchivedError. Dodatkowo sprawdzam stan wiersza (archived) i brak aktywnych celów.
- test_1b_create_waits_for_open_create_then_active_goal_exists (M16): transakcja trzymająca INSERT, drugi create_goal czeka, po COMMIT dostaje ActiveGoalExistsError, a nie surowy UniqueViolation. Asercja na **cause** potwierdza, że przyczyną jest UniqueViolation z ograniczenia one_active_goal_per_user, więc zatrzymała go baza, a nie wcześniejsze sprawdzenie.

Kolejność kontrolują blokady w bazie. Test wypuszcza pierwszą transakcję dopiero po tym, jak pg_stat_activity potwierdzi, że druga operacja czeka. Nie ma sleep decydującego o kolejności, tylko 20 ms przerwy między zapytaniami w istniejącym pomocniku.

Wyniki

┌───────────────────────────────────────┬─────────────────────────────────────────────────────────────────────┐
│ Sprawdzenie │ Wynik │
├───────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ Nowe testy na poprawnym kodzie, │ 25/25 czystych przebiegów │
│ bezpośrednio │ │
├───────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ Nowe testy na poprawnym kodzie, │ 10/10 czystych przebiegów │
│ Supavisor │ │
├───────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ M15 z test_3b (tymczasowa mutacja, 25 │ wykryta 25/25 │
│ razy) │ │
├───────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ M16 z test_1b (tymczasowa mutacja, 25 │ wykryta 25/25 │
│ razy) │ │
├───────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ Kontrola: stare testy 3 i 1 na tych │ 0/25 (jak wcześniej) │
│ samych mutacjach │ │
├───────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ Harness M14, M15, M16 (bezpośrednio i │ M14 wykrywa test 2, M15 wykrywa test_3b, M16 wykrywa test_1b (i │
│ przez Supavisor) │ test_4), wszystkie trzy też przez Supavisor │
├───────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ Cały zestaw z bazą, bezpośrednio │ 481 przeszło (było 479) │
├───────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ Cały zestaw z bazą, przez Supavisor │ 481 przeszło │
├───────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ Bez zmiennych bazy │ oba nowe testy pomijane (skipped) │
├───────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ ruff check / ruff format --check │ czysto / 53 pliki sformatowane │
└───────────────────────────────────────┴─────────────────────────────────────────────────────────────────────┘

Bezpieczeństwo mutacji

Do dowodu wykrycia tymczasowo mutowałem career_goal_service.py, tak jak w poprzedniej rundzie: kopia, SHA-256 i przywrócenie w finally. SHA przed i po: 366190487ddc. git diff dla trzech plików produkcyjnych (career_goal_service.py, profile_service.py, core/errors.py) pokazuje brak różnic względem HEAD.

Stan repozytorium

- git diff --stat: tylko backend/tests/test_services_concurrency.py, 58 wstawień.
- HEAD bez zmian: 5f2391d.
- Nieśledzone są wyłącznie obce pliki w services/ (teraz cztery: dodałeś 140 - piętnasty…md, ja ich nie dotykałem).
- Baza lokalna: auth.users 0, profiles 0, career_goals 0.

Odnotowane

- Testy 1 i 3 (z Barrier) zostają, bo sprawdzają wyniki prawdziwe w każdym przeplocie, ale nie wykrywają M15/M16. Nowe testy 1b i 3b zajmują się tym deterministycznie.
- Po zamknięciu luk wszystkie 22 mutacje z rundy mutacyjnej są wykrywane także w odpowiednich testach współbieżności (M14, M15, M16 po jednym lub dwóch). Pozostałe 19 mutacji wykrywają testy jednostkowe, co zostało opisane wcześniej.

Sugerowany commit (Ty commitujesz): test: add deterministic concurrency tests for archive and create races.

Czekam na Twój commit i dalsze polecenie: etap 6.4 (integracja handlerów z main.py) albo przegląd zakresu 5f2391d.
