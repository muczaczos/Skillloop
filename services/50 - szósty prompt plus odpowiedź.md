 Realizujemy **krok 5 z etapu 6.3 – testy integracyjne i współbieżności**.

  Kroki 1–4 zostały zaakceptowane. Teraz sprawdzamy rzeczywiste działanie serwisów z lokalną bazą PostgreSQL.

  ## 1. Najpierw analiza

  Zanim napiszesz kod:

  1. Sprawdź aktualny `git status` i HEAD.
  2. Przeanalizuj istniejące fixtures, `db_integration_helpers.py`, `conftest.py`, testy repozytoriów oraz `user_transaction`.
  3. Sprawdź, jak projekt rozpoznaje testy wymagające lokalnej bazy.
  4. Sprawdź konfigurację `DATABASE_URL` i upewnij się, że testy będą korzystać wyłącznie z lokalnej bazy.

  **Bezpieczeństwo:**
  - Nie łącz się z hosted Supabase.
  - Nie wykonuj `supabase db push`, `supabase db reset` ani żadnych zdalnych operacji.
  - Nie zmieniaj danych w zewnętrznej bazie.
  - Nie uruchamiaj automatycznie destrukcyjnych operacji.
  - Jeśli nie możesz jednoznacznie potwierdzić, że baza jest lokalna, zatrzymaj się i zapytaj.

  ## 2. Zakres zmian

  Utwórz:

  - `backend/tests/test_services_integration.py`
  - `backend/tests/test_services_concurrency.py`

  Nie zmieniaj produkcyjnego kodu, repozytoriów, migracji, schematów ani dokumentacji.

  Nie zmieniaj istniejących testów bez uzasadnienia i wcześniejszego przedstawienia powodu.

  ## 3. Testy integracyjne

  Wykorzystaj rzeczywiste:
  - PostgreSQL;
  - `user_transaction`;
  - RLS;
  - repozytoria;
  - serwisy profilu i celów kariery.

  Sprawdź co najmniej:

  **Profile**
  - pobranie profilu;
  - aktualizację profilu;
  - zachowanie jawnego `null`;
  - brak możliwości odczytu lub modyfikacji profilu innego użytkownika.

  **Career goals**
  - utworzenie celu;
  - pobranie aktywnego celu;
  - aktualizację celu;
  - archiwizację celu;
  - brak aktywnego celu;
  - próbę utworzenia drugiego aktywnego celu;
  - brak możliwości odczytu lub modyfikacji celu innego użytkownika;
  - poprawną klasyfikację celu zarchiwizowanego.

  Testy muszą sprzątać po sobie utworzone dane. Nie usuwaj danych istniejących przed testem.

  ## 4. Testy współbieżności

  Wykonaj sześć scenariuszy:

  1. **Dwa równoległe create_goal dla jednego użytkownika**
     - jeden kończy się sukcesem;
     - drugi zgłasza `ActiveGoalExistsError`.

  2. **Update podczas niezakończonej transakcji archive**
     - archiwizacja blokuje rekord;
     - aktualizacja czeka na zakończenie pierwszej transakcji;
     - po zatwierdzeniu archiwizacji aktualizacja zgłasza `GoalArchivedError`.

  3. **Dwa równoległe archive_goal**
     - jedna operacja kończy się sukcesem;
     - druga zgłasza `GoalArchivedError`.

  4. **Create podczas niezakończonej transakcji archive**
     - utworzenie nowego celu czeka na zakończenie archiwizacji;
     - po zatwierdzeniu archiwizacji utworzenie nowego celu kończy się sukcesem.

  5. **Dwie równoległe aktualizacje różnych pól tego samego celu**
     - obie operacje kończą się poprawnie;
     - żadna zmiana nie zostaje utracona.

  6. **Dwóch różnych użytkowników korzystających z tej samej puli połączeń**
     - operacje nie mieszają kontekstu RLS;
     - każdy użytkownik widzi wyłącznie własne dane.

  ### Ważne wymagania techniczne

  - Używaj rzeczywistych transakcji i osobnych połączeń.
  - Koordynuj wątki przez `threading.Event`, `Barrier` lub podobne mechanizmy.
  - Nie używaj `sleep()` do ustalania kolejności operacji.
  - Przy sprawdzaniu oczekiwania na blokadę wykorzystuj `pg_stat_activity` i `wait_event_type = 'Lock'`, jeśli jest to potrzebne.
  - Nie zakładaj, że sama bariera gwarantuje kolejność wykonania zapytań.
  - Testy muszą mieć timeouty, aby uniknąć zawieszenia całego zestawu.
  - Zadbaj o sprzątanie danych i zwalnianie połączeń nawet w przypadku niepowodzenia testu.

  Jeśli któryś scenariusz wymaga innej konstrukcji technicznej niż zatwierdzona architektura, najpierw wyjaśnij problem. Nie zmieniaj produkcyjnego kodu tylko po to, żeby ułatwić test.

  ## 5. Sposób pracy

  Pracuj etapami:

  1. Najpierw pokaż plan testów i sposób koordynacji współbieżności.
  2. Poczekaj na moją akceptację planu.
  3. Następnie utwórz wyłącznie dwa wskazane pliki testowe.
  4. Uruchom testy integracyjne i współbieżności na lokalnej bazie.
  5. Powtórz testy współbieżności kilkukrotnie, aby sprawdzić ich stabilność.
  6. Uruchom pełny zestaw testów i Ruff.

  Na końcu pokaż:
  - pełną treść nowych plików;
  - wyniki każdego scenariusza;
  - wyniki powtórzeń;
  - liczbę wszystkich testów;
  - `git status`;
  - ewentualne problemy lub ograniczenia.

  Nie wykonuj commita ani pusha.

  **Zatrzymaj się po przedstawieniu planu i poczekaj na moją akceptację. Nie rozpoczynaj jeszcze implementacji testów ani kroku 6.**

  Ran 2 shell commands

