Realizujemy krok 3 z etapu 6.3 – Career Goal Service.                             
  Kroki 1 i 2 zostały zaakceptowane. Przed rozpoczęciem sprawdź aktualny stanrepozytorium, istniejące interfejsy i zatwierdzony plan.

  Zakres zmian

  Utwórz:
  - backend/app/services/career_goal_service.py
  - backend/tests/test_career_goal_service.py

  Nie zmieniaj innych plików.

  Wymagane funkcje

  1. create_goal(pool, user, data) -> CareerGoalRecord
     - Otwórz user_transaction.
     - Wywołaj career_goals.insert() bez wcześniejszego sprawdzania, czy aktywny cel
  już istnieje.
     - Jeśli wystąpi psycopg.errors.UniqueViolation z constraintem
  one_active_goal_per_user, po wyjściu z transakcji zgłoś ActiveGoalExistsError.
     - Inne naruszenia UNIQUE przekazuj bez zmian.
     - Nie wykonuj check-then-insert. Ochronę przed równoległymi insertami zapewnia
  indeks UNIQUE w bazie.
  2. get_active_goal(pool, user) -> CareerGoalRecord
     - Otwórz user_transaction.
     - Wywołaj career_goals.get_active().
     - Jeśli wynik to None, zgłoś NoActiveGoalError.
     - W przeciwnym razie zwróć rekord.
  3. update_goal(pool, user, goal_id, data) -> CareerGoalRecord
     - Otwórz user_transaction.
     - Najpierw wywołaj career_goals.update_active().
     - Jeśli zwróci rekord, zwróć go.
     - Jeśli zwróci None, wywołaj career_goals.get_by_id() i sklasyfikuj sytuację:
       - brak rekordu (również rekord innego użytkownika ukryty przez RLS) →
  GoalNotFoundError;
       - rekord zarchiwizowany → GoalArchivedError;
       - rekord nadal aktywny, ale aktualizacja nie zwróciła wiersza → nieoczekiwany
  błąd wewnętrzny, np. RuntimeError.
     - Zmiany pobierz przez data.changes().
  4. archive_goal(pool, user, goal_id) -> CareerGoalRecord
     - Otwórz user_transaction.                                                         - Wywołaj career_goals.archive_active().
     - Jeśli zwróci rekord, zwróć go.                              
     - Jeśli zwróci None, użyj get_by_id() i zastosuj tę samą klasyfikację co w
  update_goal():
       - brak rekordu → GoalNotFoundError;
       - rekord zarchiwizowany → GoalArchivedError;
       - rekord nadal aktywny → nieoczekiwany błąd wewnętrzny.

  Ważne zasady

  - Używaj istniejących modeli, schematów, repozytoriów, user_transaction i klas z
  app/core/errors.py.
  - GoalArchivedError ma kod GOAL_ARCHIVED.
  - NoActiveGoalError ma kod NO_ACTIVE_GOAL.
  - Nie używaj FastAPI ani HTTPException.
  - Nie tłumacz globalnie błędów bazodanowych.
  - Nie dodawaj pre-checków, które mogłyby powodować TOCTOU.
  - Nie zmieniaj repozytoriów, migracji, endpointów, dokumentacji ani zależności.
  - Serwisy nie mogą wywoływać innych serwisów ani otwierać zagnieżdżonych           transakcji.
  - Nie dodawaj jeszcze testów współbieżności — będą w osobnym krok- Nie wykonuj commita ani pusha.
  - Nie modyfikuj ani nie dodawaj do zmian obcego pliku
  services/pierwszy-prompt-plus-odpowiedź.

  Testy jednostkowe

  Uwzględnij co najmniej:
  - utworzenie celu;
  - mapowanie właściwego constraintu UNIQUE na ActiveGoalExistsError;
  - przekazanie innych błędów UNIQUE bez zmian;
  - brak aktywnego celu i NoActiveGoalError;
  - aktualizację aktywnego celu;
  - klasyfikację braku celu i celu zarchiwizowanego podczas aktualizacji;
  - nieoczekiwany przypadek, gdy cel nadal jest aktywny, ale aktualizacja nie
  zwróciła rekordu;
  - archiwizację aktywnego celu;
  - klasyfikację braku celu i celu już zarchiwizowanego podczas archiwizacji;        - wykonanie operacji w ramach user_transaction;
  - sprawdzenie, że tłumaczenie błędu UNIQUE następuje po wyjściu z(rollback musi zakończyć się przed zgłoszeniem ActiveGoalExistsError).

  Sposób pracy                                                                      
  1. Najpierw przeanalizuj istniejące interfejsy repozytoriów, modeli i schematów.
  2. Zaimplementuj wyłącznie powyższy zakres.
  3. Uruchom testy nowego serwisu, pełny zestaw testów jednostkowych i Ruff.
  4. Pokaż pełną treść nowych plików, diff i wyniki testów.
  5. Pokaż git status, wyraźnie oddzielając pliki z kroków 1–3 od obcego pliku.
  6. Zatrzymaj się i poczekaj na moją akceptację. Nie rozpoczynaj kroku 4.

  Ran 4 shell commands

