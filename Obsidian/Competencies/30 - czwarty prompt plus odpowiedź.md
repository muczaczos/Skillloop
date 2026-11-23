Przeanalizuj ponownie deadlocki w projekcie Stage 9.1. Znalazłem potencjalny scenariusz, którego nie uwzględniłeś:

1. Transakcja A wykonuje UPDATE kompetencji i blokuje wiersz R.
2. Transakcja B wykonuje INSERT i blokuje wiersz celu G przez FOR NO KEY UPDATE.
3. Transakcja A próbuje zablokować G przez FOR SHARE i czeka na B.
4. Transakcja B próbuje wstawić nazwę kolidującą z aktualnie aktualizowanym wierszem R i czeka na A podczas kontroli unikalności.

W ten sposób może powstać cykl R → G → R, mimo że każda transakcja wykonuje tylko jedną instrukcję zapisu.

Sprawdź ten scenariusz na poziomie mechaniki PostgreSQL 17, uwzględniając indeks częściowy UNIQUE, kontrolę unikalności, blokady wierszy i kolejność wykonywania triggerów.

Następnie:

- Przeanalizuj wszystkie możliwe konflikty INSERT / UPDATE / archive kompetencji / archive celu.
- Zaproponuj wariant, który nie tworzy takiego cyklu.
- Oceń, czy można osiągnąć to bez SECURITY DEFINER i bez rozszerzania grantów.
- Jeżeli rozwiązanie wymaga zmiany kolejności blokad, opisz dokładnie, jakie są konsekwencje dla atomowości limitu 50 i zamrażania kompetencji po archiwizacji celu.
- Zaproponuj konkretne testy integracyjne odtwarzające powyższy deadlock, nie tylko ogólny stress test.

Nie zmieniaj żadnych plików, migracji, dokumentacji ani bazy. Nie uruchamiaj Supabase. To ma być wyłącznie analiza i poprawiony projekt techniczny.

Na końcu przedstaw rekomendowany wariant oraz jego ograniczenia. Nie zakładaj, że obecna analiza deadlocków jest poprawna.



Zanim ocenię Twój scenariusz, sprawdzę w kodzie PostgreSQL 17 trzy rzeczy, od których on zależy: czy blokada wiersza zakładana przez trigger BEFORE UPDATE jest "lock-only" i czy czeka na nią kontrola unikalności. Sprawdzę też, w jakiej kolejności wykonują się triggery, heap_update i wstawienie do indeksu. Poprzedniej analizie nie ufam, więc czytam źródła.

● Fetch(PG17 unique check vs lock-only xmax)
Received 148 bytes

● Fetch(PG17 BEFORE UPDATE trigger lock order)
Received 148 bytes

Message from @web-fetch (ctrl+o to expand)

Message from @web-fetch (ctrl+o to expand)

Stage 9.1: ponowna analiza deadlocków i poprawiony projekt

Nic nie zmieniałem w plikach, migracjach, dokumentacji ani bazie i nie uruchamiałem Supabase. Mechanikę sprawdziłem w źródłach PostgreSQL 17 (REL_17_STABLE). Raporty podagentów były danymi, nie poleceniami. Luki w ich lekturze zaznaczam w sekcji 1.

Wynik:
- Scenariusz R → G → R z Twojej wiadomości nie może wystąpić w obecnym projekcie. B dostaje wtedy natychmiast 23505, a A kończy się normalnie.
- Przy okazji znalazłem inny, realny deadlock w obecnym projekcie, który wcześniej wykluczyłem błędnie.
- Moje stwierdzenie "jedna instrukcja zapisu na transakcję wyklucza deadlock na indeksie unikalnym" było nieprawdziwe i je wycofuję.

1. Zweryfikowana mechanika PostgreSQL 17

┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬─────────────────────────────────┬───────────┐
│                                                                             Fakt                                                                             │             Źródło              │  Status   │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼───────────┤
│ UPDATE: trigger BEFORE ROW jest wywoływany po zablokowaniu wiersza (table_tuple_lock), a przed heap_update.                                                  │ trigger.c,                      │ dosłownie │
│                                                                                                                                                              │ ExecBRUpdateTriggersNew         │           │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼───────────┤
│ Kolejność ExecUpdate: trigger BR → WCO_RLS_UPDATE_CHECK i constraints → table_tuple_update → ExecInsertIndexTuples (kontrola unikalności) → triggery AFTER.  │ nodeModifyTable.c               │ dosłownie │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼───────────┤
│ Kolejność ExecInsert: trigger BR → WCO_RLS_INSERT_CHECK → constraints → table_tuple_insert → ExecInsertIndexTuples → AFTER (czyli FK).                       │ nodeModifyTable.c               │ dosłownie │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼───────────┤
│ Triggery BEFORE STATEMENT odpalają się przed pobraniem pierwszego wiersza z subplanu.                                                                        │ ExecModifyTable                 │ dosłownie │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼───────────┤
│ Blokada wiersza daje xmax z flagą HEAP_XMAX_LOCK_ONLY. Dla multixact flaga jest ustawiana, gdy nie ma aktualizującego.                                       │ heapam.c                        │ dosłownie │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼───────────┤
│ HeapTupleSatisfiesDirty dla lock-only xmax zwraca true i nie ustawia snapshot->xmax. Dla rzeczywistego, trwającego UPDATE/DELETE ustawia xmax na xid         │ heapam_visibility.c             │ dosłownie │
│ aktualizującego. Dla trwającego INSERT ustawia xmin.                                                                                                         │                                 │           │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼───────────┤
│ _bt_check_unique czeka (XactLockTableWait) tylko gdy SnapshotDirty.xmin lub .xmax jest ważne. Jeśli duplikat jest żywy i bez takiego xid, rzuca natychmiast  │ nbtinsert.c                     │ dosłownie │
│ unique_violation.                                                                                                                                            │                                 │           │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼───────────┤
│ Kontrola FK bierze FOR KEY SHARE na wierszu rodzica, jako właściciel tabeli rodzica, z SECURITY_NOFORCE_RLS.                                                 │ ri_triggers.c                   │ dosłownie │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┴─────────────────────────────────┴───────────┘

Wniosek łączący (wnioskowanie z dwóch dosłownych cytatów): wiersz zablokowany przez trigger (lock-only) nie powoduje oczekiwania w kontroli unikalności. Czeka się dopiero na wiersz, który ktoś już faktycznie zmodyfikował heap_update.

Nieprzeczytane: ciało ExecUpdateLockMode (tryb blokady UPDATE: NoKeyExclusive czy Exclusive), ciało ExecInsertIndexTuples, ustawianie HOT/TU_None. Wiem z wiedzy ogólnej, że zmiana nazwy (kolumna indeksu) wyłącza HOT i daje wstawienie do indeksu, ale nie jest to tu zweryfikowane. Test z sekcji 6 ma to pokazać.

2. Twój scenariusz krok po kroku

1. A: UPDATE competencies … WHERE id = R. ExecBRUpdateTriggersNew blokuje R przed heap_update. R ma teraz xmax = A oznaczony lock-only, a nowej wersji wiersza ani wpisów w indeksach jeszcze nie ma.
2. B: INSERT. Trigger BR bierze FOR NO KEY UPDATE na G.
3. A: trigger robi SELECT … FOR SHARE na G i czeka na B. A nadal nie wykonał heap_update.
4. B: wychodzi z triggera (WCO, constraints, table_tuple_insert) i wchodzi do _bt_check_unique. Znajduje wpis R (aktywny, ta sama nazwa). Heap tuple R ma xmax = A z lock-only, więc snapshot->xmax zostaje nieważne i B nie czeka. Dostaje natychmiast 23505.
5. B się wycofuje, zwalnia G, A kontynuuje.

Cykl wymagałby, żeby w kroku 4 A miał rzeczywistą modyfikację R (xmax nie-lock-only). A może ją wykonać dopiero po przejściu własnego triggera, czyli po zdobyciu G w FOR SHARE, a ten koliduje z NO KEY UPDATE trzymanym przez B. Obie rzeczy naraz nie mogą być prawdziwe. Wynik "B dostaje 23505, mimo że A zaraz zmieni nazwę" jest poprawny: odpowiada kolejności B przed A, w której R jeszcze istniał.

