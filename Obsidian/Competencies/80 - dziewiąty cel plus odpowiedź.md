Przed uruchomieniem migracji wykonaj niezależny przegląd kodu Stage 9.1a.

Nie zmieniaj żadnych plików. Nie uruchamiaj Supabase, migracji ani testów wymagających bazy. Nie czytaj .env.

Przeanalizuj dokładnie oba nowe pliki:
- supabase/migrations/20261123113335_create_competencies.sql
- supabase/tests/database/30_competencies.test.sql

Sprawdź szczególnie:

1. Czy rola authenticated ma wszystkie uprawnienia wymagane do wykonania SELECT FOR NO KEY UPDATE na career_goals, bez zmiany istniejących grantów.

2. Czy statement-level trigger rzeczywiście blokuje aktywny cel przed rozpoczęciem operacji na wierszach competencies.

3. Czy row-level trigger poprawnie egzekwuje aktywny cel, limit 50 oraz reguły dla operacji wielowierszowych.

4. Czy polityki RLS i triggery nie powodują nieoczekiwanej kolejności błędów ani wycieku informacji o cudzych celach.

5. Czy REVOKE/GRANT nie zmienia przypadkowo istniejących uprawnień innych tabel lub ról.

6. Czy funkcje triggerów mają poprawne SECURITY INVOKER, VOLATILE, search_path='' i uprawnienia EXECUTE.

7. Czy pg_temp.constraint_of może być wywoływana w kontekście authenticated i czy testy poprawnie przełączają role oraz JWT claims.

8. Czy asercje wykorzystujące xmax rzeczywiście dowodzą uzyskania oczekiwanej blokady. Jeśli nie, zaproponuj bardziej wiarygodny test.

9. Czy plan(110) odpowiada dokładnie liczbie asercji.

10. Czy testy kaskady i usuwania użytkownika są zgodne z rzeczywistymi FK i istniejącymi triggerami.

Dla każdego problemu podaj:
- poziom: krytyczny / istotny / drobny;
- konkretny plik i fragment;
- dlaczego jest problemem;
- proponowane rozwiązanie.

Nie poprawiaj kodu. Na końcu wydaj jednoznaczną rekomendację: READY FOR LOCAL RESET albo NOT READY FOR LOCAL RESET.


Searched for 5 patterns, read 2 files

Przegląd Stage 9.1a: NOT READY FOR LOCAL RESET

Przeczytałem oba pliki i istniejące migracje oraz testy. Niczego nie zmieniałem ani nie uruchamiałem. Poniższe wnioski opieram na analizie kodu i na źródłach PostgreSQL, więc żaden z nich nie został sprawdzony na bazie.

Migracja wygląda poprawnie i nie znalazłem w niej błędów krytycznych. Plik testów ma jeden błąd krytyczny, który wywróciłby cały skrypt.

Krytyczne

K1. 30_competencies.test.sql: modyfikujące CTE wewnątrz podzapytania

- Gdzie: 8 asercji o postaci select is((with u as (update ... returning 1) select count(*)::int from u), N, ...). To linie ok. 271, 355, 360, 409, 432, 443, 447 i 466 (testy B, archiwizacji, D i C).
- Dlaczego to problem: PostgreSQL odrzuca WITH z UPDATE poza najwyższym poziomem zapytania. Wyrażenie (with u as (update …) …) jest podzapytaniem skalarnym, więc kończy się błędem 0A000: WITH clause containing a data-modifying statement must be at the top level. Błąd przerywa transakcję testową, a wszystkie kolejne asercje padają z current transaction is aborted. Wynik supabase test db byłby przez to bezużyteczny.
- Rozwiązanie: dodać drugi helper obok constraint_of, na przykład pg_temp.rows_affected(sql text) returns integer. Wykonuje on execute p_sql, a potem get diagnostics v = row_count. Asercje przyjmą postać select is(pg_temp.rows_affected($$update ...$$), 3, '...').
- Efekt uboczny: helper wykonuje też UPDATE, więc nie trzeba nic zmieniać w liczbie asercji. plan(110) zostaje bez zmian.

Istotne

I1. Asercje xmax nie dowodzą trybu blokady (punkt 8)

- Gdzie: testy dla użytkowników E i F ("zero-row UPDATE/INSERT still locks the active goal").
- Co dowodzą: że ktoś postawił jakąkolwiek blokadę na wierszu celu. Wartość xmax jest taka sama dla FOR KEY SHARE, FOR SHARE i FOR NO KEY UPDATE. Test pozostałby zielony, gdyby statement trigger blokował słabiej, niż zakłada projekt V3.
- Co jest wiarygodne: fałszywe trafienie z innego źródła jest mało prawdopodobne. Dla zapytania z 0 wierszy nic poza statement triggerem nie dotyka celu E ani F.
- Rozwiązanie A (w pgTAP): pageinspect (heap_page_items) albo pgrowlocks. Trzeba sprawdzić bity t_infomask: LOCK_ONLY 0x0080 i EXCL_LOCK 0x0040 oraz brak HEAP_KEYS_UPDATED 0x2000 w t_infomask2. Wymaga to create extension w teście, więc najpierw trzeba potwierdzić, że lokalny obraz Supabase ma te rozszerzenia.
- Rozwiązanie B (rekomendowane): uznać xmax za test pomocniczy, a dowód trybu przenieść do 9.1b. Druga sesja robi SELECT … FROM career_goals WHERE id=… FOR SHARE NOWAIT i oczekuje 55P03. Dodatkowo FOR KEY SHARE NOWAIT musi przejść, bo NO KEY UPDATE z nim nie koliduje. To jest bezpośredni dowód siły blokady.
- Opis asercji: zmieniłbym ją na „some lock was taken”, żeby nie obiecywała więcej, niż sprawdza.

