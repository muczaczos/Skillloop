Rozpoczynamy Stage 9.1a projektu SkillLoop: migracja schematu competencies i testy pgTAP.

Pracuj zgodnie z CLAUDE.md oraz dokumentacją projektu, szczególnie:
- docs/DATABASE-DESIGN.md
- docs/API-DESIGN.md
- docs/IMPLEMENTATION-PLAN.md
- docs/MVP-SCOPE.md

**Cel:**
Utworzyć migrację SQL dla tabeli `public.competencies` oraz testy pgTAP zgodne z zatwierdzonym projektem Stage 9.0.

**Zasady pracy:**

1. Najpierw przeanalizuj istniejące migracje, konwencje SQL, polityki RLS, triggery i testy pgTAP.
2. Nie zakładaj, że dokumentacja jest zgodna z aktualnym schematem. Zweryfikuj istniejące obiekty.
3. Nie zmieniaj istniejących grantów.
4. Nie używaj SECURITY DEFINER.
5. Triggery mają być SECURITY INVOKER, VOLATILE i mieć pusty search_path.
6. Zachowaj zatwierdzony projekt blokad V3 oraz kontrakt L2 dla ról uprzywilejowanych.
7. Nie dodawaj funkcjonalności odłożonych na później, np. generowania kompetencji przez AI, hierarchii czy current_level.
8. Nie zmieniaj istniejących tabel ani polityk poza zmianami niezbędnymi do realizacji zatwierdzonego projektu. Jeśli okaże się to konieczne, najpierw zgłoś problem.

**Zakres migracji:**
- tabela competencies z kolumnami i ograniczeniami opisanymi w DATABASE-DESIGN.md;
- composite FK `(career_goal_id, user_id)` do `career_goals(id, user_id)`;
- wymagany indeks UNIQUE na `career_goals(id, user_id)`, jeśli jeszcze nie istnieje;
- partial unique index dla aktywnych kompetencji;
- indeks listowania kompetencji;
- RLS i polityki zgodne z dokumentacją;
- triggery walidujące aktywny cel i limit 50 aktywnych kompetencji;
- statement-level trigger realizujący blokadę V3;
- prawidłowe zachowanie dla INSERT, UPDATE i operacji wielowierszowych.

**Testy pgTAP:**
Uwzględnij testy:
- struktury tabeli, FK, indeksów i ograniczeń;
- RLS: dostęp własnego użytkownika i brak dostępu do cudzych danych;
- INSERT tylko dla aktywnego celu;
- UPDATE tylko własnej aktywnej kompetencji pod aktywnym celem;
- brak DELETE dla klienta;
- niezmienność archiwizacji;
- limit 50 aktywnych kompetencji;
- unikalność nazw aktywnych kompetencji;
- walidację statusów i wartości enum/check;
- zachowanie triggerów przy operacjach wielowierszowych.

**Ważne:**
Na tym etapie możesz przygotować migrację i testy. Masz zgodę na operacje na lokalnym Supabase, w tym późniejszy `supabase db reset` i `supabase test db`.

Nie wykonuj żadnych operacji na zdalnym Supabase. Nie używaj `supabase db push`.

Nie czytaj pliku `.env` ani nie wypisuj sekretów.

Nie rób commitów ani pushów.

Po implementacji:
- pokaż listę zmienionych plików;
- przedstaw najważniejsze fragmenty migracji;
- wyjaśnij, jak działają oba triggery i blokady;
- podaj liczbę i zakres testów pgTAP;
- wskaż wszelkie rozbieżności z dokumentacją;
- zatrzymaj się przed uruchomieniem `supabase db reset` i `supabase test db`.

Nie rozpoczynaj Stage 9.2.



Zaczynam od weryfikacji istniejących obiektów i konwencji, a nie od dokumentacji: czytam migrację profiles (funkcja set_updated_at), pełny test 20_career_goals i resztę testu bazowego.

  Read 3 files, ran 1 shell command

Konwencje i migracje są sprawdzone: brak jeszcze UNIQUE (id, user_id) na career_goals, a set_updated_at() można użyć ponownie. Piszę teraz migrację z nazwą 20261123113335. · summary

