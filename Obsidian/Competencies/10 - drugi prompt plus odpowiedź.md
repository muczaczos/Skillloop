Dziękuję za audyt. Kierunek jest zaakceptowany warunkowo. Przygotuj teraz szczegółowy plan Stage 9.0–9.1, uwzględniając poniższe decyzje:

1. Kompetencje należą do konkretnego career_goal. Nie tworzymy globalnego katalogu.
2. Dodajemy user_id do competencies i składany FK do career_goals(id, user_id), wraz z odpowiednim UNIQUE na career_goals.
3. Nie dodajemy current_level. Będzie wyliczany w przyszłości z historii ocen.
4. Archiwizacja jest jednokierunkowa. Zarchiwizowany career_goal zamraża swoje kompetencje.
5. Kategoria jest zwykłym polem tekstowym. Bez hierarchii.
6. Pola description, rationale i expected_level wystarczają. Nie dodawaj osobnego expectations bez wykazania konkretnej potrzeby.
7. knowledge_type pozostaje: memory, application, documentation. Opisz dokładnie znaczenie każdego wariantu. application nie oznacza udowodnionej praktycznej biegłości.
8. Unikalność nazwy aktywnej kompetencji w ramach celu, bez rozróżniania wielkości liter.
9. Maksymalnie 50 aktywnych kompetencji na jeden career_goal. Zaproponuj, jak bezpiecznie wymusić limit na poziomie bazy danych, również przy równoczesnych żądaniach.
10. ON DELETE CASCADE pozostaje technicznie zgodne z istniejącymi tabelami, ale dokumentacja ma zaznaczać, że polityka usuwania kont nie jest jeszcze zatwierdzona.
11. Stage 9 obejmuje ręczny CRUD bez generowania AI. Endpoint zapisu całej mapy odkładamy.
12. Nie dodawaj dodatkowych funkcjonalności poza uzgodnionym zakresem.

Przygotuj:
- dokładny zakres zmian w DATABASE-DESIGN.md i API-DESIGN.md,
- projekt migracji SQL,
- polityki RLS i grants,
- plan testów pgTAP, w tym testy limitu 50 i współbieżności,
- listę endpointów dla kolejnych etapów 9.2–9.3.

Ważne:
- Nie zmieniaj plików.
- Nie twórz migracji.
- Nie wykonuj operacji na bazie.
- Nie czytaj .env.
- Nie wykonuj commitów ani pushów.

Zwróć szczególną uwagę na to, czy limit 50 można poprawnie zagwarantować bez podatności na race condition. Jeśli wymaga to triggera lub blokady rekordu rodzica, wyjaśnij mechanizm i jego wpływ na współbieżność.

Zakończ raportem i poczekaj na moją akceptację.



Zanim zaprojektuję plan testów, sprawdzę konwencje istniejących testów pgTAP i testów współbieżności.

  Searched for 2 patterns, ran 1 shell command

Stage 9.0–9.1: szczegółowy plan (bez zmian w plikach)

Nie zmieniałem plików, nie tworzyłem migracji, nie dotykałem bazy ani .env, nie robiłem commitów. Dodatkowo sprawdziłem kod pod kątem współbieżności. pool.py używa autocommit=True z jawną transakcją i nie ustawia poziomu izolacji, a config.toml też nie. Obowiązuje więc domyślny READ COMMITTED, i na nim opiera się poprawność mechanizmu z sekcji 1. Test współbieżności będzie to jawnie asertować.

1. Limit 50: czy da się go zagwarantować bez race condition

Tak, ale nie samym CHECK-iem ani samym policzeniem w serwisie. CHECK nie widzi innych wierszy, a count(*) bez blokady jest klasycznym check-then-insert. Przy READ COMMITTED dwie transakcje przy 49 aktywnych policzą 49 i obie wstawią, co daje 51.

Mechanizm: trigger BEFORE INSERT blokujący wiersz rodzica
1. Trigger robi SELECT status FROM career_goals WHERE id = NEW.career_goal_id … FOR NO KEY UPDATE. Wszystkie równoległe inserty do tego samego celu ustawiają się w kolejce na tym jednym wierszu.
2. Dopiero po zdobyciu blokady wykonuje count(*) aktywnych kompetencji celu. W READ COMMITTED każde zapytanie w plpgsql dostaje nowy snapshot, więc to zapytanie widzi już wszystko, co poprzedni właściciel blokady zatwierdził.
3. Jeśli liczba ≥ 50, rzuca wyjątek 23514 z constraint = competencies_active_limit. Blokada trzymana jest do końca transakcji, czyli do COMMIT wstawiającego. Następny w kolejce widzi już 50.

