Zatwierdzam wariant V3 jako rekomendowany projekt synchronizacji zapisów kompetencji.

Zatwierdzam również:
1. Niezmienniki dotyczące kolejności blokad i operacji zapisu.
2. Zasady dla ról uprzywilejowanych i przyszłego kodu.
3. Testy T1–T7 jako wymagany zakres Stage 9.1b.
4. Obsługę 40P01 jako błędu możliwego do ponowienia w Stage 9.2.
5. Brak SECURITY DEFINER i brak rozszerzania istniejących grantów.

Przed rozpoczęciem implementacji doprecyzuj jeszcze w dokumentacji i planie testów:
- zachowanie, gdy użytkownik nie ma aktywnego celu;
- próbę zapisu do celu zarchiwizowanego lub należącego do innego użytkownika;
- INSERT obejmujący wiele kompetencji, w tym przekroczenie limitu 50;
- UPDATE obejmujący wiele kompetencji.

Teraz rozpocznij wyłącznie Stage 9.0:
- zaktualizuj DATABASE-DESIGN.md;
- zaktualizuj API-DESIGN.md;
- zaktualizuj MVP-SCOPE.md;
- zaktualizuj IMPLEMENTATION-PLAN.md.

Nie twórz migracji, nie zmieniaj schematu bazy, nie uruchamiaj Supabase, nie wykonuj testów wymagających bazy, nie czytaj .env i nie commituj ani nie pushuj.

Kompetencje mają pozostać oznaczone jako Planned, dopóki Stage 9.1 nie przejdzie wymaganych testów.

Po zakończeniu pokaż:
- listę zmienionych plików;
- najważniejsze zmiany;
- ostateczny opis V3;
- pozostałe ryzyka i założenia;
- plan Stage 9.1a i 9.1b.

Zatrzymaj się i poczekaj na moją akceptację przed Stage 9.1.




Zaczynam Stage 9.0 od odczytu fragmentów dokumentów, które będę edytować.

  Searched for 1 pattern, read 3 files, ran 1 shell command

Pliki używają zakończeń LF, więc sekcje można bezpiecznie podmieniać. Nieśledzony folder Obsidian/ w katalogu głównym nie jest mój, więc go nie ruszam, a nową treść przygotowuję w katalogu tymczasowym. · summary

  Made 5 scratchpad edits +402

Treść jest gotowa; wstawiam ją teraz do czterech dokumentów skryptem, który przerwie działanie, jeśli którykolwiek znacznik nie występuje dokładnie raz. · summary

  Ran 1 shell command · 1 failed

Heredoc w powłoce nie przeszedł. Zapiszę skrypt narzędziem Write i uruchomię go osobno.

  Made 1 scratchpad edit +236

Najpierw próbny przebieg na kopii dokumentów, żeby ewentualny błąd znacznika nie zostawił repozytorium w stanie częściowo zmienionym.

  Ran 1 shell command

Próbny przebieg przeszedł (każdy znacznik wystąpił dokładnie raz). Stosuję zmiany w repozytorium.

  Ran 1 shell command · 1 failed

Wprowadziłem poprawki; teraz sprawdzam spójność nagłówków i ewentualne pozostałe stare sformułowania. · summary

  Ran 1 shell command

Dwie drobne poprawki: nagłówek 6.2.1 ma zejść do podsekcji 6.2, a opis testu T1 w planie jest niejasny. Pole current_level w API-DESIGN:658 dotyczy odpowiedzi progresu, więc nie koliduje z decyzją. · summary

● Update(DATABASE-DESIGN.md)
Added 1 line, removed 1 line
- Every policy writes the check as `(select auth.uid())`, as a sub-select.
- Column grants mean that `id`, `created_at` and `updated_at` (and `user_id` on UPDATE) can never be written by a client.

### 6.2.1 competencies — Planned
#### 6.2.1 competencies — Planned

Approved in Stage 9.0; no migration exists yet. The pattern is that of `career_goals`: `REVOKE ALL` from `public`, `anon` and `authenticated` first, then only the minimum is granted to `authent
icated`. `anon` and `skillloop_api` get nothing. There is no change to the grants of any existing table.


● Update(IMPLEMENTATION-PLAN.md)
Added 1 line, removed 1 line