● Write(supabase\migrations\20261123113335_create_competencies.sql)
Wrote 234 lines to supabase\migrations\20261123113335_create_competencies.sql
-- Competencies of a career goal (docs/DATABASE-DESIGN.md, sections 4.3, 4.3.1, 4.8 and 6.2.1).
--
-- A competency belongs to exactly one career goal and to the owner of that goal. There is no
-- global catalogue. Rules enforced here, in the database, not only in the application:
--   * (career_goal_id, user_id) is a composite foreign key to career_goals (id, user_id), so
--     user_id is provably the owner of the goal (and RLS needs no join);
--   * the name of an ACTIVE competency is unique within its goal, ignoring case and
--     surrounding whitespace (partial unique index);
--   * at most 50 ACTIVE competencies per goal;
--   * an archived competency is immutable and never becomes active again;
… +224 lines

  Searched for 2 patterns, read 1 file, ran 1 shell command
Read supabase\migrations\20261123113335_create_competencies.sql (234 lines)
Referenced file docs\DATABASE-DESIGN.md
Referenced file docs\IMPLEMENTATION-PLAN.md
Referenced file docs\API-DESIGN.md
Read ..\..\AppData\Local\Temp\claude\23fcb7551fca89ce\29ba69ae-3cd4-4423-9242-879f5f475876\scratchpad\apply_docs.py (235 lines)

● Write(supabase\tests\database\30_competencies.test.sql)
Wrote 527 lines to supabase\tests\database\30_competencies.test.sql
-- pgTAP tests for public.competencies (docs/DATABASE-DESIGN.md, sections 4.3, 4.3.1, 4.8, 6.2.1).
-- Stage 9.1a. Every data operation of a user runs as "authenticated" with JWT claims, exactly like
-- the backend does. Rows are created by the fixtures (postgres) only for goals and users.
-- Not covered here (needs two connections, Stage 9.1b): waiting on a goal lock, deadlock freedom,
-- re-evaluation of RLS after a lock wait.
begin;

select plan(__PLAN__);

-- Helper: runs a statement as the current role and returns the violated constraint / index name
… +517 lines

  Ran 2 shell commands

Etap 9.1a: pliki są gotowe, ale niczego nie uruchamiałem. Nie odpaliłem supabase db reset ani supabase test db, więc ani migracja, ani testy nie zostały sprawdzone na bazie. Zatrzymuję się, jak prosiłeś.

Zmienione pliki

Oba są nowe i nieśledzone w git. Nie ruszałem istniejących migracji, testów, grantów ani polityk.
- supabase/migrations/20261123113335_create_competencies.sql
- supabase/tests/database/30_competencies.test.sql

Folder Obsidian/ też jest nieśledzony. Nie tworzyłem go i go nie dotykałem.

Kluczowe fragmenty migracji

- Zmiana w istniejącej tabeli: jedyna to alter table career_goals add constraint career_goals_id_user_id_key unique (id, user_id). Jest potrzebna jako cel klucza obcego.
- Tabela competencies: klucz złożony competencies_goal_owner_fkey (career_goal_id, user_id) → career_goals(id, user_id) ON DELETE CASCADE. Do tego CHECK-i z nazwanymi ograniczeniami. Nie ma current_level, source, expectations ani hierarchii.
- Indeksy: unikalny częściowy competencies_active_name_unique na (career_goal_id, lower(btrim(name))) WHERE status='active' oraz competencies_goal_status_order_idx.
- Granty: najpierw REVOKE ALL, potem SELECT, INSERT na 10 kolumnach (bez status) i UPDATE na 9 kolumnach. Nie ma DELETE ani polityki delete.
- Polityki: competencies_select_own, competencies_insert_own, competencies_update_own (USING wymaga aktywnego celu, a status zmienia się tylko z active na archived).
- Triggery: opisane niżej. Obie funkcje są SECURITY INVOKER, jawnie VOLATILE, z search_path = '' i bez EXECUTE dla ról API. Nie ma SECURITY DEFINER.
- Nagłówek: zawiera kontrakt L2 dla postgres i service_role. Sama migracja nie wstawia żadnych kompetencji.

Jak działają triggery i blokady

1. competencies_lock_goal (statement, BEFORE INSERT OR UPDATE):
   - Odpala się przed odczytem lub zablokowaniem jakiegokolwiek wiersza kompetencji, także dla zapytań, które zmieniają 0 wierszy.
   - Wykonuje SELECT 1 FROM career_goals WHERE user_id = auth.uid() AND status='active' FOR NO KEY UPDATE.
   - Dzięki temu kolejność blokad to zawsze „cel, potem kompetencje”, więc cykl blokad nie powstaje.
   - Gdy użytkownik nie ma aktywnego celu albo nie ma auth.uid() (rola uprzywilejowana), nie robi nic i nie zgłasza błędu.