Dlaczego FOR NO KEY UPDATE, a nie FOR UPDATE: kolidowałby z FOR KEY SHARE, który bierze każdy FK wskazujący na career_goals (np. przyszłe learning_sessions). NO KEY UPDATE konfliktuje tylko z innymi zapisami do celu, a nie z kontrolami FK.

Ten sam zamek rozwiązuje drugi wyścig (zamrożenie przy archiwizacji celu). Polityka RLS z EXISTS (aktywny cel) używa snapshotu z początku zapytania. Insert, który zaczął się przed COMMIT archiwizacji celu, mógłby wstawić kompetencję do zamrożonego już celu. Trigger po zdobyciu blokady sprawdza status ponownie: SELECT … FOR UPDATE w READ COMMITTED po oczekiwaniu odczytuje najnowszą wersję wiersza i ponownie sprawdza warunki. Archiwizacja celu (zwykły UPDATE, NO KEY UPDATE) czeka na insert albo insert czeka na archiwizację. Nie ma okna wyścigu.

Trigger dla UPDATE (edycja i archiwizacja kompetencji): FOR SHARE na celu plus ponowne sprawdzenie statusu. Wiele równoległych edycji nie blokuje się nawzajem, bo FOR SHARE jest współdzielony, ale czekają na trwającą archiwizację celu, a archiwizacja czeka na nie. Dzięki temu "zarchiwizowany cel zamraża kompetencje" jest prawdą atomowo, a nie tylko "z grubsza". Licznika dla UPDATE nie trzeba, bo przywracanie jest zabronione, więc UPDATE nigdy nie zwiększa liczby aktywnych.

Wpływ na współbieżność
- Granularność: jeden wiersz celu. Użytkownik ma co najwyżej jeden aktywny cel, więc rywalizują tylko jego własne żądania (podwójne kliknięcie, dwie karty). Między użytkownikami kontencji nie ma.
- Odczyty (MVCC) nie są blokowane. Kontrole FK z innych tabel nie są blokowane (NO KEY UPDATE).
- Chwilowo czekają: PATCH celu, archiwizacja celu i inne inserty do tego samego celu. Transakcje są krótkie. Rola skillloop_api ma statement_timeout = 15s, więc oczekiwanie jest ograniczone (57014 → 504 DATABASE_TIMEOUT).
- Deadlock: kolejność blokad to INSERT: cel → nowy wiersz, UPDATE: wiersz kompetencji → cel. Przeanalizowałem to pod kątem cykli (w tym oczekiwanie na unikalnym indeksie nazwy) i cyklu nie znalazłem, ale analiza nie jest dowodem. Test obciążeniowy w 9.1 (punkt C6 w sekcji 5) ma sprawdzić, że 40P01 nie występuje. Gdyby wystąpił, przejdę na kolejność "zawsze cel najpierw" także dla UPDATE.

Alternatywy odrzucone

┌──────────────────────────────────────────────────────┬───────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                        Opcja                         │                                               Dlaczego nie                                                │
├──────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Licznik active_competency_count na celu + CHECK ≤ 50 │ Atomowy, ale denormalizacja, drift przy błędzie, więcej triggerów                                         │
├──────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ pg_advisory_xact_lock(hash(goal_id))                 │ Działa dla limitu, ale nie łączy się z blokadą archiwizacji celu, a kolizje haszy dają zbędne oczekiwanie │
├──────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ SERIALIZABLE                                         │ Wymaga ponowień (40001) w całym backendzie, nieproporcjonalne                                             │
├──────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Trigger bez blokady / count w serwisie               │ Race condition (patrz wyżej)                                                                              │
└──────────────────────────────────────────────────────┴───────────────────────────────────────────────────────────────────────────────────────────────────────────┘

Funkcja triggera jest SECURITY INVOKER (jak set_updated_at): działa z uprawnieniami authenticated, nie dodaje kodu uprzywilejowanego. SELECT … FOR NO KEY UPDATE przechodzi przez politykę UPDATE celu (status = 'active'), więc zarchiwizowany lub cudzy cel jest dla triggera niewidoczny (0 wierszy). To też daje poprawny wynik ("odmowa"), a rozróżnienie 404 od 409 robi potem serwis (jak _raise_unchanged_reason).