Granica tego wniosku: opiera się na kolejności "trigger przed heap_update". Gdyby ktoś wyłączył trigger lub pracował jako rola z session_replication_role = replica, przesłanki znikają. Nie dotyczy to authenticated.

3. Co znalazłem poza Twoim scenariuszem

3.1. Realny deadlock w obecnym projekcie (wymiana nazw). Dwie edycje trzymają FOR SHARE na G jednocześnie (współdzielone, więc obie przechodzą trigger). Kolejność w ExecUpdate: heap_update (stary wiersz dostaje rzeczywisty xmax) → dopiero potem kontrola unikalności. Przy R1 = a, R2 = b:
- A: UPDATE R1 SET name='b' i C: UPDATE R2 SET name='a' oba wykonują heap_update,
- kontrola A widzi xmax = C na starym R2 i czeka na C; kontrola C widzi xmax = A na starym R1 i czeka na A.

Każda transakcja ma jedną instrukcję, więc mój poprzedni niezmiennik jej nie wykluczał. Okno czasowe jest bardzo wąskie, a w kolejności szeregowej obie operacje zakończyłyby się 23505, więc skutkiem byłby 40P01 zamiast 23505 dla jednej z nich. Problem istnieje niezależnie od naszych triggerów, ale FOR SHARE go nie zapobiega, bo nie szereguje zapisów.

3.2. Inwersja kolejności blokad (R → G w UPDATE, G → R gdzie indziej). Pomyłka z poprzedniego raportu: nie rozważyłem trzech źródeł odwróconej kolejności:
- Instrukcja wielowierszowa (np. przez Data API: UPDATE … WHERE career_goal_id = …). Po pierwszym wierszu trzyma G i próbuje drugiego R, które trzyma lock-only inna edycja czekająca na G. W wariancie z FOR SHARE G jest współdzielone, więc cykl nie powstaje. W wariancie z wyłącznym G (V1 niżej) by powstał.
- Usunięcie konta (kaskada): G (FOR UPDATE) → kasowanie R, a równoległa edycja trzyma R i czeka na G.
- Przyszły kod, który aktualizuje cel, a potem jego kompetencje.

Pierwsze dotyczy tylko własnych danych użytkownika (self-DoS przez Data API), drugie jest operacją administracyjną.

4. Macierz konfliktów (obecny projekt V0)

Oznaczenia: I = INSERT kompetencji, E = edycja (w tym zmiana nazwy), K = archiwizacja kompetencji, P = zmiana lub archiwizacja celu. Wiersz R zablokowany lock-only, G = wiersz celu.

┌──────────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────┬──────────────────────────────────────────────────────────────────────────────────────┐
│           Para           │                                        Mechanizm                                        │                                      Deadlock?                                       │
├──────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────┤
│ I–I                      │ oba biorą NO KEY UPDATE na G, szeregowo                                                 │ nie                                                                                  │
├──────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────┤
│ I–E, I–K                 │ I trzyma G; E/K czekają na G, trzymając tylko lock-only na R (I go nie widzi jako       │ nie (Twój scenariusz)                                                                │
│                          │ blokady). E/K nigdy nie modyfikują przed zdobyciem G.                                   │                                                                                      │
├──────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────┤
│ I–P, E–P, K–P            │ P bierze G; nie dotyka R                                                                │ nie                                                                                  │
├──────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────┤
│ E–E, E–K (różne wiersze) │ G współdzielone, więc oba modyfikują naraz. Czekania tylko w kontroli unikalności.      │ tak, tylko E–E przy wzajemnej wymianie nazw (3.1). K nie wstawia do indeksu          │
│                          │                                                                                         │ unikalnego (częściowy predykat fałszywy), więc nie czeka                             │
├──────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────┤
│ E–E, K–K, E–K (ten sam   │ drugi czeka na blokadę R przed triggerem; pierwszy przechodzi dalej                     │ nie                                                                                  │
│ wiersz)                  │                                                                                         │                                                                                      │
├──────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────┤
│ (dowolne)–kaskada konta  │ G → R kontra R → G                                                                      │ tak (3.2), tylko admin                                                               │
└──────────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────┴──────────────────────────────────────────────────────────────────────────────────────┘

5. Warianty poprawek

