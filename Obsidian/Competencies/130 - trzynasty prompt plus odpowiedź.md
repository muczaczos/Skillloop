# Stage 9.1b – PostgreSQL concurrency integration tests

Rozpoczynamy Stage 9.1b projektu SkillLoop.

## Cel

Zweryfikuj rzeczywiste zachowanie mechanizmu synchronizacji zapisów kompetencji opisanego w dokumentacji Stage 9.0 i zaimplementowanego w migracji:

`supabase/migrations/20261123113335_create_competencies.sql`

Stage 9.1a jest zakończony:
- migracja została zastosowana na lokalnym Supabase;
- `supabase test db`: 218/218 PASS;
- testy pgTAP przechodzą;
- commit Stage 9.1a został wykonany.

Nie powtarzaj Stage 9.1a.

## 1. Najpierw analiza

Przed zmianami:
1. Przeczytaj dokumentację Stage 9.0 i znajdź dokładne scenariusze T1–T7.
2. Przeanalizuj migrację `competencies` i istniejące testy.
3. Przeanalizuj `user_transaction()` i istniejący sposób konfiguracji połączeń testowych.
4. Sprawdź, jak projekt realizuje testy integracyjne PostgreSQL i jak zarządza transakcjami.
5. Przygotuj krótką listę testów, które zamierzasz dodać, wraz z opisem scenariuszy T1–T7.

Nie zgaduj treści scenariuszy T1–T7. Korzystaj z dokumentacji projektu.

## 2. Zakres testów

### A. Rzeczywista siła blokady

Zweryfikuj zachowanie blokady `FOR NO KEY UPDATE` założonej przez trigger.

Test powinien używać dwóch niezależnych połączeń PostgreSQL:

- Połączenie A rozpoczyna transakcję i wykonuje zapis kompetencji, utrzymując blokadę celu.
- Połączenie B próbuje wykonać `SELECT ... FOR SHARE NOWAIT` na tym samym celu. Oczekiwany wynik: SQLSTATE `55P03`.
- Następnie połączenie B próbuje `SELECT ... FOR KEY SHARE NOWAIT`. Oczekiwany wynik: sukces.

Nie używaj `xmax` jako dowodu siły blokady.

### B. Testy współbieżności T1–T7

Zaimplementuj wszystkie scenariusze T1–T7 zgodnie z dokumentacją Stage 9.0.

Wymagania:
- rzeczywiste, niezależne połączenia PostgreSQL;
- kontrolowana synchronizacja transakcji za pomocą barier lub równoważnego mechanizmu;
- możliwość sprawdzenia, która transakcja blokuje drugą;
- wykorzystanie `pg_blocking_pids()` tam, gdzie jest to potrzebne;
- timeouty zapobiegające zawieszeniu testów;
- weryfikacja końcowego stanu danych, nie tylko kodów odpowiedzi.

### C. Limit 50 kompetencji

Sprawdź, czy równoczesne próby dodawania kompetencji nie pozwalają przekroczyć limitu 50 aktywnych kompetencji dla jednego celu.

Uwzględnij przypadek, w którym kilka transakcji próbuje jednocześnie dodać ostatnie dostępne kompetencje.

Oczekiwany rezultat:
- maksymalnie 50 aktywnych kompetencji;
- poprawne odrzucenie nadmiarowych zapisów;
- brak uszkodzenia danych;
- brak deadlocków w testowanym scenariuszu.

### D. Multi-row INSERT – D5

Sprawdź wielowierszowy INSERT obejmujący kompetencje przypisane do różnych celów, w tym cel aktywny i zarchiwizowany.

Zweryfikuj, czy:
- statement-level trigger poprawnie blokuje aktywne cele;
- row-level trigger odrzuca niedozwolony zapis;
- cała instrukcja zachowuje atomowość;
- nie pozostają częściowo zapisane dane.

### E. Rola uprzywilejowana

Zweryfikuj kontrakt L2 opisany w Stage 9.0:
- role uprzywilejowane bez `auth.uid()` nie korzystają z blokady statement-level trigger;
- muszą jawnie zablokować cel przed zapisem kompetencji;
- sprawdź poprawność zachowania przy przestrzeganiu tej kolejności.

Nie zmieniaj implementacji triggerów.

## 3. Wymagania techniczne

- Korzystaj z istniejącej infrastruktury testowej projektu.
- Testy mają korzystać z rzeczywistego lokalnego PostgreSQL, a nie mocków.
- Każda równoległa transakcja musi używać osobnego połączenia.
- Zapewnij deterministyczne scenariusze testowe, unikaj opierania wyników na przypadkowym czasie wykonania.
- Po testach posprzątaj wyłącznie utworzone przez nie dane.
- Nie pozostawiaj otwartych transakcji ani połączeń.

## 4. Ograniczenia bezpieczeństwa

Zatwierdzam prace i testy Stage 9.1b wyłącznie na lokalnym Supabase.

