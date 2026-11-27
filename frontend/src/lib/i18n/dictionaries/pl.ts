// The Polish dictionary is the reference: its shape is the type every other language must have.
export const pl = {
  common: {
    appName: "SkillLoop",
    loading: "Ładowanie…",
    retry: "Spróbuj ponownie",
    language: "Język",
  },
  nav: {
    label: "Główna nawigacja",
    dashboard: "Pulpit",
    signOut: "Wyloguj się",
    signingOut: "Wylogowywanie…",
  },
  auth: {
    tagline: "Twój osobisty trener kompetencji zawodowych",
    email: "Adres e-mail",
    password: "Hasło",
    confirmPassword: "Powtórz hasło",
    passwordHint: "Co najmniej 6 znaków.",
    login: {
      title: "Zaloguj się",
      submit: "Zaloguj się",
      submitting: "Logowanie…",
      switchPrompt: "Nie masz konta?",
      switchLink: "Zarejestruj się",
    },
    register: {
      title: "Utwórz konto",
      submit: "Zarejestruj się",
      submitting: "Tworzenie konta…",
      switchPrompt: "Masz już konto?",
      switchLink: "Zaloguj się",
      checkEmail:
        "Sprawdź skrzynkę e-mail i potwierdź adres, aby dokończyć rejestrację. Potem się zaloguj.",
    },
    sessionExpired: "Twoja sesja wygasła. Zaloguj się ponownie.",
    validation: {
      emailRequired: "Podaj adres e-mail.",
      emailInvalid: "Podaj poprawny adres e-mail.",
      passwordRequired: "Podaj hasło.",
      passwordTooShort: "Hasło musi mieć co najmniej 6 znaków.",
      passwordMismatch: "Hasła nie są takie same.",
    },
    errors: {
      invalidCredentials: "Nieprawidłowy e-mail lub hasło.",
      userExists: "Konto z tym adresem e-mail już istnieje.",
      weakPassword: "Hasło jest zbyt słabe.",
      emailNotConfirmed: "Najpierw potwierdź adres e-mail.",
      emailInvalid: "Ten adres e-mail nie został zaakceptowany.",
      rateLimit: "Zbyt wiele prób. Odczekaj chwilę i spróbuj ponownie.",
      network: "Nie można połączyć się z usługą logowania. Spróbuj ponownie.",
      unknown: "Nie udało się. Spróbuj ponownie.",
      signOut: "Nie udało się wylogować. Spróbuj ponownie.",
    },
  },
  dashboard: {
    title: "Pulpit",
    account: "Twoje konto",
    email: "E-mail",
    displayName: "Nazwa",
    displayNameEmpty: "Nie ustawiono",
    memberSince: "Konto od",
    comingSoonTitle: "Wkrótce więcej",
    comingSoonText: "Cele zawodowe i kompetencje pojawią się w kolejnych etapach.",
  },
  apiErrors: {
    title: "Nie udało się pobrać danych",
    unauthorized: "Twoja sesja wygasła. Zaloguj się ponownie.",
    unavailable: "Usługa jest chwilowo niedostępna. Spróbuj ponownie za chwilę.",
    network: "Nie można połączyć się z serwerem aplikacji. Sprawdź, czy działa, i spróbuj ponownie.",
    timeout: "Serwer aplikacji nie odpowiedział na czas. Spróbuj ponownie.",
    profileNotFound: "Nie znaleziono profilu dla tego konta.",
    generic: "Wystąpił błąd. Spróbuj ponownie.",
    requestId: "Identyfikator zgłoszenia",
  },
  notFound: {
    title: "Nie ma takiej strony",
    back: "Wróć do aplikacji",
  },
  config: {
    title: "Aplikacja nie jest skonfigurowana",
    text: "Brakuje publicznych zmiennych środowiskowych lub są niepoprawne. Zobacz plik .env.example.",
  },
};

export type Dictionary = typeof pl;
