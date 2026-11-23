Zatwierdzam poprawkę helpera `pg_temp.constraint_of` w pliku `supabase/tests/database/30_competencies.test.sql`.

Zakres:
1. Zmień:
   `coalesce(v_name, sqlstate)`
   na:
   `coalesce(nullif(v_name, ''), sqlstate)`.
2. Nie zmieniaj migracji ani żadnych innych testów.
3. Nie zmieniaj `plan(112)` ani liczby asercji.
4. Uruchom ponownie wyłącznie:
   `supabase test db`

Ograniczenia:
- Wyłącznie lokalny Supabase.
- Bez `supabase db reset`, `db push` i operacji na zdalnym projekcie.
- Nie czytaj `.env`.
- Nie zmieniaj grantów ani schematu.
- Nie wykonuj commitów ani pushów.
- Nie rozpoczynaj Stage 9.1b ani 9.2.

Po wykonaniu przedstaw:
- dokładną zmianę w helperze;
- wynik wszystkich plików testowych;
- łączną liczbę PASS, FAIL i błędów wykonania;
- potwierdzenie, czy pięć wcześniej nieudanych asercji teraz przechodzi;
- końcowy status PASS albo NOT PASS.

Jeżeli pojawią się kolejne błędy, zatrzymaj się i przedstaw diagnozę bez samodzielnego wprowadzania następnych poprawek.



 Update(supabase\tests\database\30_competencies.test.sql)
Added 1 line, removed 1 line
  return 'no error';
exception when others then
  get stacked diagnostics v_name = constraint_name;
  return coalesce(v_name, sqlstate);
  return coalesce(nullif(v_name, ''), sqlstate);
end;
$$;


  Ran 1 shell command

Stage 9.1a: PASS

Wszystkie pliki testowe przechodzą, 218 z 218 asercji (0 FAIL, 0 błędów wykonania). Pięć wcześniej nieudanych asercji (36, 37, 39, 70, 71) przechodzi.

Zmiana w helperze

W supabase/tests/database/30_competencies.test.sql, w pg_temp.constraint_of:
-  return coalesce(v_name, sqlstate);
+  return coalesce(nullif(v_name, ''), sqlstate);
To jedyna zmiana. Migracji, innych testów, grantów ani schematu nie ruszałem, a plan(112) zostaje.

Wynik supabase test db

┌───────────────────────────────┬──────────────┐
│             Plik              │    Wynik     │
├───────────────────────────────┼──────────────┤
│ 00_security_baseline.test.sql │ ok           │
├───────────────────────────────┼──────────────┤
│ 10_profiles.test.sql          │ ok           │
├───────────────────────────────┼──────────────┤
│ 20_career_goals.test.sql      │ ok           │
├───────────────────────────────┼──────────────┤
│ 30_competencies.test.sql      │ ok (112/112) │
└───────────────────────────────┴──────────────┘

- Łącznie: 218 asercji, wszystkie PASS (poprzednio 213 PASS i 5 FAIL).
- Błędy wykonania: 0.
- Ostrzeżenia: tylko NOTICE: extension "pgtap" already exists, skipping (nieszkodliwe) i informacja o nowszej wersji CLI (3.3.0).
- Pięć wcześniej nieudanych asercji przeszło, a więc 42501 było zwracane poprawnie, a błędny był tylko helper, który dla błędów bez constraintu zwracał ''.

Zakres operacji

- Wszystko poszło na lokalny stack (Target: local stack (127.0.0.1:54322)).
- Nie uruchamiałem db reset, db push ani niczego na zdalnym projekcie. Nie czytałem .env.
- Nie robiłem commitów ani pushów. Stage 9.1b i 9.2 nie zostały rozpoczęte.

Dwa założenia z projektu zostały potwierdzone przez przejście testów: kolejność błędów (23514 przed 42501) i to, że authenticated może blokować własny aktywny cel bez zmiany grantów. Test blokady przez xmax dowodzi tylko, że jakaś blokada została założona. Jej siłę sprawdzi dopiero 9.1b.

Sugerowany commit:
feat: add competencies table with goal-lock write synchronisation (stage 9.1a)


