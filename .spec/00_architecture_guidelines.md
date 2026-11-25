# 00 — Wytyczne architektoniczne ClutchCup API

> Dokument nadrzędny dla `01_domain_models.md`, `02_api_contracts.md` i `03_business_invariants.md`.
> W razie sprzeczności obowiązuje kolejność: `CLAUDE.md` → `03_business_invariants.md` → `02_api_contracts.md` → `01_domain_models.md` → ten plik.

## 1. Cel systemu

ClutchCup to backendowe REST API do organizacji turniejów esportowych. Organizator tworzy turniej dla konkretnej gry, kapitan zgłasza do niego drużynę, organizator potwierdza zgłoszenia i startuje turniej. System generuje mecze i przyjmuje wyniki aż do wyłonienia zwycięzcy.

Frontendu nie ma. Jedynym interfejsem użytkownika poza API jest panel Django Admin.

## 2. Stack (ścisłe ograniczenie)

| Warstwa | Technologia |
|---|---|
| Język | Python 3.13+ |
| Framework | Django `>=6.1.0,<6.2.0` |
| REST | `djangorestframework` |
| Uwierzytelnianie | `djangorestframework-simplejwt` (JWT, bezstanowe) |
| Filtrowanie | `django-filter` + `SearchFilter` + `OrderingFilter` |
| Dokumentacja | `drf-spectacular` (OpenAPI 3.0, Swagger UI) |
| Baza danych | SQLite 3 |

Nie wolno dodawać innych zależności (Celery, Redis, PostgreSQL, Docker, pakiety frontendowe itd.). Cała logika działa synchronicznie w cyklu żądanie–odpowiedź.

## 3. Struktura repozytorium

```text
ClutchCup_174694/
├── .spec/                      # specyfikacja (ten katalog)
├── config/                     # pakiet konfiguracyjny projektu
│   ├── settings.py
│   └── urls.py                 # admin/, api/token/, api/schema/, api/docs/, tournaments/
├── tournaments/                # jedyna aplikacja domenowa
│   ├── models.py
│   ├── admin.py
│   ├── serializers.py
│   ├── permissions.py
│   ├── filters.py
│   ├── exceptions.py           # ConflictError (HTTP 409)
│   ├── cbv_views.py            # widoki kont + widoki podsumowań (APIView)
│   ├── viewsets.py             # ModelViewSet + akcje @action
│   ├── urls.py                 # app_name = 'tournaments'
│   └── tests.py
└── manage.py
```

Plik `tournaments/views.py` z szablonu `startapp` należy usunąć, bo jego rolę przejmują `cbv_views.py` i `viewsets.py`.

## 4. Odpowiedzialności modułów

| Moduł | Odpowiada za | Nie robi |
|---|---|---|
| `models.py` | pola, relacje, `Meta` (`ordering`, `verbose_name(_plural)`, `constraints`), walidatory pól, `__str__`, automatyczne generowanie `slug` w `save()`, stałe domenowe (`MAX_SUBSTITUTES`, `CANCELLATION_CUTOFF`) | logika HTTP, uprawnienia |
| `serializers.py` | kształt JSON, walidacja danych wejściowych (`validate_<field>`, `validate`), rozdzielenie serializatorów listy i szczegółów | blokady, transakcje wielorekordowe |
| `permissions.py` | „kto może”: role i własność obiektu, polskie `message` | „kiedy można” (stan i czas to reguły biznesowe) |
| `viewsets.py` | queryset (widoczność + optymalizacja), dobór serializatora i uprawnień, akcje biznesowe w `transaction.atomic()` | renderowanie HTML |
| `cbv_views.py` | rejestracja konta, `/me/`, zmiana hasła, agregacje statystyk | CRUD domeny |
| `filters.py` | `FilterSet` z filtrami zakresowymi `gte`/`lte` | |
| `exceptions.py` | `ConflictError(APIException)`: `status_code = 409`, `default_detail = "Operacja jest sprzeczna z bieżącym stanem zasobu."`, `default_code = "conflict"` | |
| `admin.py` | konfiguracja panelu dla wszystkich 7 modeli | |

## 5. Konfiguracja (`config/settings.py`): wymagane zmiany

```python
LANGUAGE_CODE = 'pl'
TIME_ZONE = 'Europe/Warsaw'
USE_TZ = True                      # w bazie zawsze UTC, porównania przez timezone.now()

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'transaction_mode': 'IMMEDIATE',   # patrz 03_business_invariants.md, INV-01
            'timeout': 20,
        },
    }
}
```