Do potwierdzenia testem, nie założenia: (a) że RLS + FOR UPDATE faktycznie ponownie ocenia warunki po oczekiwaniu, (b) że count(*) w triggerze widzi wiersze wstawione wcześniej tym samym INSERT … SELECT generate_series. Jeśli (a) zawiedzie, fallback: funkcja SECURITY DEFINER (search_path = '', REVOKE EXECUTE), która omija RLS i czyta rzeczywisty status. Wtedy wrócę do Ciebie, bo to zmienia profil uprawnień.

2. Projekt migracji SQL (szkic; nic nie zapisane)

Plik: supabase/migrations/<timestamp>_create_competencies.sql (nazwa przez supabase migration new). Nie edytujemy istniejących migracji.

-- Required as the target of the composite FK; makes user_id in competencies provably
-- the owner of the parent goal. Redundant with the PK on (id) but required by PostgreSQL.
alter table public.career_goals
  add constraint career_goals_id_user_id_key unique (id, user_id);

create table public.competencies (
  id              uuid primary key default gen_random_uuid(),
  career_goal_id  uuid not null,
  user_id         uuid not null,
  name            text not null,
  description     text,
  rationale       text,
  category        text,
  knowledge_type  text not null,
  importance      text not null,
  expected_level  text not null,
  display_order   integer not null default 0,
  status          text not null default 'active',
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  constraint competencies_goal_owner_fkey
    foreign key (career_goal_id, user_id)
    references public.career_goals (id, user_id) on delete cascade,
  constraint competencies_name_check
    check (char_length(btrim(name)) between 1 and 100),
  constraint competencies_description_check
    check (description is null or char_length(description) <= 1000),
  constraint competencies_rationale_check
    check (rationale is null or char_length(rationale) <= 1000),
  constraint competencies_category_check
    check (category is null or char_length(btrim(category)) between 1 and 60),
  constraint competencies_knowledge_type_check
    check (knowledge_type in ('memory', 'application', 'documentation')),
  constraint competencies_importance_check
    check (importance in ('critical', 'high', 'medium', 'low')),
  constraint competencies_expected_level_check
    check (expected_level in ('beginner', 'intermediate', 'advanced')),
  constraint competencies_display_order_check check (display_order >= 0),
  constraint competencies_status_check check (status in ('active', 'archived'))
);

create unique index competencies_active_name_unique
  on public.competencies (career_goal_id, lower(btrim(name)))
  where status = 'active';

create index competencies_goal_status_order_idx
  on public.competencies (career_goal_id, status, display_order);

Uwagi do schematu
- Brak osobnego FK user_id → auth.users. Złożony FK już gwarantuje istnienie użytkownika (przez cel), a kaskada usuwania konta idzie auth.users → career_goals → competencies. Drugi FK byłby zbędny. Łatwo go dodać, jeśli wolisz jawną symetrię z innymi tabelami.
- Brak current_level (decyzja 3). Brak source i expectations: patrz decyzje D-A i D-B w sekcji 6.
- Unikalność nazwy używa lower(), które zależy od kolacji bazy. Nie jest to pełne case-folding Unicode (np. ß). Dla nazw technologii wystarczy, ale zapisuję to w dokumentacji.
- Indeks unikalności bierze status = 'active': archiwizacja zwalnia nazwę, a zarchiwizowanych nie można przywrócić, więc nie ma konfliktu przy "reaktywacji".

Trigger i funkcja

create function public.competencies_enforce_goal_rules() returns trigger
language plpgsql set search_path = '' as $$
declare
  v_status text;
begin
  if tg_op = 'INSERT' then
    -- Serialises all inserts into the same goal; also serialises against archiving it.
    select g.status into v_status from public.career_goals g
      where g.id = new.career_goal_id and g.user_id = new.user_id
      for no key update;
    if v_status is distinct from 'active' then
      raise exception 'career goal is not active' using errcode = '23514',
        constraint = 'competencies_goal_not_active';
    end if;
    if new.status = 'active' and
       (select count(*) from public.competencies c
         where c.career_goal_id = new.career_goal_id and c.status = 'active') >= 50 then
      raise exception 'active competency limit reached' using errcode = '23514',
        constraint = 'competencies_active_limit';
    end if;
  else  -- UPDATE
    select g.status into v_status from public.career_goals g
      where g.id = old.career_goal_id for share;
    if v_status is distinct from 'active' then
      raise exception 'career goal is not active' using errcode = '23514',
        constraint = 'competencies_goal_not_active';
    end if;
  end if;
  return new;
