 Realizujemy krok 4 z etapu 6.3 – Central Error Handling.

  Kroki 1–3 zostały zaakceptowane. Najpierw przeanalizuj istniejący kod, docs/API-DESIGN.md, app/core/errors.py, konfigurację FastAPI oraz aktualne testy.

  Zakres

  Zgodnie z zatwierdzonym planem:
  - backend/app/api/errors.py – centralne handlery wyjątków HTTP i bazodanowych.
  - backend/app/core/errors.py – zmiany tylko wtedy, gdy są niezbędne do uzupełnienia uzgodnionej hierarchii AppError.
  - testy jednostkowe handlerów błędów.

  Nie implementuj endpointów ani nie zmieniaj serwisów i repozytoriów.

  Wymagania

  1. AppError i błędy domenowe muszą zachować uzgodnione statusy i kody:
     - PROFILE_NOT_FOUND – 404
     - GOAL_NOT_FOUND – 404
     - NO_ACTIVE_GOAL – 404
     - GOAL_ARCHIVED – 409
     - ACTIVE_GOAL_EXISTS – 409
  2. Zaimplementuj centralną obsługę AppError oraz błędów psycopg.
  3. Odpowiedź błędu musi odpowiadać formatowi D11 z docs/API-DESIGN.md, zawierać request_id i bezpieczny komunikat. Nie wymyślaj nowego formatu – odtwórz dokładnie uzgodniony kontrakt z dokumentacji.
  4. Mapowanie błędów bazodanowych:

     - 23505 – 500 INTERNAL_ERROR. Wyjątek: konflikt one_active_goal_per_user powinien być już zamieniony przez create_goal() na ActiveGoalExistsError. Handler globalny nie może ponownie mapować tego constraintu.
     - 23503 na career_goals_user_id_fkey – 401 UNAUTHORIZED.
     - Pozostałe 23503 – 409 CONFLICT.
     - 23514 i 23502 – 500 INTERNAL_ERROR.
     - 42501 – 500 INTERNAL_ERROR oraz log na poziomie ERROR. Nie zwracaj 403.
     - Przewidywalne błędy danych z klasy 22xxx – 422. Nieprzewidziane błędy z tej klasy – 500.
     - 57014 – 504 DATABASE_TIMEOUT.
     - DatabaseUnavailableError – 503 SERVICE_UNAVAILABLE.
     - Pozostałe nieobsłużone błędy psycopg – 500 INTERNAL_ERROR.

     Rozpoznawaj constrainty dokładnie po nazwie, jeśli jest dostępna. Nie maskuj błędów programistycznych jako błędów klienta.
  5. Obsługa 401 musi zachować nagłówek WWW-Authenticate: Bearer.
  6. Logowanie:
     - błędy 5xx loguj na poziomie ERROR;
     - błędy klienta nie powinny generować niepotrzebnych logów ERROR;
     - nie ujawniaj klientowi SQL, treści wyjątków psycopg, danych połączenia ani szczegółów wewnętrznych.
  7. Nie przechwytuj BaseException. Nie maskuj błędów programistycznych.

  Testy

  Dodaj testy dla:
  - każdego rodzaju mapowania wymienionego powyżej;
  - nieznanych constraintów;
  - braku constraint_name;
  - DatabaseUnavailableError;
  - AppError;
  - formatu odpowiedzi D11 i obecności request_id;
  - nagłówka WWW-Authenticate dla 401;
  - poziomu logowania dla błędów 4xx i 5xx;
  - bezpiecznych komunikatów, bez ujawniania szczegółów psycopg.

  Ograniczenia

  - Nie dodawaj endpointów.
  - Nie zmieniaj repozytoriów, migracji, serwisów ani dokumentacji.
  - Nie dodawaj zależności.
  - Nie zmieniaj mapowania UniqueViolation w career_goal_service.py.
  - Nie wykonuj commita ani pusha.
  - Nie modyfikuj obcego pliku services/pierwszy-prompt-plus-odpowiedź.

  Sposób pracy

  1. Najpierw pokaż, jak rozumiesz kontrakt D11 i aktualną architekturę obsługi błędów.
  2. Zaimplementuj wyłącznie krok 4.
  3. Uruchom nowe testy, pełne testy jednostkowe i Ruff.
  4. Pokaż pełną treść zmienionych i nowych plików, diff oraz wyniki.
  5. Pokaż git status, oddzielając pliki z kroków 1–4 od obcego pliku.
  6. Zatrzymaj się i poczekaj na moją akceptację. Nie przechodź do testów współbieżności z kroku 5.

  Listed 1 directory, ran 2 shell commands

