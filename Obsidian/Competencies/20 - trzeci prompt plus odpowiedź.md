Przeanalizowałem raport Stage 9.0–9.1. Akceptuję kierunek architektury i poniższe decyzje:

- Kompetencje per career_goal, bez globalnego katalogu.
- user_id + złożony FK.
- Bez current_level, source i expectations.
- Płaska kategoria.
- knowledge_type: memory, application, documentation.
- Unikalna nazwa aktywnej kompetencji w obrębie celu.
- Limit 50 aktywnych kompetencji.
- Jednokierunkowa archiwizacja.
- Zarchiwizowany cel zamraża kompetencje.
- Ręczny CRUD bez AI.
- display_order pozostaje.
- Bez osobnego FK do auth.users, jeśli integralność zapewnia FK złożony.
- Bez SECURITY DEFINER bez mojej osobnej zgody.
- Nieograniczona liczba zarchiwizowanych kompetencji jest akceptowana w MVP.
- Dokumentacja obejmuje również MVP-SCOPE.md i IMPLEMENTATION-PLAN.md.

Przed implementacją wykonaj jeszcze następujące zadania. Nadal wyłącznie READ-ONLY.

## 1. Zweryfikuj uprawnienia triggera

Szczególnie dokładnie sprawdź, czy proponowany trigger SECURITY INVOKER może wykonać SELECT ... FOR NO KEY UPDATE oraz SELECT ... FOR SHARE na career_goals przy aktualnych grantach i RLS.

Nie zakładaj, że SELECT privilege wystarcza do blokowania wiersza. Sprawdź rzeczywiste wymagania PostgreSQL.

Wyjaśnij, czy trigger będzie działał jako authenticated i czy nie wymaga rozszerzenia grantów do career_goals.

Nie proponuj rozszerzania uprawnień bez wyraźnego wskazania konsekwencji bezpieczeństwa.

## 2. Zweryfikuj mechanizm współbieżności

Przeanalizuj ponownie:
- limit 50 przy równoczesnych INSERT,
- INSERT kontra archiwizacja career_goal,
- UPDATE kompetencji kontra archiwizacja career_goal,
- archiwizację kompetencji kontra INSERT,
- możliwe deadlocki i kolejność blokad.

Jeżeli aktualny projekt nie jest bezpieczny, zaproponuj alternatywę. Nie zmieniaj automatycznie modelu uprawnień.

## 3. Doprecyzuj testy

Przygotuj testy, które:
- wykonują operacje jako authenticated, a nie tylko postgres,
- potwierdzają limit 50 i brak race condition,
- sprawdzają zamrożenie kompetencji po archiwizacji celu,
- potwierdzają brak możliwości obejścia limitu przez równoczesne żądania,
- sprawdzają brak deadlocków.

Wskaż, które testy są możliwe w pgTAP, a które wymagają testów integracyjnych w Pythonie z wieloma połączeniami.

## 4. Finalny plan

Po weryfikacji przedstaw:
- ewentualne korekty projektu migracji,
- ostateczne polityki RLS i grants,
- plan dokumentacji 9.0,
- plan migracji i testów 9.1,
- ryzyka i decyzje wymagające mojej zgody.

Nie zmieniaj żadnych plików, nie twórz migracji, nie uruchamiaj Supabase, nie wykonuj operacji na bazie, nie czytaj .env, nie wykonuj commitów ani pushów.

Zatrzymaj się po raporcie.



Zacznę od źródeł: sprawdzę w dokumentacji i kodzie PostgreSQL, jakie uprawnienia wymaga SELECT … FOR [NO KEY] UPDATE / FOR SHARE, jak działa z RLS i jak wyglądają konflikty blokad. Równolegle odczytam wersję PostgreSQL w konfiguracji (tylko config.toml, bez .env).

  Searched for 1 pattern