end $$;

revoke execute on function public.competencies_enforce_goal_rules()
  from public, anon, authenticated;

create trigger competencies_enforce_goal_rules
  before insert or update on public.competencies
  for each row execute function public.competencies_enforce_goal_rules();

create trigger competencies_set_updated_at
  before update on public.competencies
  for each row execute function public.set_updated_at();

Limit 50 jest stałą w funkcji; jego zmiana wymaga nowej migracji (create or replace).

3. RLS i grants

alter table public.competencies enable row level security;
revoke all on public.competencies from public, anon, authenticated;

grant select on public.competencies to authenticated;
grant insert (career_goal_id, user_id, name, description, rationale, category,
              knowledge_type, importance, expected_level, display_order)
  on public.competencies to authenticated;            -- status: always default 'active'
grant update (name, description, rationale, category, knowledge_type, importance,
              expected_level, display_order, status)
  on public.competencies to authenticated;            -- not id, user_id, career_goal_id
-- No DELETE grant.

┌─────────────────────────┬────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────┐
│        Polityka         │      Komenda       │                                               Warunek                                               │
├─────────────────────────┼────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ competencies_select_own │ SELECT             │ user_id = (select auth.uid()) (każdy status, także w zarchiwizowanym celu)                          │
├─────────────────────────┼────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ competencies_insert_own │ INSERT, WITH CHECK │ user_id = uid and status = 'active' and exists (aktywny cel tego użytkownika o id = career_goal_id) │
├─────────────────────────┼────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ competencies_update_own │ UPDATE, USING      │ user_id = uid and status = 'active' and exists (aktywny cel)                                        │
├─────────────────────────┼────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                         │ UPDATE, WITH CHECK │ user_id = uid and exists (aktywny cel) (pozwala active → archived)                                  │
├─────────────────────────┼────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ (brak)                  │ DELETE             │ brak grantu i brak polityki                                                                         │
└─────────────────────────┴────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────┘

Co to gwarantuje:
- Nie da się przenieść kompetencji do innego celu ani użytkownika: brak grantu na career_goal_id i user_id, a dodatkowo złożony FK.
- Kompetencja zarchiwizowana jest niezmienna (USING wymaga active), więc brak ścieżki "przywróć".
- Cel zarchiwizowany zamraża dzieci: polityki INSERT i UPDATE wymagają aktywnego celu, a trigger domyka wyścig z sekcji 1. Są dwie niezależne bariery (RLS plus trigger) i jeszcze jedna (FK).
- Wiersze kompetencji w zarchiwizowanym celu zachowują status = 'active'. Nic ich nie zmienia, są po prostu tylko do odczytu, a odpowiedź API będzie to uwzględniać przez stan celu.

Baseline 00_security_baseline wymusi automatycznie RLS, brak uprawnień anon, brak TRUNCATE/REFERENCES/TRIGGER i brak wykonywalnych funkcji. Nowa funkcja z SET search_path = '' i REVOKE spełnia te reguły.

4. Zakres zmian w dokumentacji (9.0)

Zasada: sekcje o kompetencjach oznaczam Planned (zatwierdzony projekt) w 9.0, a Implemented dopiero po zielonym pgTAP w 9.1. Legenda dokumentu mówi, że "Implemented" = istnieje w migracji.

