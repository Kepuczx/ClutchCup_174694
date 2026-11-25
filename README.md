# ClutchCup API — System Zarządzania Turniejami Esportowymi

Autorski projekt backendowy typu **REST API** zrealizowany w technologii **Django 6.1** oraz **Django REST Framework**, umożliwiający kompleksową organizację turniejów esportowych, zarządzanie drużynami, przeprowadzanie zapisów oraz obsługę przebiegu rozgrywek.

Pełna specyfikacja projektu znajduje się w katalogu [`.spec/`](.spec/):
[architektura](.spec/00_architecture_guidelines.md) · [modele](.spec/01_domain_models.md) · [kontrakty API](.spec/02_api_contracts.md) · [reguły biznesowe](.spec/03_business_invariants.md).

## Stack technologiczny
* **Język i framework:** Python 3.13+, Django 6.1, Django REST Framework
* **Uwierzytelnianie:** Bezstanowe tokeny JWT (`djangorestframework-simplejwt`)
* **Filtrowanie i wyszukiwanie:** `django-filter`, `SearchFilter`, `OrderingFilter`, paginacja
* **Dokumentacja API:** OpenAPI 3.0 / Swagger UI (`drf-spectacular`)
* **Baza danych:** SQLite 3

## Architektura i założenia domenowe

### 1. Schemat bazy danych (Modele i relacje)
System opiera się na relacyjnym schemacie danych odwzorowującym domenę esportową:
* **`UserProfile`** (`OneToOneField` do `User`) – rozszerzenie konta użytkownika o pseudonim (`nickname`), SteamID64 (`steam_id`), flagę weryfikacji (`is_verified`) oraz rolę w systemie: `organizer` (organizator turniejów) lub `player` (gracz). Kapitan nie jest osobną rolą – to gracz wskazany w polu `Team.captain`.
* **`Game`** – obsługiwane tytuły gier esportowych (np. Counter-Strike 2, League of Legends, Valorant) wraz z wymaganym rozmiarem składu (`team_size`, domyślnie 5).
* **`Tag`** – etykiety turniejów (np. `#cs2`, `#5v5`, `#lan`, `#amateur`), relacja `ManyToManyField` z `Tournament`.
* **`Team`** (`ForeignKey` do kapitana, `ManyToManyField` do zawodników) – drużyny esportowe z unikalną nazwą (bez względu na wielkość liter) i unikalnym skrótem `tag` (`UniqueConstraint`).
* **`Tournament`** (`ForeignKey` do `Game` oraz organizatora `User`) – turnieje w formacie `single_elimination` lub `round_robin`, ze statusem (`draft`, `open`, `ongoing`, `finished`), flagą publikacji `is_published`, limitem drużyn (`max_teams`), pulą nagród, wpisowym oraz terminami (`registration_deadline` < `start_date`).
* **`TournamentRegistration`** (`ForeignKey` do `Tournament` i `Team`, `UniqueConstraint` na parze turniej–drużyna) – zgłoszenia drużyn do turniejów ze statusem `pending`, `confirmed` lub `cancelled`.
* **`Match`** (`ForeignKey` do `Tournament` oraz do `Team` jako `team_a`, `team_b` i `winner`) – pojedynki turniejowe z numerem rundy, wynikiem punktowym, zwycięzcą oraz statusem `scheduled` / `completed`.

### 2. Logika biznesowa (poza standardowym CRUD)
* **Otwarcie zapisów (`POST /tournaments/api/v1/tournaments/{id}/open-registration/`):** Przejście `draft` → `open` z automatyczną publikacją turnieju; dostępne wyłącznie dla zweryfikowanego organizatora przed upływem terminu zapisów.
* **Rejestracja drużyny do turnieju (`POST /tournaments/api/v1/tournaments/{id}/register/`):** Operacja wykonywana przez kapitana drużyny, zabezpieczona transakcją atomową (`transaction.atomic()`) oraz blokadą pesymistyczną (`select_for_update()`) na rekordzie turnieju w celu eliminacji wyścigów (race conditions) przy zapełnianiu ostatnich wolnych miejsc (w SQLite serializację zapisów zapewnia dodatkowo `transaction_mode = 'IMMEDIATE'`). Weryfikuje otwarcie zapisów, termin, brak wcześniejszego zgłoszenia, rozmiar składu względem `game.team_size` (od `team_size` do `team_size + 2` zawodników), brak zawodników zgłoszonych w innej drużynie oraz limit `max_teams` (liczony ze zgłoszeń `pending` i `confirmed`).
* **Potwierdzanie i anulowanie zgłoszeń (`POST /tournaments/api/v1/registrations/{id}/confirm/`, `POST /tournaments/api/v1/registrations/{id}/cancel/`):** Organizator potwierdza zgłoszenia oczekujące (z ponowną walidacją składu). Kapitan może anulować zgłoszenie `pending` do startu turnieju, a zgłoszenie `confirmed` najpóźniej 24 godziny przed `start_date`; organizator może anulować dowolne aktywne zgłoszenie do startu. Anulowanie jest ostateczne.
* **Generowanie drabinki / start turnieju (`POST /tournaments/api/v1/tournaments/{id}/start/`):** Transakcyjna zmiana stanu turnieju z `open` na `ongoing` (po terminie zapisów lub po zapełnieniu wszystkich miejsc), walidacja minimalnej liczby potwierdzonych drużyn (2), anulowanie zgłoszeń `pending` oraz automatyczne wygenerowanie meczów pierwszej rundy (drabinka z wolnymi losami lub harmonogram „każdy z każdym”).
* **Raportowanie wyniku meczu (`POST /tournaments/api/v1/matches/{id}/report-score/`):** Walidacja stanu turnieju i meczu, zapis wyniku, wyłonienie zwycięzcy, automatyczne utworzenie kolejnej rundy drabinki oraz zakończenie turnieju (`finished`) po ostatnim meczu.
* **Statystyki (`GET /tournaments/api/v1/summary/`, `GET /tournaments/api/v1/tournaments/{id}/standings/`):** Agregacje `Count`, `Sum`, `Avg`, `Min`, `Max` dla opublikowanych turniejów oraz tabela wyników turnieju.

