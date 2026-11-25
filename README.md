# ClutchCup API — System Zarządzania Turniejami Esportowymi

Autorski projekt backendowy typu **REST API** zrealizowany w technologii **Django 6.1** oraz **Django REST Framework**, umożliwiający kompleksową organizację turniejów esportowych, zarządzanie drużynami, przeprowadzanie zapisów oraz obsługę przebiegu rozgrywek.

## Stack technologiczny
* **Język i framework:** Python 3.13+, Django 6.1, Django REST Framework
* **Uwierzytelnianie:** Bezstanowe tokeny JWT (`djangorestframework-simplejwt`)
* **Filtrowanie i wyszukiwanie:** `django-filter`, `SearchFilter`, paginacja
* **Dokumentacja API:** OpenAPI 3.0 / Swagger UI (`drf-spectacular`)
* **Baza danych:** SQLite 3

## Architektura i założenia domenowe

### 1. Schemat bazy danych (Modele i relacje)
System opiera się na relacyjnym schemacie danych odwzorowującym domenę esportową:
* **`UserProfile`** (`OneToOneField` do `User`) – rozszerzenie konta użytkownika o rolę w systemie (`ORGANIZER` – organizator turniejów, `CAPTAIN` – kapitan drużyny) oraz identyfikator gracza (nick/SteamID).
* **`Game`** – obsługiwane tytuły gier esportowych (np. Counter-Strike 2, League of Legends, Valorant) wraz z wymaganym rozmiarem składu.
* **`Team`** (`ForeignKey` do kapitana, `ManyToManyField` do zawodników) – drużyny esportowe z unikalną nazwą i tagiem (`UniqueConstraint`).
* **`Tournament`** (`ForeignKey` do `Game` oraz organizatora `User`) – turnieje z określonym statusem (`DRAFT`, `OPEN`, `ONGOING`, `FINISHED`), limitem miejsc, pulą nagród oraz ramami czasowymi zapisów.
* **`TournamentRegistration`** (`ForeignKey` do `Tournament` i `Team`, `UniqueConstraint` na parze turniej–drużyna) – zgłoszenia drużyn do turniejów wraz ze statusem akceptacji.
* **`Match`** (`ForeignKey` do `Tournament` oraz dwóch instancji `Team`) – pojedynki turniejowe z rundą drabinki, wynikiem punktowym, zwycięzcą oraz statusem rozegrania.

### 2. Logika biznesowa (poza standardowym CRUD)
* **Rejestracja drużyny do turnieju (`POST /api/tournaments/{id}/register/`):** Operacja zabezpieczona transakcją atomową (`transaction.atomic()`) oraz blokadą pesymistyczną (`select_for_update()`) na rekordzie turnieju w celu eliminacji wyścigów (race conditions) przy zapełnianiu ostatnich wolnych slotów. Weryfikuje otwarcie zapisów, termin oraz wymaganą liczbę graczy w drużynie.
* **Generowanie drabinki / start turnieju (`POST /api/tournaments/{id}/start/`):** Transakcyjna zmiana stanu turnieju z `OPEN` na `ONGOING`, walidacja minimalnej liczby zatwierdzonych drużyn oraz automatyczne wygenerowanie par meczowych pierwszej rundy.
* **Raportowanie wyniku meczu (`POST /api/matches/{id}/report-score/`):** Walidacja stanu meczu, aktualizacja wyniku, wyłonienie zwycięzcy oraz agregacja statystyk turniejowych.

### 3. Bezpieczeństwo, uprawnienia i optymalizacja
* **Uwierzytelnianie JWT:** Endpointy `/api/token/`, `/api/token/refresh/` oraz `/api/me/` (zwracający dane profilu i przypisaną rolę).
* **Kontrola dostępu (RBAC i Object-Level Permissions):**
  * Filtrowanie widoczności obiektów w `get_queryset()` przy użyciu `models.Q` (np. turnieje w statusie `DRAFT` widoczne wyłącznie dla ich organizatora).
  * Własne klasy uprawnień dziedziczące po `BasePermission`: `IsOrganizerOrReadOnly`, `IsTournamentOwner`, `IsTeamCaptain`.
* **Optymalizacja ORM:** Wykorzystanie `select_related` oraz `prefetch_related` w widokach list i szczegółów w celu eliminacji problemu *N+1 zapytań*.

## Struktura projektu
```text
ClutchCup_174694/
├── config/                 # Ustawienia główne projektu, routing URL, ASGI/WSGI
├── tournaments/            # Główna aplikacja domenowa
│   ├── migrations/         # Migracje schematu bazy danych
│   ├── admin.py            # Personalizacja panelu Django Admin
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