● Write(backend\app\api\errors.py)
Wrote 136 lines to backend\app\api\errors.py
"""Central translation of exceptions into API error responses.

Response body (docs/API-DESIGN.md, section 13):

    {"error": {"code": "...", "message": "...", "request_id": "<uuid>"}}

`details` is only used for request validation errors and is not produced here.

What is handled:
- AppError (app/core/errors.py): business errors raised by services; status and code come
… +126 lines

● Write(backend\tests\test_api_errors.py)
Wrote 274 lines to backend\tests\test_api_errors.py
import logging
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import errors as pg_errors
… +265 lines

Piszę handlery i testy do kroku 4. Teraz je uruchamiam.

  Ran 2 shell commands

Krok 4 gotowy: centralna obsługa błędów, ale handlery nie są jeszcze podpięte do aplikacji

Nie zmieniałem serwisów, repozytoriów, migracji ani dokumentacji, a core/errors.py zostało bez zmian. Nie robiłem commita ani pusha. Obcy plik services/pierwszy-prompt-plus-odpowiedź jest nietknięty. Dwa nowe pliki:
- backend/app/api/errors.py
- backend/tests/test_api_errors.py

Pełna treść pierwszego jest w wywołaniu Write powyżej. Drugi zmienił się po automatycznym formatowaniu Ruffa i po mojej poprawce ostatniego testu. git diff jest pusty, bo oba pliki są nowe i nieśledzone.

Kontrakt D11

W docs/ nie ma żadnego oznaczenia „D11” (grep nic nie znalazł). Jedyny kontrakt błędu to sekcja 13 w API-DESIGN.md. Odtworzyłem go dokładnie:

{"error": {"code": "...", "message": "...", "request_id": "<uuid>"}}

Pole details pomijam. Dokument przewiduje je tylko dla błędów walidacji żądania (VALIDATION_ERROR), które nie wchodzą w ten krok. Jeśli D11 miał inną treść niż sekcja 13, daj znać.

Mapowanie

- AppError używa swojego status_code i code.
- DatabaseUnavailableError daje 503 SERVICE_UNAVAILABLE.
- psycopg.Error, po SQLSTATE i dokładnej nazwie constraintu:

┌────────────────────────────────────────────────────────────────────────────────────────────────────────┬────────────────────────────────────────────┐
│                                               Przypadek                                                │                 Odpowiedź                  │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────┤
│ 23503 z career_goals_user_id_fkey                                                                      │ 401 UNAUTHORIZED z WWW-Authenticate:       │
│                                                                                                        │ Bearer                                     │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────┤
│ inne 23503                                                                                             │ 409 CONFLICT                               │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────┤
│ 22001, 22003, 22021                                                                                    │ 422 VALIDATION_ERROR                       │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────┤
│ 57014                                                                                                  │ 504 DATABASE_TIMEOUT                       │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────┤
│ 23505 (każdy, także one_active_goal_per_user), 23514, 23502, 42501, pozostałe 22xxx i inne błędy       │ 500 INTERNAL_ERROR                         │
│ psycopg                                                                                                │                                            │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┴────────────────────────────────────────────┘

Kody 500 i 504 oraz 42501 są logowane na poziomie ERROR. Wyjątki psycopg wyłapuję jako psycopg.Error, a nie BaseException. Błąd programistyczny, np. RuntimeError, nie jest maskowany. Test potwierdza, że dostaje goły 500 Starlette.

Decyzje do Twojej akceptacji

1. Lista „przewidywalnych” błędów 22xxx. Wybrałem tylko 22001 (za długi tekst), 22003 (liczba poza zakresem) i 22021 (znak spoza kodowania, np. NUL). Pozostałe, np. 22P02 czy 22012, dają 500, bo wskazują na nasz błąd. Rozszerzenie listy to jedna linia.
2. request_id jest generowany na każdą odpowiedź błędu. W projekcie nie ma middleware z request id. Trafia do body i do linii logu, co je łączy. Nie dodawałem middleware ani przyjmowania nagłówka od klienta.
3. Logowanie.
   - AppError 4xx idzie na INFO, błędy bazy 4xx na WARNING, wszystko 5xx na ERROR.
   - Loguję tylko klasę wyjątku, SQLSTATE i nazwę constraintu, bez str(exc) i bez traceback, bo komunikaty psycopg mogą zawierać wartości wierszy.
   - Wyjątek: DatabaseUnavailableError loguje str(exc), które z założenia zawiera tylko nazwę klasy i SQLSTATE.
4. Handlery nie są podpięte do app/main.py. Udostępniam register_error_handlers(app), ale jej nie wywołałem. Podpięcie to zmiana main.py poza zakresem kroku. Zmieniłoby też istniejące odpowiedzi: main.py ma już handler DatabaseUnavailableError zwracający {"detail": ...}, a test_auth_dependency.py, test_db_errors.py i test_lifespan.py asertują ten format. Do podjęcia decyzji:
   - Czy podpiąć w osobnym kroku i przy okazji zaktualizować te testy?
   - Czy przenieść też HTTPException z deps.py (401 i 503 z {"detail"}) na D11?

   Do czasu tej decyzji handlery działają tylko w testach, na osobnej aplikacji.

Testy (55)

- Wszystkie pięć kodów AppError.
- Każdy wiersz tabeli mapowania, plus unknown constraint i brak constraint_name.
- Dokładne dopasowanie nazwy constraintu (near-missy dają 409).
- Format D11 i UUID request_id, inny przy każdym żądaniu.
- WWW-Authenticate tylko w 401.
- Poziomy logowania dla 4xx i 5xx.
- Brak szczegółów psycopg w odpowiedzi i w logach.

Wyniki

- test_api_errors.py: 55 passed.
- Pełny pytest: 362 passed, 96 skipped. Pominięte to testy integracyjne wymagające bazy, tak jak wcześniej.
- ruff check i ruff format: czysto.

git status

Kroki 1–4 (nieśledzone):
?? backend/app/core/errors.py                    (krok 1)
?? backend/tests/test_domain_errors.py           (krok 1)
?? backend/app/services/profile_service.py       (krok 2)
?? backend/tests/test_profile_service.py         (krok 2)
?? backend/app/services/career_goal_service.py   (krok 3)
?? backend/tests/test_career_goal_service.py     (krok 3)
?? backend/app/api/errors.py                     (krok 4)
?? backend/tests/test_api_errors.py              (krok 4)

Obcy plik (nie dodawać do commita):
?? services/

Sugerowany commit (krok 4): feat: add central API error handlers

Czekam na akceptację i decyzję w sprawie podpięcia do main.py. Do kroku 5 nie przechodzę.

✻ Brewed for 1m 40s · done 9:14
                                                                                                                    √ Update installed · Restart to update
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
> Akceptuję krok 4, przejdź do kroku 5
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  ⏸ manual mode on · ? for shortcuts