┌──────────────────┬───────────────────────────────────────────────────────────────────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────┐
│     Wariant      │                                       Opis                                        │                                                Wynik                                                │
├──────────────────┼───────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ V0 (obecny)      │ I: NKU na G; E/K: R → SHARE na G                                                  │ Deadlock wymiany nazw (3.1), kaskada (3.2)                                                          │
├──────────────────┼───────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ V1               │ w UPDATE FOR NO KEY UPDATE zamiast FOR SHARE                                      │ Usuwa 3.1 (modyfikuje tylko jeden pisarz naraz, więc brak oczekiwań na xmax innych). Wprowadza cykl │
│                  │                                                                                   │  dla instrukcji wielowierszowych (3.2). Dla instrukcji jednowierszowych acykliczne                  │
├──────────────────┼───────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ V2               │ V1 + serwis najpierw blokuje G                                                    │ Wymusza G → R tylko dla ścieżki backendu; Data API dalej R → G                                      │
├──────────────────┼───────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ V4               │ tylko trigger INSERT, bez guardu UPDATE                                           │ brak R → G, ale 3.1 zostaje i zamrożenie edycji nie jest atomowe                                    │
├──────────────────┼───────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ V3               │ V1 + trigger statement-level BEFORE INSERT OR UPDATE, który blokuje aktywny cel   │ Kolejność G → R dla każdej instrukcji (jedno- i wielowierszowej), także przez Data API              │
│ (rekomendowany)  │ zalogowanego użytkownika zanim jakikolwiek wiersz zostanie dotknięty              │                                                                                                     │
└──────────────────┴───────────────────────────────────────────────────────────────────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────┘

Dlaczego V3 jest bezcyklowy z konstrukcji: wszyscy pisarze authenticated zdobywają G (wyłącznie) jako pierwszą rzecz. Po jego zdobyciu pisarz nie czeka na nic trzymane przez czekających, bo nikt inny nie ma otwartych modyfikacji na tym celu ani blokad R (te dostaje się dopiero po G). To muteks per cel. Kontrole FK z przyszłych tabel biorą KEY SHARE na R, co nie koliduje z NO KEY UPDATE.

Szkic V3 (nie zapisany nigdzie):
create function public.competencies_lock_active_goal() returns trigger
language plpgsql volatile set search_path = '' as $$
begin
  perform 1 from public.career_goals g
   where g.user_id = (select auth.uid()) and g.status = 'active'
   for no key update;          -- at most one row: one_active_goal_per_user
  return null;
end $$;
revoke execute on function public.competencies_lock_active_goal()
  from public, anon, authenticated;
create trigger competencies_lock_goal before insert or update
  on public.competencies for each statement
  execute function public.competencies_lock_active_goal();
Trigger wierszowy zostaje (autorytatywne: ponowne sprawdzenie statusu i limit), ale w gałęzi UPDATE FOR SHARE zamieniam na FOR NO KEY UPDATE. Blokada ponowna w tej samej transakcji jest no-opem.

Bez SECURITY DEFINER i bez rozszerzania grantów? Tak. Trigger statement-level wykonuje to samo zapytanie co wierszowy (zweryfikowane w poprzednim raporcie: UPDATE na kolumnie plus polityka UPDATE USING, które authenticated już ma). auth.uid() jest już wywoływane przez polityki jako authenticated. Wymaga to potwierdzenia testem, nie zakładam.

Konsekwencje dla atomowości:
- Limit 50: mechanizm bez zmian. count(*) nadal wykonuje się po zdobyciu blokady jako świeże zapytanie VOLATILE; blokada jest zdobywana wcześniej (początek instrukcji), więc okno wyścigu się nie powiększa. Wstawienie wielu wierszy jedną instrukcją przy 49: pierwszy przechodzi, drugi nie, cała instrukcja się wycofuje.
- Zamrożenie po archiwizacji celu: nie słabnie. Wszystkie zapisy są szeregowane z P (obie strony biorą G wyłącznie). Instrukcja, która czekała na blokadę, ma przestarzały snapshot (warunki RLS na EXISTS celu mogą przejść na starym stanie), ale trigger wierszowy sprawdza status po zdobyciu blokady, więc archiwizacja zatwierdzona w międzyczasie jest widoczna. Zachowanie przy przestarzałych wersjach wiersza R to standardowe ponowne odczytanie w READ COMMITTED (TM_Updated → ponowna blokada → ponowny trigger BR).
- Czego to kosztuje: wszystkie zapisy kompetencji jednego celu są szeregowane (pomijalne dla jednego użytkownika z jednym celem). Nawet instrukcja zmieniająca zero wierszy bierze blokadę celu. Znika FOR SHARE, więc nie ma multixactów.