### 3. Bezpieczeństwo, uprawnienia i optymalizacja
* **Uwierzytelnianie JWT:** Endpointy `/api/token/` i `/api/token/refresh/`; konto użytkownika obsługują `/tournaments/api/register/`, `/tournaments/api/me/` (dane profilu i przypisana rola) oraz `/tournaments/api/change-password/`.
* **Kontrola dostępu (RBAC i Object-Level Permissions):**
  * Filtrowanie widoczności obiektów w `get_queryset()` przy użyciu `models.Q` – turniej jest widoczny, gdy `is_published = True` lub gdy użytkownik jest jego organizatorem (nieopublikowane szkice innych organizatorów zwracają `404`).
  * Własne klasy uprawnień dziedziczące po `BasePermission`: `IsAdminOrReadOnly`, `IsOrganizerOrReadOnly`, `IsTournamentOwner`, `IsPlayer`, `IsTeamCaptain`, `IsRegistrationParty`.
* **Optymalizacja ORM:** Wykorzystanie `select_related`, `prefetch_related` oraz adnotacji `Count` w widokach list i szczegółów w celu eliminacji problemu *N+1 zapytań*.

## Endpointy

| Prefiks | Zasoby |
|---|---|
| `/api/token/`, `/api/token/refresh/` | tokeny JWT |
| `/api/schema/`, `/api/docs/` | schemat OpenAPI i Swagger UI |
| `/tournaments/api/` | `register/`, `me/`, `change-password/` |
| `/tournaments/api/v1/` | `games/`, `tags/`, `teams/`, `tournaments/`, `registrations/`, `matches/`, `summary/` |
| `/admin/` | panel Django Admin |

Akcje niestandardowe:

| Metoda | Ścieżka |
|---|---|
| `POST` | `/tournaments/api/v1/tournaments/{id}/open-registration/` |
| `POST` | `/tournaments/api/v1/tournaments/{id}/register/` |
| `POST` | `/tournaments/api/v1/tournaments/{id}/start/` |
| `GET` | `/tournaments/api/v1/tournaments/{id}/registrations/` |
| `GET` | `/tournaments/api/v1/tournaments/{id}/standings/` |
| `POST` | `/tournaments/api/v1/registrations/{id}/confirm/` |
| `POST` | `/tournaments/api/v1/registrations/{id}/cancel/` |
| `POST` | `/tournaments/api/v1/matches/{id}/report-score/` |

Szczegółowe kontrakty (pola, kody HTTP, komunikaty błędów) opisuje [`.spec/02_api_contracts.md`](.spec/02_api_contracts.md).

## Struktura projektu
```text
ClutchCup_174694/
├── .spec/                  # Specyfikacja: architektura, modele, kontrakty API, reguły biznesowe
├── config/                 # Ustawienia główne projektu, routing URL, ASGI/WSGI
├── tournaments/            # Główna aplikacja domenowa
│   ├── migrations/         # Migracje schematu bazy danych
│   ├── admin.py            # Personalizacja panelu Django Admin
│   ├── cbv_views.py        # Widoki konta (rejestracja, /me/, zmiana hasła) i podsumowania
│   ├── exceptions.py       # ConflictError (HTTP 409)
│   ├── filters.py          # Klasy FilterSet (filtrowanie zakresowe gte/lte)
│   ├── models.py           # Modele relacyjne, więzy i walidatory
│   ├── permissions.py      # Własne klasy BasePermission
│   ├── serializers.py      # Serializatory listy, szczegółów i walidacja
│   ├── tests.py            # Testy automatyczne APITestCase
│   ├── urls.py             # DefaultRouter i ścieżki endpointów
│   └── viewsets.py         # ModelViewSet oraz akcje biznesowe @action
├── .gitignore
├── manage.py
├── README.md
└── requirements.txt
```