Bezwzględnie:
- nie wykonuj żadnych operacji na zdalnym Supabase;
- nie używaj `supabase db push`, `supabase link` ani `supabase db reset`;
- nie czytaj `.env` i nie wypisuj sekretów ani connection stringów;
- nie zmieniaj migracji, schematu, triggerów, RLS ani grantów;
- nie zmieniaj istniejących testów Stage 9.1a;
- nie wykonuj commitów ani pushów;
- nie rozpoczynaj Stage 9.2.

Jeżeli lokalna baza nie jest dostępna albo nie możesz zagwarantować, że testy nie dotkną danych spoza własnych fixture'ów, zatrzymaj się i przedstaw problem.

Jeśli wykryjesz błąd implementacji, nie poprawiaj go automatycznie. Najpierw przedstaw reprodukcję, przyczynę i proponowane rozwiązanie.

## 5. Raport końcowy

Po wykonaniu przedstaw:
1. Listę dodanych plików i zmian.
2. Opis każdego testu T1–T7.
3. Wynik testu siły blokady `FOR NO KEY UPDATE`.
4. Wynik testu limitu 50.
5. Wynik testu D5.
6. Wynik testu roli uprzywilejowanej.
7. Liczbę PASS, FAIL i błędów wykonania.
8. Informację o timeoutach i deadlockach.
9. Potwierdzenie, że testy korzystały wyłącznie z lokalnego PostgreSQL.
10. Potwierdzenie, że migracja i schemat nie zostały zmienione.

Końcowy status: PASS albo NOT PASS.

Nie uznawaj braku deadlocków w kilku testach za matematyczny dowód ich niemożliwości. Raportuj dokładnie, jakie scenariusze zostały sprawdzone.




 Searched for 4 patterns, read 6 files, ran 2 shell commands · 1 failed

The test suite needs TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL, and neither is set in my shell. I'm checking .env.example and the role migration to see how local connections should be built. · summary

  Searched for 1 pattern, read 2 files, ran 1 shell command

Stage 9.1b: analiza i plan, czekam na zatwierdzenie

Niczego jeszcze nie zmieniłem ani nie uruchomiłem. Zatrzymałem się z dwóch powodów: CLAUDE.md wymaga zatwierdzenia planu przed większą zmianą, a tej sesji brakuje poświadczeń do uruchomienia testów (szczegóły niżej).

Co sprawdziłem

- Scenariusze T1–T7: są w docs/IMPLEMENTATION-PLAN.md w sekcji 9.1b (linie 247–260). Nie zgaduję ich treści, tylko je wdrażam.
- Infrastruktura testów:
  - Wzorem jest backend/tests/test_services_concurrency.py. Ma held_transaction (transakcja w osobnym wątku, trzymana do zwolnienia), wait_until_blocked_by (dowód oczekiwania przez pg_stat_activity i pg_blocking_pids), run_in_parallel (bariera) oraz limit czasu 20 s.
  - Fixtures admin i users są w conftest.py.
  - open_api_pool wymusza lokalny host.
  - user_transaction() robi SET LOCAL ROLE authenticated i ustawia claims.
- Lokalna baza:
  - Działa, to PostgreSQL 17.11, domyślna izolacja read committed.
  - Konto postgres ma domyślny lokalny login i może przełączyć się na authenticated.

Blokada: brak TEST_DATABASE_URL i TEST_DATABASE_ADMIN_URL

- Żadna z tych zmiennych nie jest ustawiona w moim środowisku. Bez nich istniejące testy integracyjne się pomijają (skipif).
- Rola skillloop_api ma lokalnie ustawione hasło, którego nie znam. Migracja go nie ustawia, a .env mam zakaz czytać.
- Nie ustawię własnego hasła, bo zmieniłbym stan roli w twojej lokalnej bazie i mógłbym zepsuć twoją konfigurację backendu.

Proponuję (rekomendowane): napiszę testy w stylu istniejących, które przy braku zmiennych się pomijają, i sprawdzę je statycznie (ruff, mypy, pytest --collect-only). Uruchomisz je ty komendą z prefiksem !, ze zmiennymi ustawionymi w tej samej linii. Wynik trafi wtedy do konwersacji, a ja go przeanalizuję i złożę raport. Dzięki temu nie dotykam sekretów.

Alternatywa gorsza: połączenia tylko przez konto postgres (SET LOCAL ROLE authenticated), bez skillloop_api i bez prawdziwego user_transaction(). To odbiega od dokumentu i testuje trochę inną ścieżkę, więc nie polecam.

Plan: jeden nowy plik

backend/tests/test_competencies_db_concurrency.py, bez zmian w migracji, schemacie i istniejących testach. Fixtures: własny użytkownik i cel w każdym teście, sprzątanie przez users (kaskada) oraz finally z zakończeniem i dołączeniem każdego wątku. Rola uprzywilejowana to połączenie admin, z blokadą celu zgodnie z L2.