DATABASE-DESIGN.md (v1.2 → v1.3)
- Nagłówek: status, linia "Verified against" (nie twierdzę, że pgTAP był uruchomiony, dopóki nie był).
- Tabela encji (3): competencies → "Competencies belonging to a goal"; relacje: dopisek "maks. 50 aktywnych na cel".
- 4.2 career_goals: dopisek o UNIQUE (id, user_id) i o tym, że D13 obejmuje także kompetencje celu.
- 4.3 competencies: przepisana: nowa tabela kolumn zgodna z migracją; usunięcie current_level (z uzasadnieniem: wyliczane z historii ocen, nie przechowywane) i source (przeniesione do etapu AI); definicje knowledge_type (poniżej); definicje poziomów; reguły: limit 50, unikalność nazwy, archiwizacja jednokierunkowa, zamrożenie w zarchiwizowanym celu.
- 4.7: nowa funkcja triggera i jej utwardzenie (INVOKER, search_path = '', REVOKE).
- 6.2: grants i polityki competencies; uwaga o blokadzie wiersza celu i jej wpływie na współbieżność.
- 7.1 / 7.2: indeksy i ograniczenia z migracji; usunięcie wpisu "current_level restricted…".
- 8.1/8.2/8.3: wprost: kaskada obejmuje kompetencje, polityka usuwania kont nie jest zatwierdzona (decyzja otwarta nr 1 rozszerzona o competencies).
- 10: krok 4 → Implemented dopiero w 9.1; liczba asercji pgTAP.

Definicje knowledge_type (do wpisania w 4.3)

┌───────────────┬───────────────────────────────────────────────────────────────────────────────────────────────────────────────┬────────────────────────────────────────────────────────────────────────────┐
│    Wartość    │                                                   Znaczenie                                                   │                             Czego nie oznacza                              │
├───────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────┤
│               │ Wiedza, którą osoba w tej roli powinna odtwarzać z pamięci bez zaglądania do źródeł: pojęcia, terminologia,   │                                                                            │
│ memory        │ modele myślowe (np. czym jest kontener, klasy kodów HTTP). Weryfikowana pytaniami o zrozumienie i             │ Nie wymaga znajomości szczegółów składni.                                  │
│               │ wyjaśnienie.                                                                                                  │                                                                            │
├───────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────┤
│               │ Wiedza, którą rola wymaga stosować w pracy: wiedzieć, jak i kiedy użyć narzędzia lub techniki (np. dobrać     │ Nie oznacza udowodnionej praktycznej biegłości. Zgodnie z regułą 8 z       │
│ application   │ strategię wdrożenia, napisać Dockerfile). To etykieta wymagania roli, nie oceny użytkownika. W MVP            │ AI-ASSESSMENT-DESIGN system nie stwierdza kompetencji praktycznej na       │
│               │ weryfikujemy ją wyłącznie przez wyjaśnienia tekstowe lub głosowe, więc ocena może wykazać, że użytkownik      │ podstawie teorii. Ćwiczenia i wykonywanie kodu są w zakresie odroczonym.   │
│               │ potrafi wyjaśnić, jak by to zastosował.                                                                       │                                                                            │
├───────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────┤
│               │ Wiedza, której nie trzeba pamiętać, ale trzeba wiedzieć, że istnieje, gdzie jej szukać i jak ją odczytać w    │ Nie oznacza, że szczegóły można pomijać: chodzi o umiejętność ich          │
│ documentation │ oficjalnej dokumentacji (np. flagi kubectl, argumenty providera Terraform). Weryfikowana pod kątem orientacji │ znalezienia.                                                               │
│               │  i interpretacji.                                                                                             │                                                                            │
└───────────────┴───────────────────────────────────────────────────────────────────────────────────────────────────────────────┴────────────────────────────────────────────────────────────────────────────┘

API-DESIGN.md
- Tabela endpointów (linie ~111–115): generate oznaczony jako Deferred (AI stage), pozostałe cztery z numerem etapu.
- Sekcja 7: nowe schematy żądań i odpowiedzi (pola jak w tabeli, bez rationale-wymogu przy ręcznym dodawaniu, status tylko w odpowiedzi), reguły (limit 50, unikalność nazwy bez rozróżniania wielkości liter, zamrożenie przy zarchiwizowanym celu, archiwizacja jednokierunkowa), kody błędów, sekcja "AI generation: Deferred". Treść generate zostaje jako projekt przyszły.
- Uzupełnienie opisu PATCH (które pola, brak status, brak zmiany celu) i archive.

Pozostałe dokumenty (propozycja, drobne)
- MVP-SCOPE.md 3.2: "Remove irrelevant competencies" → "Archive"; "Expected knowledge and skills" pokrywa description (zgodnie z decyzją 6).
- IMPLEMENTATION-PLAN.md Phase 2: dopisek, że Phase 2 jest rozbita: ręczny CRUD (Stage 9), generowanie AI (kolejny etap).
- AI-SERVICE-DESIGN i AI-ASSESSMENT-DESIGN: bez zmian.

5. Plan testów (9.1)