Granice V3:
1. Dotyczy tylko authenticated. Rola uprzywilejowana (postgres, service_role) nie ma auth.uid(), więc blokady statement-level nie bierze i działa w kolejności R → G. Zasada: każdy zapis uprzywilejowany do competencies musi najpierw sam zablokować cel. Dla kaskady usunięcia konta (G → R) cyklu z authenticated nie ma, bo authenticated czeka na G, zanim dotknie R.
2. Opiera się na regule "co najwyżej jeden aktywny cel" (one_active_goal_per_user) i na tym, że zapisy kompetencji są dozwolone tylko w aktywnym celu.
3. Dwie funkcje i dwa triggery zamiast jednej. To większa złożoność niż V1. Jeśli wolisz mniej elementów, V1 jest akceptowalnym minimum: usuwa udowodniony deadlock 3.1, a cykl 3.2 wymaga instrukcji wielowierszowej przez Data API i uderza tylko w własne żądania użytkownika.

6. Testy odtwarzające deadlocki (integracyjne, Python, wiele połączeń)

Wszystkie jako authenticated przez user_transaction(), z fixtures i obserwacją z połączenia administracyjnego. Synchronizacja przez wątek trzymający transakcję i dowód oczekiwania (pg_blocking_pids, wait_event_type = 'Lock'), nigdy przez sleep(). Błąd psycopg.errors.DeadlockDetected (40P01) jest asercją porażki. Po deadlocku PostgreSQL potrzebuje do detekcji ok. 1 s (deadlock_timeout), więc każdy test ma limit czasu.

Oczekiwania podaję dla V0, a dla V3 (rekomendowany). Testy piszę pod finalny projekt, ale dzięki temu każdy z nich rozróżnia warianty.

T1: dokładnie Twój scenariusz
1. H (transakcja pomocnicza): SELECT … FROM career_goals WHERE id = G FOR NO KEY UPDATE, trzymana. To odtwarza stan "B po triggerze".
2. A: UPDATE competencies SET name = 'x' WHERE id = R (R ma nazwę docker). Dowód, że A czeka (blokada G od H). Opcjonalnie dla V0: xmax wiersza R = xid A (lock-only).
3. H wykonuje INSERT o nazwie docker.
4. Oczekiwane: natychmiast 23505 (bez oczekiwania na A), bez 40P01. Po rollbacku H, A kończy sukcesem, R ma nazwę x.
- T1b: H wstawia nazwę niekolidującą: A czeka, H commit, A kończy sukcesem.

T2: wymiana nazw, deterministycznie (wiele instrukcji, odtwarza mechanizm)
R1=a, R2=b, R3=c.
1. T1: UPDATE R1 SET name='z' (trzymane).
2. T2: UPDATE R2 SET name='a'.
   - V0: T2 przechodzi trigger (G współdzielone), robi heap_update i czeka w kontroli unikalności na T1 (R1 ma xmax = T1).
   - V3: T2 czeka na G już na początku instrukcji (T1 go trzyma).
3. T1: UPDATE R3 SET name='b'.
   - V0: czeka na T2 (R2 ma xmax = T2) → 40P01.
   - V3: R2 jeszcze nie zmodyfikowany, więc natychmiastowe 23505; po jego rollbacku T2 dostaje 23505 (R1 nadal a). Wynik zbioru: {23505, 23505}, nigdy 40P01.

T3: wymiana nazw, jedna instrukcja na transakcję (best-effort, probabilistycznie)
Dwa wątki z Barrier, a↔b, setki powtórzeń, każdy raz od świeżego stanu. V0 może dać 40P01; V3 nie. Test nie jest deterministyczny (okno jest wąskie), więc jest uzupełnieniem T2, nie jego zamiennikiem.