2. competencies_enforce_goal_rules (row, BEFORE INSERT OR UPDATE):
   - Ponownie blokuje cel docelowy; jeśli blokada już jest, to nic nie zmienia.
   - Jeśli celu nie widać albo jest zarchiwizowany, rzuca 23514 z constraintem competencies_goal_not_active.
   - Przy INSERT aktywnej kompetencji liczy aktywne w celu. Przy 50 lub więcej rzuca 23514 z constraintem competencies_active_limit.
   - Liczy po przejęciu blokady, więc widzi wszystkie zatwierdzone wiersze oraz wiersze z tego samego wielowierszowego INSERT.
3. Kolejność błędów:
   - Trigger wierszowy działa przed WITH CHECK polityki INSERT, więc INSERT do cudzego lub zarchiwizowanego celu daje 23514, nie 42501.
   - Gdy złamane są obie reguły, najpierw zgłaszany jest limit, a dopiero potem kolizja nazwy.
   - Test obejmuje też wariant z wyłączonym triggerem: wtedy sama polityka daje 42501.

Testy pgTAP

110 asercji, plan(110) policzone z pliku. Zakres:
- Struktura i granty (ok. 34):
  - kolumny, klucz główny, wartości domyślne, klucz złożony z kaskadą, unikat na career_goals;
  - oba indeksy, RLS, polityki, trzy triggery (rodzaj i włączenie);
  - obie funkcje (volatile, invoker, search_path, brak EXECUTE);
  - granty kolumnowe i tabelowe oraz niezmienione granty career_goals.
- RLS i INSERT:
  - własne kontra cudze dane, anon, brak DELETE;
  - blokada własnego aktywnego celu działa, a zarchiwizowanego i cudzego nie;
  - INSERT do cudzego lub zarchiwizowanego celu, podszywanie się pod innego user_id, status przy INSERT.
- Ograniczenia (14): granice 100/101, 1000/1001, 60/61, puste i białe znaki, złe wartości enum, ujemny display_order.
- Reguły nazw i archiwizacji:
  - unikalność nazwy (bez względu na wielkość liter i spacje), uwolnienie nazwy po archiwizacji, kolizja przy zmianie nazwy;
  - archiwizacja tylko w jedną stronę, brak zmiany career_goal_id i user_id, ruch updated_at.
- Limit 50: pojedyncze wiersze, wiele wierszy w jednym zapytaniu (50 przechodzi, 51. odrzuca całe zapytanie bez śladu), zwalnianie miejsc, limit osobno dla każdego celu.
- Wielowierszowe UPDATE:
  - masowe archiwizowanie i zmiana nazw, zamiana nazw w jednym zapytaniu (23505 i brak zmian);
  - UPDATE bez WHERE pomija wiersze zarchiwizowane i cudze.
- Zamrożony cel: kompetencje zostają widoczne, UPDATE zwraca 0 wierszy, INSERT daje 23514.
- Rola postgres: zapis zgodny z L2 przechodzi, a nawet bez blokady trigger egzekwuje status celu i limit.
- Kaskady: usunięcie celu oraz usunięcie użytkownika w auth.users.

Trigger instrukcji sprawdzam pośrednio. Zapytania zmieniające 0 wierszy mają zostawić niezerowy xmax na wierszu celu, co świadczy o założonej blokadzie.

Niezgodności z dokumentacją

- Żadnych merytorycznych. Dokumentacja zgadzała się ze schematem: career_goals nie miał UNIQUE (id, user_id), a set_updated_at() było do ponownego użycia.
- Przy ujednoliceniu nazw dodałem tylko stałą c_active_limit w funkcji. To wartość z projektu (50), nie zmiana.

Ryzyka, które wyjdą dopiero przy uruchomieniu

- Test używa pomocniczej funkcji pg_temp.constraint_of, wywoływanej jako authenticated. Zakładam, że ta rola może wywołać funkcję w schemacie tymczasowym. Jeśli nie, wiele asercji padnie jednocześnie, a przyczyna będzie jedna.
- Asercje na xmax i na kolejność 23514 kontra 42501 to założenia 2 i 3 z projektu. Jeśli któreś nie zadziała, zatrzymam się i zgłoszę to zamiast naginać test.
- Nie wiem, czy supabase jest na PATH w PowerShellu. W Bashu go nie było, a repo ma supabase 3.1.0 w devDependencies, więc zadziała pewnie npx supabase.
- Pełnych testów współbieżności (T1–T7) tu nie ma. To etap 9.1b.

Sugerowany commit

feat: add competencies table with goal-lock write synchronisation (stage 9.1a)

Czekam na zgodę na lokalne supabase db reset i supabase test db.