pgTAP: supabase/tests/database/30_competencies.test.sql (~65 asercji; plan ustalę po napisaniu)

Konwencja: jedna wycofywana transakcja jako postgres, ścieżki użytkownika przez SET LOCAL ROLE authenticated + request.jwt.claims.

- Struktura: columns_are (dokładna lista, bez current_level/source), PK, domyślne (status, display_order), złożony FK na career_goals(id, user_id) z ON DELETE CASCADE, UNIQUE (id, user_id) na career_goals, RLS włączone, obie indeksy (definicje), obie funkcje i triggery (has_trigger), funkcja nie jest wykonywalna dla anon/authenticated, ma search_path, nie jest SECURITY DEFINER.
- CHECK-i (throws_ok 23514 + nazwa ograniczenia): puste/spacjowe name, name 101 znaków (a 100 przechodzi), description/rationale 1001 znaków, category pusta/61 znaków, złe knowledge_type/importance/expected_level (w tym not_assessed jako expected_level), display_order < 0, złe status.
- Uprawnienia: brak DELETE/TRUNCATE/tabelowych INSERT/UPDATE; kolumny insertowalne i aktualizowalne dokładnie jak w sekcji 3; anon i skillloop_api bez dostępu.
- Izolacja (A vs B): A nie widzi kompetencji B; A nie wstawi z cudzym user_id; A nie wstawi do celu B (obie bariery); A nie zaktualizuje i nie zarchiwizuje kompetencji B (0 wierszy); DELETE odrzucony (brak uprawnień).
- Własność: złożony FK blokuje user_id niezgodny z właścicielem celu (jako postgres, z ominięciem RLS); career_goal_id/user_id nie da się zmienić (brak grantu).
- Zamrożenie (D13 dla dzieci): po archiwizacji celu: insert → błąd competencies_goal_not_active; update i archiwizacja kompetencji → 0 wierszy lub błąd triggera (zależnie od tego, co pierwsze zadziała, test opisze oba); SELECT nadal działa; stan danych bez zmian.
- Archiwizacja kompetencji jednokierunkowa: active → archived działa; zmiana zarchiwizowanej (nazwa, status = 'active') → 0 wierszy; wiersz zostaje widoczny.
- Unikalność nazwy: duplikat Docker/docker/ DOCKER → 23505 z nazwą competencies_active_name_unique; ta sama nazwa w innym celu OK; po archiwizacji nazwa się zwalnia; dwie zarchiwizowane o tej samej nazwie OK; zmiana nazwy na istniejącą (UPDATE) → 23505.
- Limit 50: 50 aktywnych wstawionych jednym INSERT … SELECT generate_series przechodzi (jednocześnie potwierdza założenie (b)); 51. → 23514 z competencies_active_limit; po archiwizacji jednej 51. przechodzi; zarchiwizowane nie liczą się (50 aktywnych + 100 zarchiwizowanych → insert nadal odrzucony, bo aktywnych 50); limit jest per cel (cel B ma własne 50); INSERT wiersza z status = 'archived' jest niemożliwy dla authenticated (brak grantu), a jako postgres nie liczy się do limitu.
- Trigger updated_at: zmienia się przy UPDATE, created_at bez zmian.
- Kaskada: usunięcie użytkownika z auth.users usuwa cele i kompetencje (zaznaczone jako bieżący stan techniczny, nie polityka).
- 00_security_baseline i 10/20 nie wymagają zmian. Przy implementacji sprawdzę, czy 20_career_goals nie asercjonuje dokładnej listy ograniczeń (wtedy dodam asercję UNIQUE (id, user_id) w pliku 30, a nie edytuję starych testów bez Twojej zgody).

Testy współbieżności (osobno: pgTAP działa w jednej sesji, jak zaznacza nagłówek 20_career_goals)

Plik backend/tests/test_competencies_db_concurrency.py na surowym SQL, w stylu test_services_concurrency.py: user_transaction, wątek trzymający transakcję, dowód oczekiwania przez pg_blocking_pids, Barrier, bez sleep(). Wymaga lokalnego Supabase (TEST_DATABASE_URL). To test migracji, więc należy do 9.1, a nie do 9.2.

