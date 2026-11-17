 Akceptuję plan etapu 6.3 z następującymi ustaleniami:

  1. Akceptuję decyzję 1: w update_goal() najpierw wykonujemy update_active(), a get_by_id() tylko wtedy, gdy repozytorium zwróci None. Zachowujemy klasyfikację 404 / 409 / 500.
  2. Akceptuję decyzję 2: mapowanie UniqueViolation dla constraint one_active_goal_per_user odbywa się w create_goal(), poza blokiem user_transaction(). W etapie 6.4 centralny handler nie może ponownie mapować tego samego błędu. Pozostałe, nieobsłużone naruszenia 23505 muszą kończyć się 500. Dodaj test potwierdzający, że oryginalny UniqueViolation dociera do serwisu po opuszczeniu transakcji.
  3. Akceptuję wyjątki domenowe w app/core/errors.py. Nie dodawaj zależności od FastAPI ani HTTPException. Atrybut status_code może pozostać jako kontrakt dla przyszłego handlera w 6.4.
  4. Akceptuję serwisy przyjmujące schematy i zwracające rekordy.
  5. Akceptuję 404 PROFILE_NOT_FOUND dla braku profilu.
  6. Nie dodawaj funkcjonalności spoza zaakceptowanego API-DESIGN.

  Zmiana organizacji pracy:
  Nie wykonuj całego etapu 6.3 za jednym razem. Pracujemy krokami:
  - Krok 1: core/errors.py i testy.
  - Krok 2: profile_service.py i jego testy.
  - Krok 3: create_goal() i get_active_goal() oraz testy.
  - Krok 4: update_goal(), archive_goal() oraz testy.
  - Krok 5: testy współbieżności.
  - Krok 6: pełna walidacja.

  Po każdym z kroków 1–4 zatrzymaj się, pokaż zmienione pliki, testy i diff. Poczekaj na mój przegląd i akceptację przed przejściem dalej.

  Na razie wykonaj wyłącznie krok 1. Nie implementuj kolejnych kroków. Bez commita i push.

  Zanim rozpoczniesz krok 1:
  1. Sprawdź aktualny branch i git status.
  2. Sprawdź ostatni commit poleceniem git log -1 --oneline.
  3. Zweryfikuj, czy commit 58195a7 jest obecny.
  4. Sprawdź, czy lokalny kod odpowiada temu commitowi i czy nie ma niezacommitowanych zmian.

  Jeśli wszystko się zgadza, przejdź do kroku 1 z poniższego polecenia.
  Jeśli wykryjesz rozbieżności, niczego nie zmieniaj i najpierw przedstaw raport.

  Ran 2 shell commands

Stan się zgadza: branch main, working tree czysty, HEAD = 58195a7. Przechodzę do kroku 1. Najpierw sprawdzam istniejący kod.

  Ran 7 shell commands

Pracuję nad krokiem 1: poprawiam uwagę lintera i uzupełniam środowisko testowe. W .venv brakuje psycopg, który jest zadeklarowany w requirements.

  Ran 2 shell commands

Krok 1 gotowy: core/errors.py i testy

Weryfikacja przed startem: branch main, working tree czysty, HEAD to 58195a7 feat: add profile and career goal repositories. Lokalny kod zgadza się z commitem.

Zmienione pliki (oba nowe, nieśledzone)

- backend/app/core/errors.py
- backend/tests/test_domain_errors.py

Nie zmieniałem żadnych istniejących plików. Nie robiłem commita ani pusha.

app/core/errors.py

Bazowy DomainError ma atrybuty status_code, code i message. Nie importuje FastAPI ani HTTPException. status_code to kontrakt dla handlera z etapu 6.4.

┌───────────────────────┬──────┬────────────────────┬─────────────────────────────────────────────────────────────────────────────────┐
│        Wyjątek        │ HTTP │        code        │                                     Użycie                                      │
├───────────────────────┼──────┼────────────────────┼─────────────────────────────────────────────────────────────────────────────────┤
│ ProfileNotFoundError  │ 404  │ PROFILE_NOT_FOUND  │ brak profilu                                                                    │
├───────────────────────┼──────┼────────────────────┼─────────────────────────────────────────────────────────────────────────────────┤
│ GoalNotFoundError     │ 404  │ GOAL_NOT_FOUND     │ cel nie istnieje albo należy do kogoś innego (RLS)                              │
├───────────────────────┼──────┼────────────────────┼─────────────────────────────────────────────────────────────────────────────────┤
│ ActiveGoalExistsError │ 409  │ ACTIVE_GOAL_EXISTS │ one_active_goal_per_user w create_goal()                                        │
├───────────────────────┼──────┼────────────────────┼─────────────────────────────────────────────────────────────────────────────────┤
│ GoalNotActiveError    │ 409  │ GOAL_NOT_ACTIVE    │ cel istnieje, ale jest zarchiwizowany (update_goal() po None z update_active()) │
└───────────────────────┴──────┴────────────────────┴─────────────────────────────────────────────────────────────────────────────────┘

Domyślny status 500 w bazie jest tylko zabezpieczeniem. Nieobsłużone 23505 i tak nie przechodzą przez DomainError, więc kończą się 500 w ogólnym handlerze.

Do Twojej decyzji: nazwy kodów GOAL_NOT_FOUND, ACTIVE_GOAL_EXISTS i GOAL_NOT_ACTIVE są moją propozycją. Zaakceptowałeś tylko PROFILE_NOT_FOUND, a API-DESIGN nie definiuje kodów dla tych przypadków. Daj znać, jeśli wolisz inne nazwy.

Testy (test_domain_errors.py)

- Kontrakt status_code i code oraz domyślna wiadomość dla każdego wyjątku (parametryzowany).
- Własna wiadomość nadpisuje domyślną.
- Kody są unikalne.
- Moduł nie zależy od FastAPI.

Test dotyczący UniqueViolation z decyzji 2 dodam w kroku 3, bo wymaga create_goal().

Wyniki

- Pełny pytest: 283 passed, 96 skipped. Pominięte to testy integracyjne wymagające bazy, które były pomijane już wcześniej.
- ruff check i ruff format --check: czysto.

W backend/.venv brakowało psycopg, więc conftest.py się nie ładował. Uruchomiłem pip install -r requirements-dev.txt. To tylko środowisko lokalne, bez zmian w repozytorium.

Diff

Pliki są nowe, więc git diff jest pusty. Pełną treść pokazałem powyżej, a w całości są w plikach.

Sugerowany commit: feat: add domain error classes for profile and goal services

Czekam na Twój przegląd przed krokiem 2 (profile_service.py).

✻ Crunched for 1m 6s · done 8:40