T4: instrukcja wielowierszowa kontra edycja pojedyncza (cykl V1, rozwiązany w V3)
R1, R2 w kolejności fizycznej (test asercjonuje kolejność ctid).
1. H trzyma G (jak w T1).
2. Z (pierwszy): UPDATE competencies SET display_order = display_order + 1 WHERE career_goal_id = G (obejmuje R1, R2).
3. X (drugi): UPDATE … WHERE id = R2.
4. H commit. Kolejka blokady jest FIFO: G dostaje Z.
   - V1: Z robi R1, potem czeka na R2 zablokowane przez X, a X czeka na G od Z → 40P01.
   - V3: oba czekały na G na początku instrukcji, nikt nie trzymał R → Z kończy, potem X. Brak 40P01.
     (Kolejność FIFO jest założeniem testu; test sprawdza ją przez pg_blocking_pids, a jeśli się nie sprawdza, kończy się jako błąd przygotowania, nie jako zielony.)

T5: kaskada usunięcia konta (G → R) kontra edycja
1. Z (admin): SELECT … FROM career_goals WHERE id = G FOR UPDATE (trzymane, emulacja kolejności kaskady).
2. U: UPDATE competencies … WHERE id = R.
3. Z: DELETE FROM competencies WHERE career_goal_id = G.
   - V0/V1: U trzyma R, czeka na G; Z czeka na R → 40P01.
   - V3: U czeka na G już w trigger statement-level i nie trzyma R, więc Z kończy.

T6: macierz parami (I, E, K, P) × (I, E, K, P), obie kolejności, na wzorcu "pierwsza operacja trzyma transakcję". Asercje: w V3 druga zawsze czeka (udowodnione), wynik po commicie zgodny z semantyką szeregową (limit, unikalność, zamrożenie), brak 40P01.

T7: stres końcowy (mieszanka I/E/K/P, wiele wątków, kilka sekund): brak 40P01, ≤ 50 aktywnych, unikalne nazwy, brak zapisu po zatwierdzeniu archiwizacji celu.

Czego testy nie udowodnią: brak deadlocków w ogólności. T2, T4, T5 są deterministyczne dla konkretnych cykli, które znam. Poza nimi zostaje analiza z sekcji 3–5.

pgTAP zweryfikuje tylko metadane (istnienie obu triggerów, volatile, INVOKER, REVOKE EXECUTE) i zachowanie sekwencyjne jako authenticated.

7. Rekomendacja i ograniczenia

Rekomendowany wariant: V3 (blokada celu na poziomie instrukcji + FOR NO KEY UPDATE we wszystkich operacjach zapisu). Bez SECURITY DEFINER, bez zmian grantów. Limit 50 i zamrożenie zachowują atomowość (sekcja 5).

Ograniczenia i ryzyka:
1. Zasada dla ról uprzywilejowanych: najpierw blokada celu, potem zapis kompetencji.
2. Zasada dla przyszłego kodu: żadnej transakcji, która zapisze cel, a potem jego kompetencje.
3. Przypięcie testami (nie zakładam): ponowna ocena RLS po oczekiwaniu na blokadę, auth.uid() w triggerze statement-level, FIFO kolejki blokady w T4, brak HOT przy zmianie nazwy.
4. Backend powinien mapować 40P01 (i 55P03/57014) na błąd ponawialny, bo klasa deadlocków poza zakresem naszej wiedzy nie jest zerowa. To zmiana w 9.2, nie w migracji.
5. Całkowite szeregowanie zapisów per cel: pomijalne w MVP, do przemyślenia przy kilku jednoczesnych celach (odroczone).

Decyzje, których potrzebuję:
1. V3 (rekomendowany) czy V1 (mniej elementów, ale zostaje cykl wielowierszowy przez Data API).
2. Zasady dla ról uprzywilejowanych i przyszłego kodu zapisane w dokumentacji i komentarzu migracji.
3. Testy T1–T7 jako część 9.1b (uruchomienie wymaga lokalnego Supabase i Twojej zgody, jak wcześniej).
4. Mapowanie 40P01 w serwisie na błąd ponawialny w 9.2.

Do czasu Twojej decyzji niczego nie zmieniam.

Cogitated for 7m 16s · done 10:10