┌─────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬────────────────────────────────────────────────────────────────────────────────┐
│  #  │                                                     Scenariusz                                                      │                                Oczekiwany wynik                                │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
│ C0  │ Poziom izolacji sesji to read committed                                                                             │ Asercja założenia                                                              │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
│ C1  │ 49 aktywnych; transakcja trzyma 50. insert bez commitu; drugi insert czeka (dowód blokady); (a) commit → drugi      │ Zawsze ≤ 50                                                                    │
│     │ dostaje competencies_active_limit; (b) rollback → drugi przechodzi                                                  │                                                                                │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
│ C1b │ 49 aktywnych, N równoległych insertów przez Barrier                                                                 │ Dokładnie jeden sukces, reszta active_limit, łącznie 50                        │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
│ C2  │ Transakcja trzyma insert (blokada celu); archiwizacja celu czeka; po commicie archiwizacja przechodzi               │ Kompetencja zapisana, kolejne inserty odrzucone                                │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
│ C3  │ Odwrotnie: transakcja trzyma archiwizację celu bez commitu; insert czeka; po commicie                               │ Insert odrzucony competencies_goal_not_active (to dowodzi ponownej oceny po    │
│     │                                                                                                                     │ oczekiwaniu przez RLS, założenie (a))                                          │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
│ C4  │ Edycja kompetencji vs archiwizacja celu (obie kolejności)                                                           │ Edycja nigdy nie przechodzi po commicie archiwizacji                           │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
│ C5  │ 50 aktywnych; transakcja archiwizuje jedną bez commitu; insert czeka na blokadę celu; po commicie                   │ Insert przechodzi (liczba 49 po zatwierdzeniu)                                 │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
│ C6  │ Równoległe duplikaty nazwy (Barrier)                                                                                │ Jeden sukces, reszta 23505                                                     │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
│ C7  │ Ograniczony test obciążeniowy: mieszanka insertów, edycji, archiwizacji z kilku wątków przez kilka sekund           │ Brak 40P01, końcowo ≤ 50 aktywnych, unikalne nazwy                             │
└─────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┴────────────────────────────────────────────────────────────────────────────────┘

Testy wymagają uruchomienia lokalnego stosu Supabase i zastosowania migracji (db reset). Tego nie zrobię bez Twojej wyraźnej zgody (ograniczenie z poprzedniej i obecnej wiadomości). Poproszę o nią przed 9.1.

6. Endpointy dla etapów 9.2–9.3

Etap 9.2: odczyt i dodawanie

┌────────┬──────────────────────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────┬──────────────────────────────────────────────────────────┐
│ Metoda │               Ścieżka                │                                              Opis                                               │                        Odpowiedzi                        │
├────────┼──────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────┤
│ GET    │ /career-goals/{goal_id}/competencies │ Lista wszystkich kompetencji celu (aktywne i zarchiwizowane, pole status), sortowanie           │ 200; 404 GOAL_NOT_FOUND; 401/422/503/504                 │
│        │                                      │ display_order, created_at, id. Działa też dla celu zarchiwizowanego.                            │                                                          │
├────────┼──────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────┤
│        │                                      │ Dodanie jednej kompetencji. Body: name, knowledge_type, importance, expected_level (wymagane);  │ 201; 404 GOAL_NOT_FOUND; 409 GOAL_ARCHIVED; 409          │
│ POST   │ /career-goals/{goal_id}/competencies │ description, rationale, category, display_order (opcjonalne). Odrzuca nieznane pola (user_id,   │ COMPETENCY_NAME_EXISTS; 409 COMPETENCY_LIMIT_REACHED;    │
│        │                                      │ status, career_goal_id).                                                                        │ 422                                                      │
└────────┴──────────────────────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────┴──────────────────────────────────────────────────────────┘

Etap 9.3: edycja i archiwizacja

┌────────┬───────────────────────────────────────┬──────────────────────────────────────────────────────────────────────────────────────┬────────────────────────────────────────────────────────────────────┐
│ Metoda │                Ścieżka                │                                         Opis                                         │                             Odpowiedzi                             │
├────────┼───────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────┤
│ PATCH  │ /competencies/{competency_id}         │ Częściowa zmiana: name, description, rationale, category, knowledge_type,            │ 200; 404 COMPETENCY_NOT_FOUND; 409 COMPETENCY_ARCHIVED; 409        │
│        │                                       │ importance, expected_level, display_order. Bez status i bez zmiany celu.             │ GOAL_ARCHIVED; 409 COMPETENCY_NAME_EXISTS; 422                     │
├────────┼───────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────┤
│ POST   │ /competencies/{competency_id}/archive │ Archiwizacja jednokierunkowa, zwraca kompetencję.                                    │ 200; 404; 409 COMPETENCY_ARCHIVED; 409 GOAL_ARCHIVED               │
└────────┴───────────────────────────────────────┴──────────────────────────────────────────────────────────────────────────────────────┴────────────────────────────────────────────────────────────────────┘