Drobne

- D1. Helper w pg_temp (punkt 7).
  - Co zadziała: wywołanie jako authenticated powinno działać, bo domyślne uprawnienia z migracji create_api_role dotyczą tylko schematu public. Funkcja w pg_temp ma więc domyślne EXECUTE dla PUBLIC. Dostęp do schematu tymczasowego wymaga tylko uprawnienia TEMP na bazie, które domyślnie ma PUBLIC.
  - Czego nie sprawdziłem: czy lokalny Supabase nie cofa TEMP. Jeśli cofa, padną wszystkie asercje z constraint_of. To trzeba potwierdzić przy pierwszym uruchomieniu.
- D2. Asercje kaskady (punkt 10). Test nie sprawdza liczby wierszy przed usunięciem. Wynik 0 mógłby być pusty, gdyby fixture nie powstał. Dodałbym asercję „przed” albo użył ok z >0 (zmiana planu o +2).
- D3. Komentarz nagłówka testu. Pisze, że fixtures tworzą tylko cele i użytkowników, a kompetencja dla C jest wstawiana jako postgres, zgodnie z L2. Komentarz wymaga drobnej korekty.
- D4. Komunikat triggera. Dla nieistniejącego lub cudzego celu mówi „career goal is not active”. Bezpieczne, bo komunikat jest identyczny, więc nic nie wycieka. Ale backend 9.2 musi dopiero odczytem rozróżnić „nie istnieje” i „zarchiwizowany”.
- D5. Wielowierszowy INSERT na dwa cele. Brak testu INSERT, który w jednej instrukcji obejmuje aktywny i zarchiwizowany cel. Oczekiwany wynik to odrzucenie całości. Można dodać do 9.1b.

Odpowiedzi na punkty 1–10

1. Uprawnienia do FOR NO KEY UPDATE: tak.
   - authenticated ma UPDATE na 4 kolumnach career_goals i SELECT na tabeli. Dokumentacja SELECT wymaga UPDATE na co najmniej jednej kolumnie.
   - Blokujący odczyt filtruje politykę UPDATE (user_id=uid AND status='active'). Własny aktywny cel jest blokowalny, a zarchiwizowany i cudzy dają 0 wierszy.
   - Granty nie wymagają zmian. Dowodzą tego 3 asercje w teście.
2. Statement trigger blokuje przed operacją na wierszach: tak.
   - Wyzwalacze BEFORE STATEMENT odpalają się przed pobraniem pierwszego wiersza, także przy 0 wierszy.
   - Zapytanie w funkcji VOLATILE dostaje świeży snapshot, więc po oczekiwaniu na blokadę widzi późniejsze zatwierdzenia. Snapshot samej instrukcji UPDATE jest starszy, dlatego trigger wierszowy ponownie sprawdza status celu.
3. Trigger wierszowy: tak.
   - Blokada, sprawdzenie aktywnego celu i limit są poprawne.
   - Limit liczy wiersze z tej samej instrukcji, bo VOLATILE plpgsql podnosi licznik poleceń przed każdym zapytaniem.
   - Liczby w testach D się zgadzają: 47+3=50 przechodzi, a czwarty wiersz kończy się odrzuceniem.
4. Kolejność błędów i wycieki: poprawnie.
   - BEFORE ROW działa przed WCO polityki INSERT, więc dla cudzego, nieistniejącego i zarchiwizowanego celu błąd jest ten sam (23514 competencies_goal_not_active). To nie ujawnia istnienia cudzych celów.
   - Indeks unikalny i klucz obcy są osiągalne tylko po przejściu triggera, czyli tylko dla własnego celu.
5. REVOKE/GRANT: bez wpływu na inne obiekty.
   - Dotyczą wyłącznie public.competencies.
   - ALTER TABLE career_goals ADD CONSTRAINT ... UNIQUE dodaje constraint i indeks, nie zmienia grantów ani polityk.
6. Funkcje triggerów: zgodnie z wymaganiami.
   - SECURITY INVOKER (domyślnie), jawne VOLATILE, search_path = '', wszystkie odwołania z kwalifikowanymi schematami (public.*, auth.uid()).
   - REVOKE EXECUTE dla public, anon i authenticated jest w migracji, a baseline pilnuje też skillloop_api. EXECUTE nie jest sprawdzane w chwili odpalenia triggera, tylko przy CREATE TRIGGER.
7. Przełączanie ról i claims: poprawne.
   - SET LOCAL ROLE z roli anon do authenticated jest dozwolone, bo sprawdza się użytkownika sesji.
   - set_config('request.jwt.claims','',true) daje auth.uid() = NULL.
   - Zastrzeżenia są w D1.
8. Asercje xmax: patrz I1.
9. plan(110): zgadza się.
   - W pliku jest 124 linie zaczynające się od select .
   - Odejmuję 14 linii nieasercyjnych (plan, finish, set_config, dwa blokujące select 1) i zostaje 110.
   - Niezależny grep na nazwach funkcji pgTAP też daje 110.
10. Kaskady: zgodne z FK.
    - Triggerów DELETE nie ma, a kaskada przez klucz złożony i auth.users działa jak w 20_career_goals.
    - Zastrzeżenie jest w D2.

Rekomendacja

NOT READY FOR LOCAL RESET. Przed uruchomieniem trzeba naprawić K1, bo bez tego skrypt przerwie się na pierwszym zapytaniu z CTE. I1 i D2 można poprawić w tej samej rundzie albo odłożyć do 9.1b. Gdy potwierdzisz zakres poprawek, naniosę je w pliku testów. Migracji nie trzeba zmieniać.