● Fetch(https://www.postgresql.org/docs/current/sql-grant.html)
Received 3.8KB

● Fetch(https://www.postgresql.org/docs/current/explicit-locking.html)
Received 5.6KB

● Fetch(PG source check for row-lock perms)
Received 148 bytes

Message from @web-fetch (ctrl+o to expand)

Stage 9.0–9.1: weryfikacja triggera i współbieżności, plan finalny

Nic nie zmieniałem w plikach, nie tworzyłem migracji, nie uruchamiałem Supabase i nie dotykałem bazy ani .env. Weryfikację oparłem na oficjalnej dokumentacji PostgreSQL oraz kodzie źródłowym gałęzi REL_17_STABLE. Lokalny Supabase używa PostgreSQL 17 (config.toml, major_version = 17). Raporty podagentów, którzy pobierali strony, zawierały tylko dane, a nie polecenia, więc nie wymagały żadnej reakcji.

1. Uprawnienia triggera SECURITY INVOKER: wynik

Wniosek: trigger zadziała jako authenticated przy obecnych grantach i RLS. Żadne rozszerzenie uprawnień do career_goals nie jest potrzebne.

┌─────┬──────────────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬──────────────────────┐
│  #  │           Pytanie            │                                                                     Wynik                                                                      │        Źródło        │
├─────┼──────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────┤
│ 1   │ Czy samo SELECT wystarcza do │ Nie. Locking clause wymaga dodatkowo UPDATE "for at least one column of each table so selected". Uwaga: wcześniej przypisałem to stronie       │ docs: sql-select     │
│     │  blokowania wiersza?         │ GRANT. Jest na stronie SELECT, a GRANT tego nie wspomina.                                                                                      │ (dosłowny cytat)     │
├─────┼──────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────┤
│     │ Czy UPDATE na poziomie       │ Tak. ExecCheckPermissionsModified: gdy zapytanie nie modyfikuje żadnych kolumn, wystarczy uprawnienie na dowolnej kolumnie                     │ execMain.c,          │
│ 2   │ kolumn wystarcza?            │ (pg_attribute_aclcheck_all(..., ACLMASK_ANY)), "to handle SELECT FOR UPDATE". ACL_SELECT_FOR_UPDATE jest zdefiniowane jako ACL_UPDATE.         │ parsenodes.h         │
│     │                              │                                                                                                                                                │ (dosłownie)          │
├─────┼──────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────┤
│     │ Czy dotyczy wszystkich       │                                                                                                                                                │ analyze.c            │
│ 3   │ czterech trybów (NO KEY      │ Tak, każdy ustawia ten sam bit. Ta część wynika z podsumowania analyze.c, nie z dosłownego cytatu.                                             │ [podsumowanie]       │
│     │ UPDATE, SHARE, …)?           │                                                                                                                                                │                      │
├─────┼──────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────┤
│ 4   │ Co mamy w career_goals dla   │ Tabelaryczne SELECT plus kolumnowe UPDATE (target_role, seniority, description, status) (migracja …create_career_goals.sql). Obie wymagane     │ migracja             │
│     │ authenticated?               │ rzeczy są spełnione.                                                                                                                           │ (przeczytana)        │
├─────┼──────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────┤
│     │ Jakie polityki RLS obejmują  │ Dla SELECT … FOR [KEY] UPDATE/SHARE PostgreSQL dodaje najpierw USING z polityk UPDATE, potem z polityk SELECT. Warunek to tylko ACL_UPDATE w   │ rowsecurity.c        │
│ 5   │ blokowanie?                  │ requiredPerms, więc dotyczy wszystkich czterech trybów. Tabela w dokumentacji CREATE POLICY potwierdza: "SELECT FOR UPDATE/SHARE: filter       │ (dosłownie), docs    │
│     │                              │ existing row" dla SELECT i UPDATE (USING).                                                                                                     │                      │
├─────┼──────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────┤
│     │                              │ career_goals_update_own (user_id = uid and status = 'active') działa jako filtr blokady. Cel zarchiwizowany i cel innego użytkownika są dla    │                      │
│ 6   │ Konsekwencja dla nas         │ triggera niewidoczne (0 wierszy, bez błędu). Trigger nie odróżni "zarchiwizowany" od "nie istnieje". To nam wystarcza, bo w obu przypadkach    │ wniosek z 5          │
│     │                              │ odmawia, a rozróżnienie 404/409 robi serwis przez get_by_id (polityka SELECT pokazuje właścicielowi także cele zarchiwizowane).                │                      │
├─────┼──────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────┤
│     │                              │ SECURITY INVOKER = bieżący użytkownik, czyli authenticated po SET LOCAL ROLE (backend dodatkowo sprawdza current_user). Dokumentacja pod ręką  │                      │
│ 7   │ Rola, z jaką działa funkcja  │ mówi o EXECUTE tylko przy tworzeniu triggera ("must also have EXECUTE privilege on the trigger function"). Nie ma tam stwierdzenia o           │ docs: createtrigger; │
│     │                              │ odpalaniu. Projekt już na tym polega: set_updated_at() ma REVOKE EXECUTE od authenticated, a triggery na profiles/career_goals działają przy   │  repo                │
│     │                              │ zapisach klienta.                                                                                                                              │                      │
└─────┴──────────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┴──────────────────────┘

Co zostaje nieudowodnione (a w takim razie zostanie przypięte testem, patrz sekcja 3): ponowna ocena warunków RLS po oczekiwaniu na blokadę. Dokumentacja mówi dosłownie: "The search condition of the command (the WHERE clause) is re-evaluated … the updated version of the row that is locked and returned". RLS jest dołączane do tych warunków, więc spodziewam się tego samego, ale dokumentacja tego wprost nie stwierdza. Test C3 rozstrzyga.

Brak zmian w grantach. Dodam do pgTAP asercje, że authenticated nadal nie ma tabelarycznego UPDATE ani DELETE na career_goals. Chroni to przed późniejszym "naprawieniem" problemu przez poszerzenie grantów.

2. Weryfikacja współbieżności

Zweryfikowane fakty (dokumentacja):
- Macierz konfliktów: KEY SHARE koliduje tylko z FOR UPDATE; SHARE z NO KEY UPDATE i UPDATE; NO KEY UPDATE z SHARE, NO KEY UPDATE, UPDATE; FOR UPDATE ze wszystkim. Blokady wierszy trzymane są do końca transakcji i nie blokują odczytów.
- Zwykły UPDATE bierze NO KEY UPDATE, chyba że zmienia kolumny z niepartycyjnym, niewyrażeniowym unikalnym indeksem. Po naszej migracji takie kolumny w career_goals to id i user_id (przez UNIQUE (id, user_id)), których klient nie może zmieniać. Częściowy indeks one_active_goal_per_user się nie liczy. Archiwizacja i PATCH celu to więc NO KEY UPDATE.
- Funkcja VOLATILE dostaje świeży snapshot na każde zapytanie, STABLE/IMMUTABLE używają snapshotu zapytania wywołującego. Trigger BEFORE ROW widzi zmiany wierszy już przetworzonych w tym samym poleceniu, ale tylko dla funkcji VOLATILE. Korekta: funkcja musi być jawnie volatile; dodam to do migracji i asercję volatility_is.
- W READ COMMITTED SELECT FOR UPDATE/SHARE zwraca zaktualizowaną wersję wiersza, jeśli nadal spełnia warunek, a pomija wiersz, który go nie spełnia lub został usunięty.

Fakty spoza pobranych stron (moja wiedza o PostgreSQL, do przypięcia testem): kontrola FK bierze FOR KEY SHARE na rodzicu (dokumentacja o tym tu nie mówi); przed BEFORE UPDATE trigger wiersz kompetencji jest już zablokowany; WITH CHECK dla INSERT jest sprawdzane po triggerach BEFORE. Analizę poniżej oparłem na najbardziej ostrożnych założeniach.

Analiza scenariuszy (G = wiersz celu, R = wiersz kompetencji):

┌────────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬────────────────────────────┐
│       Scenariusz       │                                                                      Przebieg                                                                       │          Werdykt           │
├────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────┤
│ Równoległe INSERT-y    │ Każdy bierze NO KEY UPDATE na G; kolejni czekają. count(*) wykonuje się po zdobyciu blokady, jako osobne zapytanie VOLATILE ze świeżym snapshotem,  │                            │
│ przy limicie           │ więc widzi commit poprzednika. Liczba aktywnych rośnie tylko przez INSERT (przywracanie zabronione), a wszystkie INSERT-y dla G są szeregowane,     │ Bezpieczne                 │
│                        │ więc 50 nie może zostać przekroczone.                                                                                                               │                            │
├────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────┤
│ INSERT vs archiwizacja │ Insert pierwszy: archiwizacja czeka, potem przechodzi; kompetencja zapisana przed zamrożeniem. Archiwizacja pierwsza: insert czeka na blokadę,      │ Bezpieczne (zakładając     │
│  celu                  │ potem blokada widzi nową wersję G (zarchiwizowaną), wiersz odpada przez filtr UPDATE-policy lub test status, więc insert dostaje 23514              │ ponowną ocenę RLS, test    │
│                        │ competencies_goal_not_active.                                                                                                                       │ C3)                        │
├────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────┤
│ UPDATE kompetencji vs  │ Edycja bierze FOR SHARE na G; archiwizacja (NO KEY UPDATE) czeka na zakończenie edycji, albo edycja czeka na archiwizację i widzi archived.         │ Bezpieczne                 │
│ archiwizacja celu      │                                                                                                                                                     │                            │
├────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────┤
│ Archiwizacja           │ Ten sam FOR SHARE na G koliduje z NO KEY UPDATE inserta, więc insert czeka na commit archiwizacji i potem liczy 49 (bez blokady dostałby fałszywe   │ Bezpieczne (i poprawia     │
│ kompetencji vs INSERT  │ odrzucenie przy 50). Bezpieczeństwo limitu od tego nie zależy: archiwizacja liczbę tylko zmniejsza.                                                 │ dokładność)                │
├────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────┤
│ Edycje równoległe      │ FOR SHARE są współdzielone, więc edycje różnych kompetencji tego samego celu się nie blokują.                                                       │ OK                         │
└────────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┴────────────────────────────┘

Deadlocki i kolejność blokad. Kolejność jest nierówna: INSERT bierze G, potem nowy wiersz; UPDATE ma już R, potem G. Przeszedłem wszystkie pary transakcji:
- Cykl wymagałby, by ktoś trzymający G czekał na coś, co trzyma czekający na G. Po zdobyciu G INSERT nie czeka na nic oprócz unikalnego indeksu nazwy, a każda zmiana w toku (rename/archiwizacja) pochodzi od transakcji z FOR SHARE na G, która nie może współistnieć z NO KEY UPDATE INSERT-a. Archiwizacja/PATCH celu nie dotyka żadnego R. Cyklu nie znalazłem.
- Pozostaje klasyczny deadlock na unikalnym indeksie nazwy między dwiema transakcjami z wieloma zapisami (np. dwie wzajemne zamiany nazw). Występuje także bez naszych triggerów. API wykonuje jedną instrukcję zapisu na transakcję, więc ten cykl nie jest osiągalny.

To jest analiza, nie dowód. Dlatego wprowadzam dwie niezmienniki do dokumentacji i testów:
1. Każde żądanie API wykonuje co najwyżej jedną instrukcję zapisu na competencies.
2. Żadna transakcja nie może zapisać career_goals, a potem competencies tego celu (kolejność G → R jest odwrócona względem triggera, co grozi cyklem z równoległą edycją). Dotyczy to przyszłego kodu (np. kaskadowego archiwizowania, etapu AI). Samo archiwizowanie celu pozostaje czystym UPDATE celu.

Czy projekt jest bezpieczny? Tak, pod warunkiem niezmienników. Zmiana modelu uprawnień nie jest potrzebna, więc żadnej nie proponuję. Gdyby test wykazał problem, kolejność opcji awaryjnych jest taka: (1) wycofanie guardu UPDATE (wariant A z D-C: zostaje tylko trigger INSERT, nie zmienia uprawnień, kosztem nieatomowej edycji vs archiwizacja), (2) SECURITY DEFINER wyłącznie za Twoją zgodą.

Koszt: każdy INSERT/UPDATE kompetencji zapisuje znacznik blokady na wierszu celu (WAL, przy wielu FOR SHARE multixact). Przy skali jednego użytkownika i jednego celu jest to pomijalne. Czekanie ogranicza statement_timeout = 15s roli skillloop_api (57014 → 504).

3. Testy

Korekta względem poprzedniego planu: WITH CHECK INSERT jest sprawdzane po triggerach BEFORE. Dlatego insert do zarchiwizowanego lub cudzego celu da 23514 z triggera, a nie 42501 z RLS. Aby przetestować bariery niezależnie, test pgTAP wyłączy trigger na czas jednej asercji (alter table … disable trigger jako właściciel w wycofywanej transakcji) i sprawdzi samą politykę. Oczekiwane kody ustalę przy pisaniu testów z faktycznego zachowania i dokumentacji, a nie dopasuję testów do wyniku bez analizy.

Możliwe w pgTAP (jedna sesja, rola authenticated przez SET LOCAL ROLE + request.jwt.claims)

- Uprawnienia triggera (punkt 1): jako authenticated SELECT … FOR NO KEY UPDATE i FOR SHARE na własnym aktywnym celu zwracają wiersz; na zarchiwizowanym i na cudzym 0 wierszy; grant UPDATE/DELETE na tabeli career_goals nadal brak.
- Wszystko z sekcji "pgTAP" poprzedniego planu, wykonywane jako authenticated: struktura, CHECK-i, izolacja A/B, brak DELETE, jednokierunkowa archiwizacja, unikalność nazwy (w tym Docker/docker/ DOCKER), zamrożenie sekwencyjne po archiwizacji celu, updated_at, kaskada.
- Limit 50 (sekwencyjnie, jako authenticated): 50 przechodzi, 51. → competencies_active_limit; archiwizacja jednej zwalnia miejsce; zarchiwizowane nie liczą się; limit per cel (cel drugiego użytkownika); wstawienie 50 jednym INSERT … SELECT generate_series (przypięcie założenia o widoczności wierszy tego samego polecenia).
- Funkcja triggera: volatility_is(..., 'volatile'), security INVOKER, search_path, brak EXECUTE dla anon/authenticated.
- Niezależne sprawdzenie polityk RLS przy wyłączonym triggerze (patrz wyżej).

Wymagają testów integracyjnych w Pythonie (wiele połączeń, rola authenticated przez prawdziwe user_transaction())

pgTAP działa w jednej sesji i nie może wywołać oczekiwania na blokadę, tak jak zaznacza komentarz w 20_career_goals.test.sql. Plik backend/tests/test_competencies_db_concurrency.py na surowym SQL, w stylu test_services_concurrency.py (wątek trzymający transakcję, dowód oczekiwania przez pg_blocking_pids, Barrier, timeouty, bez sleep() jako synchronizacji; fixtures i obserwacja z połączenia administracyjnego):

┌─────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬───────────────────────────────────────────────────────────────────────┐
│  #  │                                                         Scenariusz                                                          │                              Co dowodzi                               │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ C0  │ transaction_isolation = 'read committed' w user_transaction                                                                 │ założenie izolacji                                                    │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ C1  │ 49 aktywnych; transakcja trzyma 50. insert bez commitu; drugi czeka (udowodnione); commit → active_limit, rollback → sukces │ brak obejścia limitu                                                  │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ C1b │ N równoległych insertów przy 49 (Barrier), wiele powtórzeń                                                                  │ dokładnie 1 sukces, razem 50                                          │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ C2  │ insert trzyma blokadę; archiwizacja celu czeka; po commicie obie przechodzą w tej kolejności                                │ atomowość insert vs archiwizacja                                      │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ C3  │ archiwizacja celu bez commitu; insert czeka; po commicie competencies_goal_not_active                                       │ ponowna ocena RLS po blokadzie                                        │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ C4  │ edycja kompetencji vs archiwizacja celu, obie kolejności                                                                    │ zamrożenie jest atomowe                                               │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ C5  │ 50 aktywnych; archiwizacja jednej bez commitu; insert czeka i potem przechodzi                                              │ FOR SHARE zwalnia miejsce poprawnie                                   │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ C6  │ równoległe duplikaty nazwy                                                                                                  │ jeden sukces, reszta 23505                                            │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ C7  │ stress: wiele wątków, mieszanka insert/edycja/archiwizacja kompetencji i archiwizacji celu przez kilka sekund               │ brak 40P01, ≤ 50 aktywnych, unikalne nazwy, brak zapisu po zamrożeniu │
└─────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┴───────────────────────────────────────────────────────────────────────┘

Czego testy nie udowodnią: brak deadlocków nie da się wykazać testem, tylko sprawdzić w ograniczonym stress-teście (C7) oraz analizą z sekcji 2. Nie obniżam z tego powodu zakresu testów, ale nie będę twierdził "udowodniono brak deadlocków".

Wszystkie testy wymagają lokalnego Supabase (supabase test db, db reset, TEST_DATABASE_URL). Bez Twojej zgody (D-G poniżej) ich nie uruchamiam.

4. Finalny plan

4.1 Korekty projektu migracji

Szkic z poprzedniego raportu pozostaje, ze zmianami:
1. Funkcja triggera deklarowana jawnie jako volatile, security invoker, set search_path = ''.
2. Brak osobnego FK user_id → auth.users (zgodnie z Twoją decyzją). Kaskada: auth.users → career_goals → competencies.
3. Bez source, current_level, expectations.
4. Komentarz w migracji z niezmiennikami (jedna instrukcja zapisu, zakaz kolejności cel → kompetencje) i z opisem zachowania: trigger nie odróżnia celu zarchiwizowanego od nieistniejącego.
5. Brak zmian w grantach istniejących tabel (jedyna zmiana w career_goals to UNIQUE (id, user_id)).

4.2 Ostateczne polityki RLS i grants

alter table public.competencies enable row level security;
revoke all on public.competencies from public, anon, authenticated;
grant select on public.competencies to authenticated;
grant insert (career_goal_id, user_id, name, description, rationale, category,
              knowledge_type, importance, expected_level, display_order)
  on public.competencies to authenticated;
grant update (name, description, rationale, category, knowledge_type, importance,
              expected_level, display_order, status)
  on public.competencies to authenticated;
-- No DELETE grant, no DELETE policy.

create policy competencies_select_own on public.competencies
  for select to authenticated
  using (user_id = (select auth.uid()));

create policy competencies_insert_own on public.competencies
  for insert to authenticated
  with check (
    user_id = (select auth.uid()) and status = 'active'
    and exists (select 1 from public.career_goals g
                where g.id = career_goal_id and g.user_id = (select auth.uid())
                  and g.status = 'active'));

create policy competencies_update_own on public.competencies
  for update to authenticated
  using (
    user_id = (select auth.uid()) and status = 'active'
    and exists (select 1 from public.career_goals g
                where g.id = career_goal_id and g.user_id = (select auth.uid())
                  and g.status = 'active'))
  with check (
    user_id = (select auth.uid())
    and exists (select 1 from public.career_goals g
                where g.id = career_goal_id and g.user_id = (select auth.uid())
                  and g.status = 'active'));

Zachowanie, które trzeba uwzględnić w serwisie (9.2–9.3): przy sekwencyjnym UPDATE w zarchiwizowanym celu polityka zwraca 0 wierszy (trigger nie wchodzi), a błąd competencies_goal_not_active pojawia się tylko w wyścigu. Serwis musi obsłużyć oba przypadki i zmapować je na GOAL_ARCHIVED.

4.3 Plan dokumentacji 9.0

Jak w poprzednim raporcie (sekcje kompetencji jako Planned, aż pgTAP przejdzie), plus:
- DATABASE-DESIGN.md (v1.3): nagłówek i "Verified against"; tabela encji; 4.2 (UNIQUE (id, user_id), rozciągnięcie D13); 4.3 (tabela kolumn bez current_level i source, definicje knowledge_type, definicje poziomów, reguły); 4.7 (funkcja triggera: mechanizm blokady, volatile, INVOKER, wpływ na współbieżność, niezmienniki); 6.2 (polityki i grants, uwaga o uprawnieniach blokady wiersza); 7.1/7.2; 8.1–8.3 (kaskada obejmuje kompetencje, polityka usuwania kont nie jest zatwierdzona, nieograniczona liczba zarchiwizowanych kompetencji zaakceptowana w MVP); 10.
- API-DESIGN.md: tabela endpointów (generate jako Deferred), sekcja 7 ze schematami i kodami błędów z poprzedniego planu, reguły (limit 50, unikalność, zamrożenie).
- MVP-SCOPE.md 3.2: "Remove irrelevant competencies" → "Archive"; "Expected knowledge and skills" pokryte przez description; kategoria jako płaskie pole tekstowe.
- IMPLEMENTATION-PLAN.md Phase 2: podział na ręczny CRUD (Stage 9) i generowanie AI (kolejny etap); zadania "AI provider/prompt/generation/validation" przeniesione do drugiej części.
- Definicje knowledge_type jak w poprzednim raporcie. application opisane wprost jako wymaganie roli, które w MVP weryfikujemy tylko przez wyjaśnienia, a nie jako udowodniona biegłość praktyczna.

4.4 Plan migracji i testów 9.1

1. 9.1a: migracja supabase migration new create_competencies (nazwę nadaje CLI, to tworzy tylko plik) + 30_competencies.test.sql. Uruchomienie db reset i supabase test db po Twojej zgodzie; przy pierwszej awarii zatrzymuję się i raportuję.
2. 9.1b: test_competencies_db_concurrency.py (C0–C7). Jeśli któreś z założeń (ponowna ocena RLS, kolejność WITH CHECK, blokada R przed triggerem) zawiedzie, wracam z raportem i opcjami z sekcji 2, bez samodzielnej zmiany modelu uprawnień.
3. Aktualizacja dokumentacji do Implemented, raport, propozycja komunikatu commita (np. feat: add competencies table with RLS and active-limit trigger). Commity robisz Ty.

4.5 Ryzyka

- Nieudowodnione założenia: wymienione w sekcji 1 i 2; każde ma przypisany test.
- Niezmienniki dla przyszłego kodu (jedna instrukcja zapisu na transakcję; brak zapisu celu przed zapisem jego kompetencji). Naruszenie może dać deadlock (40P01), który PostgreSQL wykryje i przerwie jedną z transakcji, ale będzie to błąd 500 do obsłużenia.
- Wyłączenie triggera (ALTER TABLE … DISABLE TRIGGER, session_replication_role = replica) jest możliwe dla właściciela tabeli lub superusera i omija limit i zamrożenie. Nie dotyczy authenticated; dla ról uprzywilejowanych (np. postgres, service_role) trigger działa normalnie.
- lower() zależy od kolacji; nie jest pełnym case-foldingiem Unicode.
- Limit 50 to stała w funkcji; zmiana wymaga nowej migracji.
- Nieograniczona liczba zarchiwizowanych kompetencji (zaakceptowana).

5. Decyzje wymagające Twojej zgody

Poprzednie decyzje przyjąłem zgodnie z Twoją listą. Do rozstrzygnięcia zostają:
1. D-C (guard UPDATE). Czy trigger BEFORE UPDATE z FOR SHARE na celu wchodzi do 9.1 (moja rekomendacja: tak), czy tylko trigger INSERT (opcja awaryjna, bez zmian uprawnień)?
2. Niezmienniki (sekcja 2) jako wiążące zasady projektowe, zapisane w migracji i dokumentacji.
3. Wyłączanie triggera w pgTAP do niezależnego testu polityk RLS (tylko w wycofywanej transakcji testowej). Zgoda?
4. D-F. Czy reguła "kompetencje zarchiwizowanego celu są zamrożone" dostaje numer decyzji w stylu D13, czy tylko opis w dokumentacji?
5. D-G. Zgoda na uruchomienie lokalnego Supabase, db reset i supabase test db oraz testów w Pythonie (wyłącznie lokalnie, bez kosztów chmury) w 9.1.
6. Procedura przy niespełnionym założeniu: gdy test wykaże, że założenie jest nieprawdziwe, zatrzymuję się i wracam z raportem. Opcje: (a) wycofanie guardu UPDATE, (b) SECURITY DEFINER tylko za osobną zgodą. Potwierdź, że to akceptujesz.

Potwierdzenia 1–6 wystarczą, żebym ruszył z 9.0 (dokumentacja). Do tego czasu niczego nie zmieniam.


