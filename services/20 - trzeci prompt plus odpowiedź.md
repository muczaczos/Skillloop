 Realizujemy krok 2 z etapu 6.3 – Profile Service.

  Krok 1 został zaakceptowany. Pracuj zgodnie z wcześniej zatwierdzonym planem.

  Zakres zmian

  Utwórz:
  - backend/app/services/profile_service.py
  - backend/tests/test_profile_service.py

  Wymagane funkcje

  1. get_profile(pool, user: AuthenticatedUser) -> ProfileRecord
     - Otwórz transakcję przez user_transaction(pool, user).
     - Pobierz profil przez profiles.get(conn, user.id).
     - Jeśli repozytorium zwróci None, zgłoś ProfileNotFoundError.
     - W przeciwnym razie zwróć ProfileRecord.
  2. update_profile(pool, user: AuthenticatedUser, data: ProfileUpdate) -> ProfileRecord
     - Otwórz transakcję przez user_transaction(pool, user).
     - Pobierz zmiany przez data.changes().
     - Wywołaj profiles.update(conn, user.id, data.changes()).
     - Jeśli repozytorium zwróci None, zgłoś ProfileNotFoundError.
     - W przeciwnym razie zwróć ProfileRecord.

  Zasady

  - Korzystaj z istniejących modeli, schematów, repozytoriów i AppError.
  - Serwis zarządza transakcją przez user_transaction.
  - Nie dodawaj własnej obsługi ani tłumaczenia błędów bazodanowych.
  - Nie używaj FastAPI ani HTTPException.
  - Nie zmieniaj repozytoriów, migracji, endpointów ani dokumentacji.
  - Nie dodawaj zależności.
  - Nie twórz jeszcze career_goal_service.py – to będzie następny krok.
  - Nie wykonuj commita ani push.
  - Nie usuwaj ani nie modyfikuj obcego pliku services/pierwszy-prompt-plus-odpowiedź. Nie dodawaj go do zmian.

  Testy

  Napisz testy jednostkowe z mockami:
  - pobranie istniejącego profilu;
  - brak profilu i ProfileNotFoundError;
  - aktualizacja profilu;
  - poprawne przekazanie data.changes() do repozytorium;
  - brak profilu podczas aktualizacji i ProfileNotFoundError;
  - sprawdzenie, że operacje wykonują się w user_transaction.

  Sposób pracy

  1. Najpierw sprawdź aktualny stan repozytorium i istniejące interfejsy.
  2. Zaimplementuj wyłącznie powyższy zakres.
  3. Uruchom testy jednostkowe oraz Ruff.
  4. Pokaż pełną treść nowych plików i diff.
  5. Podaj wyniki testów i git status.
  6. Zatrzymaj się i poczekaj na moją akceptację. Nie rozpoczynaj kroku 3.

  Ran 3 shell commands

Krok 2 gotowy: profile_service.py i testy

Nie ruszałem repozytoriów, migracji, endpointów ani dokumentacji. Nie robiłem commita ani pusha. Obcy plik services/pierwszy-prompt-plus-odpowiedź został nietknięty.

Nowe pliki

- backend/app/services/profile_service.py
- backend/tests/test_profile_service.py

Oba są nowe i nieśledzone, więc git diff jest pusty. Pełna treść obu plików jest w poleceniu powyżej.

Implementacja

- get_profile(pool, user) otwiera user_transaction, wywołuje profiles.get(conn, user.id) i zwraca ProfileRecord. Gdy repozytorium zwróci None, rzuca ProfileNotFoundError.
- update_profile(pool, user, data) otwiera user_transaction, wywołuje profiles.update(conn, user.id, data.changes()) i zachowuje się tak samo.
- Serwis nie używa FastAPI i nie tłumaczy błędów bazodanowych.

Decyzja do sprawdzenia: ProfileNotFoundError rzucam po wyjściu z bloku user_transaction, a nie wewnątrz. Transakcja kończy się wtedy zwykłym COMMIT zamiast ROLLBACK. Dla samego odczytu None i dla UPDATE bez dopasowanego wiersza nie ma żadnej różnicy w danych. Jeśli wolisz rzucać wewnątrz bloku, żeby zawsze był rollback, to jest zmiana jednej linii.

Testy (7)

- Pobranie istniejącego profilu: zwraca ten sam rekord, repozytorium dostaje (conn, user.id), a transakcja jest otwarta dla właściwych pool i user.
- Brak profilu przy pobraniu: ProfileNotFoundError.
- Pobranie wykonuje się wewnątrz transakcji (kolejność zdarzeń: begin → get → end).
- Aktualizacja zwraca zaktualizowany rekord.
- data.changes() trafia do repozytorium: jawny null jest zmianą, pominięte pola nie są wysyłane.
- Brak profilu przy aktualizacji: ProfileNotFoundError.
- Aktualizacja wykonuje się wewnątrz transakcji.

Testy używają mocków dla user_transaction i repozytorium, więc nie potrzebują bazy.

Wyniki

- pytest tests/test_profile_service.py: 7 passed.
- Pełny pytest: 291 passed, 96 skipped. Pominięte to testy integracyjne wymagające bazy, tak jak wcześniej.
- ruff check i ruff format: czysto.

git status

?? backend/app/core/errors.py              (krok 1)
?? backend/app/services/profile_service.py
?? backend/tests/test_domain_errors.py     (krok 1)
?? backend/tests/test_profile_service.py
?? services/                               (obcy plik, nie dodawać)

Sugerowany commit (krok 2): feat: add profile service

Czekam na akceptację przed krokiem 3.

✻ Churned for 38s · done 8:49
                                                                                                                    √ Update installed · Restart to update
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
> Akceptuję krok 2, przejdź do kroku 3.