Pozostała konfiguracja `REST_FRAMEWORK`, `SIMPLE_JWT` i `SPECTACULAR_SETTINGS` jest już w repozytorium i zostaje bez zmian. Domyślne uprawnienie to `IsAuthenticatedOrReadOnly`, paginacja `PageNumberPagination`, `PAGE_SIZE = 10`.

## 6. Routing

### 6.1. `config/urls.py`

| Ścieżka | Widok |
|---|---|
| `admin/` | Django Admin |
| `api/token/` | `TokenObtainPairView` |
| `api/token/refresh/` | `TokenRefreshView` |
| `api/schema/` | `SpectacularAPIView` |
| `api/docs/` | `SpectacularSwaggerView(url_name='schema')` |
| `tournaments/` | `include('tournaments.urls')` |

### 6.2. `tournaments/urls.py`

```python
app_name = 'tournaments'

router = DefaultRouter()
router.register('games', GameViewSet, basename='game')
router.register('tags', TagViewSet, basename='tag')
router.register('teams', TeamViewSet, basename='team')
router.register('tournaments', TournamentViewSet, basename='tournament')
router.register('registrations', TournamentRegistrationViewSet, basename='registration')
router.register('matches', MatchViewSet, basename='match')

urlpatterns = [
    path('api/register/', UserRegisterView.as_view(), name='register'),
    path('api/me/', CurrentUserProfileView.as_view(), name='me'),
    path('api/change-password/', ChangePasswordView.as_view(), name='change-password'),
    path('api/v1/summary/', TournamentSummaryView.as_view(), name='summary'),
    path('api/v1/', include(router.urls)),
]
```

Wynikowe prefiksy: zasoby domenowe są pod `/tournaments/api/v1/`, konta pod `/tournaments/api/`, tokeny pod `/api/token/`.

## 7. Uwierzytelnianie i role

- Wyłącznie JWT (`Authorization: Bearer <access>`). Żądanie bez tokenu do chronionego zasobu dostaje **401**.
- Rola to pole `UserProfile.role`: `organizer` lub `player`. Wybiera się ją przy rejestracji konta i później jest tylko do odczytu w API (zmienia ją wyłącznie admin).
- `is_staff` (admin) zarządza słownikami (`Game`, `Tag`) i polem `is_verified`.

### 7.1. Klasy uprawnień (`permissions.py`)

| Klasa | `has_permission` | `has_object_permission` | `message` |
|---|---|---|---|
| `IsAdminOrReadOnly` | SAFE albo `is_staff` | jak obok | „Tylko administrator może modyfikować ten zasób.” |
| `IsOrganizerOrReadOnly` | SAFE albo `profile.role == 'organizer'` | – | „Tylko użytkownik z rolą organizatora może tworzyć turnieje.” |
| `IsTournamentOwner` | – | `obj.organizer == request.user` (dla `TournamentRegistration`/`Match` wg `obj.tournament.organizer`) | „Tylko organizator tego turnieju może wykonać tę operację.” |
| `IsPlayer` | `profile.role == 'player'` | – | „Tylko użytkownik z rolą gracza może tworzyć drużyny.” |
| `IsTeamCaptain` | – | SAFE albo `obj.captain == request.user` | „Tylko kapitan drużyny może ją modyfikować.” |
| `IsRegistrationParty` | – | `obj.team.captain == user` lub `obj.tournament.organizer == user` | „Tylko kapitan zgłoszonej drużyny lub organizator turnieju może anulować to zgłoszenie.” |

Uprawnienia rozstrzygają tylko **kto** wykonuje operację. **Kiedy** operacja jest dozwolona (status, czas, limity) sprawdzają reguły biznesowe w akcjach. Ich naruszenie daje **409** albo **400**, nigdy **403**.

## 8. Polityka kodów HTTP

| Kod | Kiedy |
|---|---|
| 200 | odczyt, aktualizacja, akcja bez tworzenia nowego zasobu |
| 201 | utworzenie zasobu (`POST` kolekcji, `register`) |
| 204 | usunięcie |
| 400 | niepoprawne dane wejściowe: walidacja pól, walidacja krzyżowa, drużyna niespełniająca wymagań turnieju |
| 401 | brak lub nieważny token |
| 403 | zalogowany użytkownik bez wymaganej roli lub własności obiektu |
| 404 | obiekt nie istnieje **albo jest niewidoczny** dla użytkownika (np. nieopublikowany szkic cudzego turnieju) |
| 405 | metoda celowo niedostępna (np. `POST /matches/`, `DELETE /registrations/{id}/`) |
| 409 | konflikt ze stanem zasobu: zły status, minął termin, brak miejsc, duplikat, zamrożony skład |