┌──────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│         Test         │                                                                  Scenariusz (z dokumentacji, chyba że zaznaczono)                                                                   │
├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ C0                   │ Obie sesje używają read committed.                                                                                                                                                  │
├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ L1 (twoja sekcja A,  │ A zapisuje kompetencję i trzyma transakcję. B robi FOR SHARE NOWAIT na celu i oczekuje 55P03, potem FOR KEY SHARE NOWAIT i oczekuje sukcesu. Bez xmax.                              │
│ spoza listy T)       │                                                                                                                                                                                     │
├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ T1 / T1b             │ H blokuje cel i trzyma. A edytuje kompetencję R i czeka na cel (dowód oczekiwania). H wstawia kolidującą nazwę, więc dostaje 23505 od razu. Po zwolnieniu A kończy sukcesem, bez    │
│                      │ 40P01. T1b: H wstawia inną nazwę, A czeka i kończy po commicie H.                                                                                                                   │
├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ T2                   │ Zamiana nazw w wielu instrukcjach: T1 zmienia a→z i trzyma, T2 zmienia b→a i czeka na cel, T1 zmienia c→b. Oczekiwane: T1 dostaje 23505, po jego zakończeniu T2 też, bez 40P01.     │
├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ T3                   │ Archiwizacja celu kontra INSERT, edycja i archiwizacja kompetencji, w obu kolejnościach. Czekający zapis kończy się competencies_goal_not_active, a zapis, który był pierwszy,      │
│                      │ zostaje. To dowodzi ponownej oceny po oczekiwaniu.                                                                                                                                  │
├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ T4                   │ Wielowierszowy UPDATE kontra edycja jednego z tych wierszy, oba uruchomione podczas trzymanej blokady celu i oba udowodnione jako blokowane. Wiele powtórzeń, obie kolejności       │
│                      │ startu. Asercje tylko niezależne od kolejności: brak 40P01, oba kończą się w limicie czasu, dane zgodne z wynikiem którejś seryjnej kolejności.                                     │
├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ T5a                  │ Uprzywilejowany pomocnik blokuje cel, potem robi jawny DELETE kompetencji, a U edytuje jedną z nich. U jest blokowany przed dotknięciem wiersza kompetencji, po commicie zmienia 0  │
│                      │ wierszy.                                                                                                                                                                            │
├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ T5b                  │ Prawdziwa kaskada: DELETE FROM career_goals, bez jawnego DELETE kompetencji. Części: (1) H pierwszy, (2) U pierwszy, (3) jednoczesne z barierą, wiele powtórzeń. Stan końcowy       │
│                      │ sprawdzany przez admin.                                                                                                                                                             │
├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ T6                   │ Pary INSERT, edycja i archiwizacja kompetencji kontra archiwizacja celu oraz wielowierszowy INSERT i UPDATE. Limit: 49 aktywnych i kilka jednoczesnych INSERT-ów (wygrywa dokładnie │
│                      │  jeden), INSERT trzymany jako 50., a drugi INSERT czeka (commit i rollback), oraz 50 aktywnych z archiwizowaną jedną i czekającym INSERT.                                           │
├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ T7                   │ Ograniczony stres: wiele wątków miesza INSERT, edycje, archiwizację kompetencji i celu przez kilka sekund. Sprawdzane: brak 40P01, nie więcej niż 50 aktywnych, unikalne nazwy,     │
│                      │ brak zapisu po zatwierdzonej archiwizacji celu.                                                                                                                                     │
├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ D5 (twoja sekcja D,  │ Wielowierszowy INSERT obejmujący cel aktywny i zarchiwizowany. Oczekiwane: cała instrukcja odrzucona (competencies_goal_not_active), żaden wiersz nie zostaje, blokada celu         │
│ spoza listy T)       │ aktywnego zwolniona po rollbacku.                                                                                                                                                   │
├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ L2 (twoja sekcja E,  │ Rola uprzywilejowana bez auth.uid(): bez jawnej blokady nie czeka na transakcję użytkownika, z blokadą celu przed zapisem czeka i poprawnie się układa.                             │
│ spoza listy T)       │                                                                                                                                                                                     │
└──────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

Zasady wspólne: limit czasu 20 s na każde oczekiwanie, pooler z kilkoma połączeniami, zero sleep() decydującego o kolejności, 40P01 jest porażką, a testy nie zależą od kolejności wybudzania czekających.

Ograniczenie z konfiguracji roli: skillloop_api ma statement_timeout = 15s i idle_in_transaction_session_timeout = 30s. Trzymane transakcje i oczekiwania muszą się mieścić w tych limitach, więc projektuję je krócej (np. trzymanie do 10 s).

Decyzja potrzebna od ciebie

1. Czy zatwierdzasz powyższy plan?
2. Czy wybieramy wariant rekomendowany, czyli ty uruchamiasz testy przez ! ze swoimi zmiennymi środowiskowymi?

Jeśli wolisz inny sposób uruchomienia, powiedz jaki. Do tego czasu nie piszę żadnych plików.