Krok 5: plan testów integracyjnych i współbieżności (bez implementacji)

Nie mogę teraz uruchomić testów na lokalnej bazie. Najpierw potrzebuję Twojej decyzji (sekcja „Blokada środowiska”).

1. Co sprawdziłem

- Repozytorium: HEAD to 58195a7, a nieśledzone są tylko pliki z kroków 1–4 i obcy services/.
- Rozpoznawanie testów z bazą:
  - Testy integracyjne mają pytestmark = skipif(not (TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL)).
  - conftest.py na sztywno nadpisuje DATABASE_URL atrapą, więc prawdziwy URL z shella czy .env nie trafi do testów jednostkowych.
  - assert_local() wywołuje pytest.fail dla każdego hosta spoza localhost, 127.0.0.1, ::1 i host.docker.internal, dla obu zmiennych.
- Fixtures:
  - admin: lokalny login postgres, autocommit, tylko do przygotowania i inspekcji danych.
  - users: czterech użytkowników z unikalnymi UUID, usuwanych kaskadowo w finally.
  - pool: pula z jednym połączeniem.
  - open_api_pool(max_size=N): pula na loginie skillloop_api.
- Sprzątanie: testy tworzą wyłącznie dane należące do tymczasowych użytkowników, więc kaskada czyści wszystko i nie dotyka istniejących danych.

2. Blokada środowiska

- TEST_DATABASE_URL i TEST_DATABASE_ADMIN_URL nie są ustawione. Testy by się po cichu pominęły, a pominięty test niczego nie dowodzi.
- Docker Desktop nie działa: docker ps nie łączy się z silnikiem.
- Polecenie supabase nie jest zainstalowane, więc nie mogę uruchomić lokalnego stosu (supabase start).

Nic z tego nie robiłem i nie łączyłem się z żadną bazą. Daj znać, jak chcesz to rozwiązać:
- A. Sam uruchamiasz lokalny stack i ustawiasz obie zmienne. Podasz mi tylko wartości hosta i portu (bez haseł) do potwierdzenia, że są lokalne.
- B. Zgadzasz się, żebym zainstalował Supabase CLI i uruchomił supabase start (lokalnie, wymaga Dockera i pobiera obrazy, kilka GB, ale nie ma kosztów chmurowych). Bez db push ani db reset.

3. Pliki

Tylko tests/test_services_integration.py i tests/test_services_concurrency.py. Produkcyjny kod i istniejące testy zostają bez zmian. Oba pliki dostaną ten sam pytestmark i fixtures co test_repositories_integration.py.

4. test_services_integration.py (przez prawdziwe serwisy, user_transaction, RLS)

- Profil:
  - pobranie profilu;
  - aktualizacja;
  - jawny null czyści display_name, pominięte pola zostają;
  - użytkownik B nie czyta ani nie zmienia profilu A. Serwis zawsze używa user.id z tokena, więc sprawdzam brak wycieku i brak zmiany danych A (weryfikacja kontem admina).
- Cele:
  - utworzenie, pobranie aktywnego i aktualizacja;
  - archiwizacja, potem NoActiveGoalError;
  - brak aktywnego celu od początku;
  - drugi aktywny cel → ActiveGoalExistsError;
  - cel obcego użytkownika → GoalNotFoundError przy update i archive, a dane A bez zmian;
  - zarchiwizowany cel → GoalArchivedError przy update i archive;
  - po archiwizacji można utworzyć nowy cel.

5. test_services_concurrency.py: mechanizm koordynacji

Narzędzia:
- threading.Event i Barrier, z timeoutem na każdym wait() i future.result().
- Pula open_api_pool(max_size=4..6, timeout=...), żeby wątki miały osobne połączenia.

