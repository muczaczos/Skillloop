Popraw krok 1 zgodnie z wcześniej zaakceptowanym kontraktem.

  W backend/app/core/errors.py:
  - Zmień DomainError na AppError.
  - Zmień GoalNotActiveError na GoalArchivedError (409, GOAL_ARCHIVED).
  - Dodaj NoActiveGoalError (404, NO_ACTIVE_GOAL).
  - Zachowaj ProfileNotFoundError, GoalNotFoundError i ActiveGoalExistsError.
  - Wszystkie wyjątki mają dziedziczyć po AppError.

  W backend/tests/test_domain_errors.py:
  - Zaktualizuj importy i testy.
  - Dodaj test kontraktu NoActiveGoalError.
  - Sprawdź unikalność wszystkich pięciu kodów.
  - Zachowaj test niezależności od FastAPI.

  Nie zmieniaj żadnych innych plików. Uruchom testy i Ruff. Pokaż pełną treść obu zmienionych plików i diff. Bez commita. Nie przechodź do kroku 2.

  Ran 2 shell commands

Krok 1 poprawiony zgodnie z kontraktem. Zmieniłem tylko dwa pliki, bez commita i bez kroku 2.

Zmiany

- errors.py: DomainError zmienione na AppError, a GoalNotActiveError na GoalArchivedError (409, GOAL_ARCHIVED). Dodałem NoActiveGoalError (404, NO_ACTIVE_GOAL). ProfileNotFoundError, GoalNotFoundError i ActiveGoalExistsError zostały bez zmian i wszystkie dziedziczą po AppError.
- test_domain_errors.py:
  - Zaktualizowałem importy i dodałem NoActiveGoalError do testu kontraktu.
  - Test unikalności sprawdza teraz pięć kodów.
  - Test niezależności od FastAPI został.
- Pełna treść obu plików jest w wyniku polecenia powyżej.

Wyniki

- pytest: 284 passed, 96 skipped. Pominięte to testy integracyjne wymagające bazy.
- ruff check i ruff format --check: czysto.

Diff

Oba pliki są nowe i nieśledzone, więc git diff jest pusty. Zmiany względem poprzedniej wersji to tylko zamiany nazw, dodanie NoActiveGoalError i 5 zamiast 4 w teście unikalności.

Obce pliki w katalogu

W katalogu pojawił się nowy, nieśledzony plik services/pierwszy-prompt-plus-odpowiedź. Jest pusty i ma datę 08:41 dzisiaj. Nie tworzyłem go, więc nie mam pewności, skąd się wziął. Nie ruszałem go. Zdecyduj, czy go zostawić, bo przy commicie lepiej go nie dodawać przez przypadek.

Sugerowany commit: feat: add application error classes for profile and goal services

Czekam na akceptację przed krokiem 2.

✻ Sautéed for 24s · done 8:46