Wzorzec błędów jak w career_goals: serwis próbuje operacji, mapuje błąd bazy po diag.constraint_name (competencies_active_name_unique → COMPETENCY_NAME_EXISTS, competencies_active_limit → COMPETENCY_LIMIT_REACHED, competencies_goal_not_active → po odczycie celu GOAL_NOT_FOUND lub GOAL_ARCHIVED), a przy 0 wierszy UPDATE ustala przyczynę dodatkowym odczytem. Cudza kompetencja jest nie do odróżnienia od nieistniejącej (404). Nowe wyjątki dochodzą do core/errors.py, a atrapa w test_allow_header.py zostaje zastąpiona prawdziwymi trasami.

Poza zakresem: GET /competencies/{id}, zapis całej mapy, generate, osobny endpoint zmiany kolejności, filtrowanie/paginacja.

Ryzyko do świadomej akceptacji: liczba zarchiwizowanych kompetencji nie jest ograniczona (limit dotyczy aktywnych), więc cykl dodaj-archiwizuj może rozdąć odpowiedź listy. To własne dane użytkownika, ryzyko niskie. Opcje: zostawić (rekomendacja dla MVP), albo później dodać filtr ?status=/paginację.

7. Podział na kroki po akceptacji

1. 9.0: edycje dokumentacji z sekcji 4 (sekcje kompetencji jako Planned).
2. 9.1a: migracja + 30_competencies.test.sql, uruchomienie pgTAP po Twojej zgodzie na operację na lokalnej bazie.
3. 9.1b: testy współbieżności (C0–C7); poprawki mechanizmu, jeśli założenia (a)/(b) zawiodą.
4. Dokumentacja → Implemented, raport i propozycja komunikatu commita (np. feat: add competencies table with RLS and active-limit trigger). Commity robisz Ty.

8. Decyzje do zatwierdzenia przed 9.0

- D-A: source (ai_generated/user_added). Proponuję pominąć w Stage 9 (wartość stała, a przy generowaniu AI i tak potrzebny będzie grant), dodać w migracji etapu AI. Alternatywa: dodać teraz z domyślnym user_added bez grantu.
- D-B: display_order. Zostaje jako zwykłe pole (domyślnie 0, ustawialne przy dodaniu i w PATCH), bez osobnego endpointu zmiany kolejności. Alternatywa: usunąć z Stage 9.
- D-C: edycja pod blokadą. Czy trigger UPDATE (FOR SHARE na celu) ma wejść (moja rekomendacja: tak, bo inaczej "zamrożenie" nie jest atomowe przy równoczesnej archiwizacji celu), czy ograniczamy się do triggera INSERT (prostsze, bez analizy deadlocków, ale edycja może "prześlizgnąć się" przez archiwizację celu).
- D-D: brak osobnego FK user_id → auth.users (złożony FK wystarcza). Zgoda?
- D-E: fallback SECURITY DEFINER, jeśli test wykaże, że RLS przeszkadza w ponownej ocenie po blokadzie. Wymaga mojego powrotu do Ciebie przed zmianą.
- D-F: czy nazywamy regułę "kompetencje zarchiwizowanego celu są zamrożone" kolejną numerowaną decyzją (jak D13), czy tylko opisujemy w dokumentacji. Wcześniejszy audyt pokazał, że D13 nie ma rejestru decyzji w repozytorium.
- D-G: zgoda na uruchomienie lokalnego stosu Supabase, supabase db reset i supabase test db w 9.1 (operacje wyłącznie lokalne, bez kosztów chmury).
- D-H: ryzyko nieograniczonej liczby zarchiwizowanych kompetencji: akceptujesz dla MVP?
- D-I: zmiany w MVP-SCOPE.md i IMPLEMENTATION-PLAN.md (sekcja 4, ostatni punkt): wchodzą do 9.0 czy pomijamy?

Czekam na akceptację lub korekty. Do tego czasu nie wprowadzam żadnych zmian.