|Test|Scenario|Expected result|
|---|---|---|
|T1|The scenario of "lock the row, then the goal, then a colliding name": helper H locks the goal row (`FOR NO KEY UPDATE`) and keeps the transaction open; A updates competency R and waits for
the goal; H then inserts a name that collides with R. Variant T1b: H inserts a different name.|H gets `23505` at once and does not wait for A; after H ends, A succeeds; no `40P01`. T1b: A waits
, H commits, A succeeds.|
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

  Ran 1 shell command

Stage 9.0 zakończony: dokumentacja zaktualizowana, czekam na akceptację

Zmieniłem tylko cztery pliki w docs/. Nie tworzyłem migracji, nie ruszałem schematu, nie uruchamiałem Supabase ani testów z bazą, nie czytałem .env i nie robiłem commitów ani pushów. Wszystkie sekcje o kompetencjach są oznaczone jako Planned. Sprawdziłem tylko, że znaczniki w dokumentach wystąpiły dokładnie raz i że nagłówki są spójne, a nie renderowałem dokumentów. W katalogu głównym jest nieśledzony folder Obsidian/, którego nie tworzyłem i nie ruszałem.

1. Zmienione pliki

┌───────────────────────────────────────────┬────────────────────────────────────────────────┐
│                   Plik                    │            Zakres (git diff --stat)            │
├───────────────────────────────────────────┼────────────────────────────────────────────────┤
│ docs/DATABASE-DESIGN.md (v1.2 → v1.3)     │ +247/−… (najwięcej zmian)                      │
├───────────────────────────────────────────┼────────────────────────────────────────────────┤
│ docs/API-DESIGN.md (v1.0 → v1.1)          │ sekcja 7 przepisana, tabela błędów             │
├───────────────────────────────────────────┼────────────────────────────────────────────────┤
│ docs/IMPLEMENTATION-PLAN.md (v1.0 → v1.1) │ Phase 2 rozbita, nowy szczegółowy plan Stage 9 │
├───────────────────────────────────────────┼────────────────────────────────────────────────┤
│ docs/MVP-SCOPE.md (v1.0 → v1.1)           │ sekcja 3.2                                     │
└───────────────────────────────────────────┴────────────────────────────────────────────────┘

Razem: 383 dodania, 142 usunięcia. Pliki mają zakończenia linii LF, jak wcześniej. Ostrzeżenie gita o LF→CRLF dotyczy jego konfiguracji autocrlf, a nie mojej zmiany.

2. Najważniejsze zmiany

DATABASE-DESIGN.md
- 4.3 competencies przepisana: kolumny, ograniczenia i indeksy w wersji planowanej, model własności (per cel), UNIQUE (id, user_id) na career_goals, złożony FK z ON DELETE CASCADE.
- Jawna lista tego, czego tabela nie zawiera: current_level, source, expectations, hierarchii i osobnego FK do auth.users.
- Dokładne definicje memory, application i documentation, z kolumną "czego to nie oznacza". application to wymaganie roli i nie oznacza udowodnionej biegłości praktycznej.
- 4.3.1 (nowa): zachowanie w sytuacjach szczególnych. Tabela obejmuje cztery przypadki, o które prosiłeś:
  - brak aktywnego celu,
  - zapis do celu zarchiwizowanego lub cudzego,
  - INSERT wielu kompetencji, w tym przekroczenie 50 (cała instrukcja się wycofuje, nic nie zostaje),
  - UPDATE wielu kompetencji (atomowy, wiersze ukryte przez RLS pomijane, zamiana nazw w jednej instrukcji kończy się 23505).

  Dodałem też wyścig z archiwizacją i zapis przez rolę uprzywilejowaną.
- 4.8 (nowa): synchronizacja zapisów, projekt V3. Pełny opis w sekcji 3 poniżej.
- 6.2.1 (nowa): finalne grants i polityki RLS dla competencies. Grantów istniejących tabel nie zmieniam.
- Pozostałe: uwaga w 4.2 o planowanej zmianie career_goals, 6.3 (zasady zapisują się w bazie, więc działają też przez Data API), 7.1/7.2, 8.2/8.3 (kaskada obejmuje kompetencje i polityka usuwania kont nadal nie jest zatwierdzona), tabela w sekcji 10, nagłówek i stopka.