Utrzymanie transakcji „niezatwierdzonej”. Serwis zatwierdza przy powrocie, więc nie da się go zatrzymać w połowie bez zmiany kodu produkcyjnego. Dlatego wątek-holder otwiera prawdziwe user_transaction, wywołuje bezpośrednio career_goals.archive_active (lub update_active), sygnalizuje Event i czeka na release. Wątek-oczekujący woła prawdziwy serwis.

Brak sleep() do ustalania kolejności. Dowód, że oczekujący naprawdę czeka na blokadę:
- Holder zapamiętuje swój pg_backend_pid() z transakcji.
- Wątek główny (połączenie admin) odpytuje pg_stat_activity z warunkiem holder_pid = ANY(pg_blocking_pids(pid)) AND wait_event_type = 'Lock'.
- Odpytywanie ma ograniczony deadline (Event.wait(0.05) w pętli, nie sleep). Po timeoucie test pada z czytelnym komunikatem.
- Dopiero potem ustawiam release.

Ta metoda jest dokładniejsza niż samo wait_event_type = 'Lock': wskazuje konkretnego blokującego, a nie dowolny proces w bazie.

Sprzątanie:
- release.set() jest w finally, więc holder nigdy nie zostaje zawieszony.
- Pula jest zamykana przez fixture.
- users kasuje dane nawet po błędzie.

6. Sześć scenariuszy

┌─────┬───────────────────────────┬──────────────────────────────────────────────────────────────────────────────┬───────────────────────────────────┐
│  #  │        Scenariusz         │                                 Konstrukcja                                  │         Oczekiwany wynik          │
├─────┼───────────────────────────┼──────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────┤
│     │ Dwa równoległe            │ Barrier(2) przed wywołaniem. Kolejność nie ma znaczenia, wynik jest          │ 1 sukces, 1                       │
│ 1   │ create_goal               │ deterministyczny przez UNIQUE.                                               │ ActiveGoalExistsError, w bazie 1  │
│     │                           │                                                                              │ aktywny cel                       │
├─────┼───────────────────────────┼──────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────┤
│ 2   │ update_goal podczas       │ Holder archiwizuje. Weryfikuję oczekiwanie przez pg_blocking_pids. Po        │ GoalArchivedError                 │
│     │ niezatwierdzonego archive │ release UPDATE ponawia warunek status='active'.                              │                                   │
├─────┼───────────────────────────┼──────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────┤
│ 3   │ Dwa równoległe            │ Barrier(2). Drugi UPDATE czeka na blokadę wiersza i po commicie widzi        │ 1 sukces, 1 GoalArchivedError     │
│     │ archive_goal              │ archived.                                                                    │                                   │
├─────┼───────────────────────────┼──────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────┤
│ 4   │ create_goal podczas       │ Holder archiwizuje. INSERT czeka na transakcję holdera przy indeksie UNIQUE  │ Po commicie sukces, nowy cel      │
│     │ niezatwierdzonego archive │ (weryfikacja przez pg_blocking_pids).                                        │ aktywny, stary zarchiwizowany     │
├─────┼───────────────────────────┼──────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────┤
│ 5   │ Dwie aktualizacje różnych │ 5a: holder (update_active pola target_role, niezatwierdzony) + serwis        │ Obie powiodą się, końcowy rekord  │
│     │  pól                      │ update_goal pola seniority czeka. 5b: dwa serwisy pod Barrier.               │ ma obie zmiany                    │
├─────┼───────────────────────────┼──────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────┤
│     │ Dwóch użytkowników, jedna │ Pula max_size=1 (wymusza reużycie tej samej sesji) i max_size=2. Wątki A i B │ Każdy widzi tylko swoje dane, B   │
│ 6   │  pula                     │  naprzemiennie, wiele iteracji pod Barrier, wołają get_profile i             │ dostaje GoalNotFoundError         │
│     │                           │ get_active_goal. Dodatkowo B próbuje update_goal na celu A.                  │                                   │
└─────┴───────────────────────────┴──────────────────────────────────────────────────────────────────────────────┴───────────────────────────────────┘

5b i 6 to uzupełnienia ponad Twoją listę. 5b to wariant bez blokady holdera (race naturalny), a 6 wzmacnia pomiar mieszania kontekstu RLS przez większą liczbę iteracji. Daj znać, jeśli wolisz tylko 5a i jedną iterację w 6.

7. Stabilność i wyniki

- Po napisaniu uruchomię testy współbieżności minimum 10 razy z rzędu (pytest --count wymaga wtyczki, której nie dodaję, więc użyję pętli w shellu).
- Potem pełny pytest i Ruff.
- Pokażę pełną treść plików, wynik każdego scenariusza i powtórzeń, liczbę testów i git status.

Pytania przed startem

1. Środowisko: wariant A czy B z sekcji 2?
2. Czy akceptujesz ten plan, w tym uzupełnienia 5b i wielokrotne iteracje w 6?

Do czasu odpowiedzi nie piszę żadnych plików testowych ani nie przechodzę do kroku 6.

✻ Crunched for 47s · done 9:18