Format błędów to standard DRF: `{"detail": "..."}` dla błędów ogólnych oraz `{"<pole>": ["..."]}` / `{"non_field_errors": ["..."]}` dla walidacji. **Wszystkie komunikaty są po polsku.**

## 9. Transakcje i współbieżność

1. Każda akcja zmieniająca stan **zgłoszeń, statusu turnieju lub meczów** (`register`, `confirm`, `cancel`, `open-registration`, `start`, `report-score`, aktualizacja turnieju w statusie `open`) działa w `transaction.atomic()`.
2. Pierwszą operacją w transakcji jest `Tournament.objects.select_for_update().get(pk=...)`. **Kolejność blokad jest zawsze taka sama: turniej → zgłoszenie/mecz.** Zapobiega to zakleszczeniom.
3. Wszystkie warunki biznesowe sprawdza się **po** założeniu blokady, na świeżo odczytanych danych. Wartości z `get_object()` sprzed blokady służą tylko do 404 i 403.
4. W SQLite `select_for_update()` jest ignorowane, a serializację zapisów zapewnia `transaction_mode = 'IMMEDIATE'`. Kod pozostaje poprawny po ewentualnej zmianie silnika (szczegóły w `03`, INV-01).
5. Wyjątek w bloku `atomic()` wycofuje całą operację. Błędy biznesowe zgłasza się przez `raise ConflictError(...)` / `raise ValidationError(...)`, a nie przez `return Response(...)` z wnętrza bloku.

## 10. Optymalizacja ORM

- Listy turniejów: `select_related('game', 'organizer')`, `prefetch_related('tags')`, `annotate(active_registrations_count=Count('registrations', filter=Q(registrations__status__in=['pending', 'confirmed'])))`.
- Szczegóły turnieju dodatkowo: `Prefetch('registrations', queryset=TournamentRegistration.objects.select_related('team'))`.
- Drużyny: `select_related('captain__profile')`, `prefetch_related('members__profile')`, `annotate(members_count=Count('members', distinct=True))`.
- Zgłoszenia: `select_related('tournament__game', 'team__captain')`.
- Mecze: `select_related('tournament', 'team_a', 'team_b', 'winner')`.
- Gdy queryset filtruje przez relację M2M (`team__members=user`), musi kończyć się `.distinct()`.
- `SerializerMethodField` nie wykonuje zapytań. Czyta wartości z adnotacji albo z prefetch.

## 11. Konwencje kodu

- PEP 8, DRY, bez komentarzy opisujących oczywisty kod. Docstringi tylko tam, gdzie zasila nimi `drf-spectacular`.
- Wartości wyborów jako `models.TextChoices` zagnieżdżone w modelu (`Tournament.Status.OPEN`).
- Stałe domenowe w `models.py` na poziomie modułu: `MAX_SUBSTITUTES = 2`, `MAX_TEAM_MEMBERS = 12`, `CANCELLATION_CUTOFF = timedelta(hours=24)`, `MIN_TEAMS_TO_START = 2`.
- Czas zawsze z `django.utils.timezone.now()`, nigdy z `datetime.now()`.
- Akcje `@action` z myślnikami w URL: `url_path='open-registration'`, `url_path='report-score'`.
- Każda akcja ma `@extend_schema` z serializatorem żądania i odpowiedzi.
- Git: asystent nie wykonuje `commit` ani `push`.

## 12. Strategia testów (`tests.py`)

- `APITestCase` + `APIClient`, uwierzytelnianie przez `client.force_authenticate(user)`, a w jednym teście przepływu przez `/api/token/`.
- Czas kontrolowany przez ustawianie `registration_deadline` i `start_date` względem `timezone.now()` w `setUp` (bez zewnętrznych bibliotek do mockowania czasu, tylko `unittest.mock.patch('django.utils.timezone.now')` w razie potrzeby).
- Każdy niezmiennik z `03_business_invariants.md` ma co najmniej jeden test pozytywny i jeden negatywny z asercją kodu HTTP **oraz** stanu bazy po operacji (np. liczba zgłoszeń się nie zmieniła).
- Wymagane pokrycie kodów: 200, 201, 204, 400, 401, 403, 404, 405, 409.

## 13. Definition of Done dla każdej funkcji

1. Model ma migrację, a `python manage.py makemigrations --check` nie wykrywa zmian.
2. Endpoint jest zgodny z `02_api_contracts.md` (ścieżka, metoda, pola, kody).
3. Reguły z `03_business_invariants.md` są egzekwowane i przetestowane.
4. `python manage.py test tournaments` przechodzi.
5. `python manage.py spectacular --validate` przechodzi bez błędów.