API-DESIGN.md. Sekcja 7 opisuje ręczny CRUD: GET/POST w Stage 9.2, PATCH/archive w 9.3, obiekt kompetencji, limity pól, reguły, kolejność błędów i obsługę 40P01/40001 jako błędów ponawialnych. generate jest oznaczone jako odroczone (Phase 2B). Planowane kody błędów (COMPETENCY_NOT_FOUND, COMPETENCY_ARCHIVED, COMPETENCY_NAME_EXISTS, COMPETENCY_LIMIT_REACHED) dodałem w osobnej tabeli, żeby tabela "w użyciu" pozostała prawdziwa.

MVP-SCOPE.md. "Remove" → "Archive"; "Expected knowledge and skills" pokryte przez description i rationale; application opisane jako wymaganie, nie dowód biegłości; blok reguł mapy (per cel, limit 50, unikalność nazw, jednokierunkowa archiwizacja, zamrożenie, tylko poziom oczekiwany). Kryteria akceptacji MVP bez zmian.

IMPLEMENTATION-PLAN.md. Phase 2 podzielona na 2A (ręczna mapa = Stage 9) i 2B (AI). Dodany szczegółowy plan Stage 9 z tabelą statusów, listą testów pgTAP, testami T1–T7 i zakresem 9.2/9.3.

3. Ostateczny opis V3

Każdy zapis do competencies najpierw bierze blokadę wiersza celu (FOR NO KEY UPDATE), trzymaną do końca transakcji. Działa jak muteks per cel.

1. Trigger statement-level competencies_lock_goal (BEFORE INSERT OR UPDATE) blokuje aktywny cel zalogowanego użytkownika, zanim jakikolwiek wiersz zostanie odczytany lub zablokowany. Przy braku aktywnego celu nic nie robi i nie zgłasza błędu.
2. Trigger wierszowy competencies_enforce_goal_rules (BEFORE INSERT OR UPDATE) ponownie blokuje cel docelowy i sprawdza jego status. Jeśli cel jest zarchiwizowany, nie istnieje albo należy do kogoś innego, zgłasza 23514 competencies_goal_not_active. Dla INSERT dodatkowo liczy aktywne i zgłasza 23514 competencies_active_limit przy ≥ 50. Dla UPDATE licznika nie ma.
3. Obie funkcje są SECURITY INVOKER, jawnie VOLATILE, z pustym search_path i bez EXECUTE dla ról API. Bez SECURITY DEFINER, bez zmian grantów.

Dlaczego to działa:
- Limit 50: count(*) wykonuje się po zdobyciu blokady jako osobne zapytanie ze świeżym snapshotem. Liczba aktywnych rośnie tylko przez INSERT, a wszystkie INSERT-y celu są szeregowane.
- Zamrożenie: archiwizacja celu bierze tę samą blokadę, więc jest szeregowana z każdym zapisem kompetencji. Instrukcja, która czekała, sprawdza status ponownie po zdobyciu blokady.
- Brak cykli: kolejność blokad to zawsze cel, potem wiersze kompetencji, a pierwsza blokada jest wyłączna. Pisarz trzymający blokadę celu nie czeka na nikogo, kto czeka na ten cel.

Niezmienniki:
- L1: kolejność blokad: najpierw cel, potem kompetencje, nigdy odwrotnie.
- L2: rola uprzywilejowana (postgres, service_role) musi sama zablokować cel przed zapisem do competencies.
- L3: triggery nie mogą być wyłączane dla zapisów aplikacyjnych.
- L4: blokada statement-level opiera się na regule jednego aktywnego celu na użytkownika.

Niezmiennik "jedna instrukcja zapisu na transakcję" z wcześniejszych wersji projektu jest wycofany i w V3 niepotrzebny.

4. Ryzyka i założenia