Nic nie zostało zapisane (błąd cytowania w shellu), więc zapisuję pliki narzędziem
Write.

● Write(app\services\career_goal_service.py)
Wrote 89 lines to app\services\career_goal_service.py
"""Business logic for career goals.

Each function owns one user_transaction(), so the repository calls run
as the authenticated
user and Row Level Security applies. Services never call other services
.

Concurrency: nothing is checked before it is written (no check-then-ins
ert). The partial
unique index one_active_goal_per_user and the `status = 'active'` condi
… +83 lines (~3 KB)

● Write(tests\test_career_goal_service.py)
Wrote 283 lines to tests\test_career_goal_service.py
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime                                           from typing import Any
from unittest.mock import MagicMock                        
from uuid import uuid4

import pytest
from psycopg.errors import UniqueViolation
… +274 lines

  Ran 1 shell command

Krok 3 gotowy: career_goal_service.py i testy

Nie zmieniałem żadnych istniejących plików. Nie robiłem commita ani pusha. Obcy plik
services/pierwszy-prompt-plus-odpowiedź został nietknięty. Pierwsza próba zapisu   plików nie powiodła się z powodu błędu cytowania w shellu i nic wtedy nie powstało.
Pliki zapisałem potem narzędziem Write.                          
Nowe pliki (nieśledzone, więc git diff jest pusty)
                                                                                  - backend/app/services/career_goal_service.py
- backend/tests/test_career_goal_service.py

Pełna treść obu plików jest w wywołaniach Write powyżej.

Implementacja

- create_goal:
  - Wywołuje career_goals.insert() w user_transaction, bez pre-checku.
  - except UniqueViolation jest poza blokiem with, więc w chwili mapowania rollback
    już się zakończył.
  - Constraint one_active_goal_per_user daje ActiveGoalExistsError (z from exc).
    Inne naruszenia UNIQUE wychodzą bez zmian.
- get_active_goal: zwraca rekord albo rzuca NoActiveGoalError.
- update_goal i archive_goal: najpierw update_active() lub archive_active(). Dopiero
  gdy wynik to None, wspólny helper _raise_unchanged_reason wywołuje get_by_id() i
  klasyfikuje przypadek:
  - brak rekordu (w tym cel innego użytkownika ukryty przez RLS) →
    GoalNotFoundError;
  - rekord zarchiwizowany → GoalArchivedError;
  - rekord nadal aktywny → RuntimeError.                                           - Serwisy nie wywołują innych serwisów, nie otwierają zagnieżdżonych transakcji i
  nie używają FastAPI.                                           
Decyzja do sprawdzenia: w update_goal i archive_goal klasyfikację (get_by_id() i
wyjątki) robię wewnątrz bloku user_transaction, w tej samej transakcji co UPDATE. Dzięki temu odczyt statusu jest spójny z poprzedzającym UPDATE, a wyjątek kończy
transakcję rollbackiem. Mapowanie UniqueViolation w create_goal jest natomiast po
wyjściu z transakcji, zgodnie z decyzją 2.

Testy (16)

- Utworzenie celu:
  - zwrot rekordu i argumenty repozytorium;
  - brak pre-checku (get_active i get_by_id nie są wywoływane).
- Mapowanie UNIQUE:
  - one_active_goal_per_user → ActiveGoalExistsError, z oryginalnym __cause__;
  - inny constraint lub None przechodzi bez zmian (parametryzowany).
- Kolejność transakcji: transakcja zobaczyła surowy UniqueViolation i zrobiła
  rollback, zanim powstał ActiveGoalExistsError.
- get_active_goal: zwrot rekordu oraz NoActiveGoalError.
- update_goal:
  - sukces z poprawnym data.changes() (jawny null jest zmianą);
  - brak celu → GoalNotFoundError;
  - zarchiwizowany → GoalArchivedError;
  - nadal aktywny → RuntimeError.
- archive_goal: sukces oraz te same trzy klasyfikacje.
- Transakcja: każda operacja przechodzi przez user_transaction z właściwymi pool i user. Kolejność zdarzeń (begin, wywołania repozytorium, commit lub rollback) jest sprawdzana w testach.

diag.constraint_name jest w testach podmieniane przez podklasę UniqueViolation, bo psycopg buduje diag z odpowiedzi serwera. Realny constraint sprawdza już istniejący test integracyjny repozytorium, a pełny przepływ z bazą pokryje krok 5.

Wyniki

- test_career_goal_service.py: 16 passed.
- Pełny pytest: 307 passed, 96 skipped. Pominięte to testy integracyjne wymagające bazy, tak jak wcześniej.
- ruff check i ruff format: czysto.

git status

Kroki 1–3 (nieśledzone):
?? backend/app/core/errors.py                    (krok 1)
?? backend/tests/test_domain_errors.py           (krok 1)
?? backend/app/services/profile_service.py       (krok 2)
?? backend/tests/test_profile_service.py         (krok 2)
?? backend/app/services/career_goal_service.py   (krok 3)
?? backend/tests/test_career_goal_service.py     (krok 3)

Obcy plik (nie dodawać do commita):
?? services/

Sugerowany commit (krok 3): feat: add career goal service

Czekam na akceptację przed krokiem 4.