Założenia, które muszą potwierdzić testy 9.1. Są wymienione w 4.8 i żadnego nie traktuję jako prawdziwego:
1. Blokada przez RLS jest oceniana ponownie po oczekiwaniu (T3).
2. auth.uid() działa w triggerze statement-level, a authenticated blokuje swój cel i nie może zablokować zarchiwizowanego ani cudzego.
3. WITH CHECK INSERT-a jest sprawdzane po triggerze BEFORE ROW, więc kod błędu to 23514, a nie 42501.
4. Zapytanie liczące w triggerze widzi wiersze wstawione wcześniej tą samą instrukcją.
5. Blokada wiersza założona przez trigger nie powoduje oczekiwania w kontroli unikalności (T1).
6. Kolejka blokady jest FIFO, bo na tym opiera się T4. Jeśli nie jest, T4 zgłosi błąd przygotowania zamiast przejść.

Źródło faktów: fakty o PostgreSQL 17 pochodzą z dokumentacji i kodu źródłowego (REL_17_STABLE), nie z uruchomienia. Podagenci nie przeczytali ciała ExecUpdateLockMode, ExecInsertIndexTuples ani ustawiania HOT.

Decyzje, które sam podjąłem w dokumentach i proszę o ocenę:
- Kolejność błędów dla kompetencji, której nie można zmienić: najpierw COMPETENCY_ARCHIVED, potem GOAL_ARCHIVED, w razie braku 404.
- Lista kompetencji zwracana jako {"competencies": [...]}, sortowana po display_order, created_at, id, bez paginacji.
- Opis poziomów beginner/intermediate/advanced jako wymagania roli (moje sformułowanie).
- Liczba ponowień przy 40P01 nie jest ustalona: zostawiłem ją na Stage 9.2.

Ryzyka pozostałe:
- Wszystkie zapisy kompetencji jednego celu są szeregowane (dla MVP pomijalne).
- Nieograniczona liczba zarchiwizowanych kompetencji (zaakceptowane).
- Polityka usuwania kont nadal niezatwierdzona.
- Przy wprowadzeniu wielu aktywnych celów V3 wymaga ponownego przeglądu (L4).

5. Plan Stage 9.1a i 9.1b (po Twojej akceptacji)

9.1a: migracja i pgTAP.
- Jedna migracja (supabase migration new create_competencies) zawiera:
  - UNIQUE (id, user_id) na career_goals,
  - tabelę competencies z ograniczeniami i indeksami,
  - RLS, grants i polityki z 6.2.1,
  - trigger updated_at,
  - oba triggery V3 i ich funkcje.
- Test: supabase/tests/database/30_competencies.test.sql, operacje jako authenticated.
- Zakres testu:
  - struktura, CHECK-i, grants (w tym niezmienione grants career_goals);
  - zdolność blokowania jako authenticated;
  - izolacja użytkowników;
  - brak aktywnego celu;
  - cel zarchiwizowany i cudzy (z wyłączonym triggerem w transakcji testowej, żeby przetestować politykę osobno);
  - jednokierunkowa archiwizacja i zamrożenie;
  - unikalność nazw;
  - limit 50 pojedynczo;
  - wielowierszowy INSERT (50 przechodzi, 51. wycofuje całość, duplikat nazw wycofuje całość);
  - wielowierszowy UPDATE (atomowość, wiersze ukryte przez RLS, zamiana nazw);
  - updated_at i kaskada.
- Testy 00, 10 i 20 muszą przejść bez zmian.
- Uruchomienie lokalne wymaga Twojej zgody w tym momencie.

9.1b: testy integracyjne w Pythonie (backend/tests/test_competencies_db_concurrency.py, wiele połączeń, jako authenticated, z dowodem oczekiwania przez pg_blocking_pids):
- C0: read committed.
- T1: dokładnie Twój scenariusz, plus wariant T1b.
- T2: zamiana nazw, deterministycznie.
- T3: archiwizacja celu kontra zapisy.
- T4: wielowierszowy UPDATE kontra edycja pojedyncza.
- T5: kolejność kasowania kaskadowego.
- T6: macierz par operacji, w tym limit 50 przy równoległych INSERT-ach.
- T7: ograniczony stress.

Jeśli którekolwiek założenie z sekcji 4 się nie potwierdzi, zatrzymuję się i raportuję. Opcje awaryjne to wariant bez guardu UPDATE albo SECURITY DEFINER wyłącznie za Twoją osobną zgodą.

Proponowany komunikat commita (commit robisz Ty): docs: design competencies model and write synchronisation for Stage 9

Zatrzymuję się i czekam na akceptację przed Stage 9.1.
