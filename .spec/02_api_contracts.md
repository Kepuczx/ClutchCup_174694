# 02 — Kontrakty API ClutchCup

Konwencje wspólne:

- `Content-Type: application/json`. Daty w ISO 8601 ze strefą (`2026-12-05T18:00:00+01:00`), odpowiedzi w strefie `Europe/Warsaw`.
- Kwoty (`DecimalField`) w odpowiedziach są **stringami**, np. `"1500.00"` (domyślne `COERCE_DECIMAL_TO_STRING`).
- Listy są paginowane: `{"count", "next", "previous", "results": [...]}`, `PAGE_SIZE = 10`, parametr `?page=`.
- „Auth” w tabelach: `—` oznacza dostęp bez tokenu, `JWT` wymaga `Authorization: Bearer <access>`.
- Kody błędów zgodne z polityką z `00_architecture_guidelines.md` §8. Treść i warunki błędów 409/400 dla reguł biznesowych opisuje `03_business_invariants.md` (identyfikatory `INV-xx`).

---

## A. Uwierzytelnianie i konto

### A.1. `POST /api/token/` — `TokenObtainPairView`
Body: `{"username", "password"}`. Odpowiedź **200** `{"access", "refresh"}`, błędne dane **401**.

### A.2. `POST /api/token/refresh/` — `TokenRefreshView`
Body: `{"refresh"}`. Odpowiedź **200** `{"access"}`, nieważny token **401**.

### A.3. `POST /tournaments/api/register/` — `UserRegisterView` (`CreateAPIView`, `AllowAny`)

Request:
```json
{
  "username": "s1mple_fan",
  "email": "fan@example.com",
  "password": "Trudne!Haslo123",
  "password2": "Trudne!Haslo123",
  "nickname": "s1mpleFan",
  "role": "player",
  "steam_id": "76561198000000001"
}
```

Walidacja (400):
- `password != password2` → `{"password2": ["Hasła nie są identyczne."]}`
- `validate_password()` z `AUTH_PASSWORD_VALIDATORS` → komunikaty walidatorów Django (w PL dzięki `LANGUAGE_CODE='pl'`)
- `username` / `nickname` / `steam_id` zajęte → `"Ta nazwa użytkownika jest już zajęta."` / `"Ten pseudonim jest już zajęty."` / `"Ten SteamID jest już przypisany do innego konta."`
- `role` spoza `organizer|player` → standardowy błąd `ChoiceField`

Odpowiedź **201**, bez hasła:
```json
{"id": 7, "username": "s1mple_fan", "email": "fan@example.com",
 "profile": {"nickname": "s1mpleFan", "role": "player", "steam_id": "76561198000000001", "is_verified": false}}
```
`User` i `UserProfile` powstają w jednej `transaction.atomic()`.

### A.4. `GET | PUT | PATCH /tournaments/api/me/` — `CurrentUserProfileView` (`RetrieveUpdateAPIView`, `IsAuthenticated`)

`get_object()` zwraca `request.user.profile`.

Odpowiedź **200**:
```json
{"id": 7, "username": "s1mple_fan", "email": "fan@example.com",
 "nickname": "s1mpleFan", "role": "player", "steam_id": "76561198000000001",
 "is_verified": false, "captained_teams": [3], "teams": [3, 5]}
```
- Zapisywalne: `email`, `nickname`, `steam_id`.
- Tylko do odczytu: `id`, `username`, `role`, `is_verified`, `captained_teams`, `teams`. Wartości przesłane w tych polach są ignorowane.
- Bez tokenu **401**.

### A.5. `PUT /tournaments/api/change-password/` — `ChangePasswordView` (`UpdateAPIView`, `IsAuthenticated`)

Request: `{"old_password", "new_password", "new_password2"}`.
- Błędne `old_password` → **400** `{"old_password": ["Aktualne hasło jest nieprawidłowe."]}`
- `new_password != new_password2` → **400** `{"new_password2": ["Hasła nie są identyczne."]}`
- `validate_password(new_password, user)` → **400**
- Sukces **200** `{"detail": "Hasło zostało zmienione."}`
- `PATCH` → **405**, bez tokenu → **401**.

---

## B. Słowniki: `/tournaments/api/v1/games/` i `/tournaments/api/v1/tags/`

`ModelViewSet`, uprawnienie `IsAdminOrReadOnly`.

| Metoda | Ścieżka | Auth | Sukces | Błędy |
|---|---|---|---|---|
| GET | `games/` | — | 200 | |
| GET | `games/{id}/` | — | 200 | 404 |
| POST | `games/` | JWT, `is_staff` | 201 | 400, 401, 403 |
| PUT/PATCH | `games/{id}/` | JWT, `is_staff` | 200 | 400, 401, 403, 404, 409 (INV-05) |
| DELETE | `games/{id}/` | JWT, `is_staff` | 204 | 401, 403, 404, 409 (gra ma turnieje, `ProtectedError`) |

`Game` (request/response): `{"id", "name", "slug" (RO), "team_size", "description", "tournaments_count" (RO, adnotacja)}`.
Filtry `GameFilter`: `team_size`, `team_size_min` (`gte`), `team_size_max` (`lte`). Wyszukiwanie: `?search=` po `name`.

`Tag`: `{"id", "name", "slug" (RO)}`. Wyszukiwanie: `?search=` po `name`. Operacje i kody jak dla `games/`. `DELETE` usuwa tag także z turniejów (M2M), więc nie zwraca 409.

---

## C. Drużyny: `/tournaments/api/v1/teams/`

`TeamViewSet(ModelViewSet)`.
- `get_permissions()`: `list`, `retrieve` → `AllowAny`; `create` → `IsAuthenticated` + `IsPlayer`; `update`, `partial_update`, `destroy` → `IsAuthenticated` + `IsTeamCaptain`.
- `get_serializer_class()`: `list` → `TeamListSerializer`, pozostałe → `TeamModelSerializer`.

| Metoda | Ścieżka | Auth | Sukces | Błędy |
|---|---|---|---|---|
| GET | `teams/` | — | 200 | |
| GET | `teams/{id}/` | — | 200 | 404 |
| POST | `teams/` | JWT, rola `player` | 201 | 400, 401, 403 |
| PUT/PATCH | `teams/{id}/` | JWT, kapitan | 200 | 400, 401, 403, 404, 409 (INV-04) |
| DELETE | `teams/{id}/` | JWT, kapitan | 204 | 401, 403, 404, 409 (INV-10) |

`TeamListSerializer`:
```json
{"id": 3, "name": "Clutch Kings", "slug": "clutch-kings", "tag": "CK",
 "captain_nickname": "s1mpleFan", "members_count": 5}
```
`captain_nickname` i `members_count` to `SerializerMethodField` / adnotacja.

`TeamModelSerializer`:
```json
{
  "id": 3, "name": "Clutch Kings", "slug": "clutch-kings", "tag": "CK",
  "captain": {"id": 7, "username": "s1mple_fan", "nickname": "s1mpleFan"},
  "members": [{"id": 7, "username": "s1mple_fan", "nickname": "s1mpleFan"}, "..."],
  "member_ids": [7, 8, 9, 10, 11],
  "created_at": "2026-11-20T12:00:00+01:00"
}
```
- `captain`, `members`: zagnieżdżone, tylko do odczytu. `captain` ustawia `perform_create` na `request.user`.
- `member_ids`: `PrimaryKeyRelatedField(many=True, write_only=True, queryset=User.objects.all(), source='members')`. Kapitan jest dopisywany automatycznie, jeśli go brakuje.
- `validate_name`: nazwa unikalna bez względu na wielkość liter → „Drużyna o tej nazwie już istnieje.”
- `validate_member_ids`: `len(set(ids ∪ {kapitan})) <= MAX_TEAM_MEMBERS` → „Drużyna może liczyć maksymalnie 12 zawodników.”
- `tag` jest normalizowany przez `.upper()` przed walidacją wzorca.

Filtry `TeamFilter`: `name` (`icontains`), `tag` (`iexact`), `captain` (id), `member` (id użytkownika), `members_min` / `members_max` (`gte`/`lte` na adnotacji `members_count`), `created_after` / `created_before` (`gte`/`lte`). `?search=` po `name`, `tag`. `?ordering=` po `name`, `created_at`, `members_count`.

---

## D. Turnieje: `/tournaments/api/v1/tournaments/`

`TournamentViewSet(ModelViewSet)`.

**Widoczność (`get_queryset`)**:
- anonim: `Q(is_published=True)`,
- zalogowany: `Q(is_published=True) | Q(organizer=request.user)`.

Obiekt niewidoczny daje **404**, nie 403.

**Uprawnienia (`get_permissions`)**:

| Akcja | Klasy |
|---|---|
| `list`, `retrieve`, `registrations`, `standings` | `AllowAny` |
| `create` | `IsAuthenticated`, `IsOrganizerOrReadOnly` |
| `update`, `partial_update`, `destroy`, `open_registration`, `start` | `IsAuthenticated`, `IsTournamentOwner` |
| `register` | `IsAuthenticated` (kapitana sprawdza akcja, patrz D.6) |

### D.1. Lista i szczegóły

| Metoda | Ścieżka | Sukces | Błędy |
|---|---|---|---|
| GET | `tournaments/` | 200 | |
| GET | `tournaments/{id}/` | 200 | 404 |

`TournamentListSerializer`:
```json
{
  "id": 12, "title": "ClutchCup Winter #1", "slug": "clutchcup-winter-1",
  "game_name": "Counter-Strike 2", "format": "single_elimination", "status": "open",
  "start_date": "2026-12-12T18:00:00+01:00", "registration_deadline": "2026-12-10T23:59:00+01:00",
  "prize_pool": "1500.00", "entry_fee": "0.00",
  "max_teams": 8, "slots_taken": 5, "slots_left": 3,
  "tags": ["cs2", "5v5", "amateur"]
}
```
`slots_taken` i `slots_left` to `SerializerMethodField` oparte na adnotacji `active_registrations_count` (zgłoszenia `pending` + `confirmed`). `tags` zwraca listę nazw.

`TournamentModelSerializer` (szczegóły i zapis):
```json
{
  "id": 12, "title": "...", "slug": "...", "description": "...",
  "game": {"id": 1, "name": "Counter-Strike 2", "slug": "counter-strike-2", "team_size": 5},
  "game_id": 1,
  "organizer": "org_anna",
  "tags": [{"id": 1, "name": "cs2", "slug": "cs2"}],
  "tag_ids": [1, 2],
  "format": "single_elimination", "max_teams": 8,
  "prize_pool": "1500.00", "entry_fee": "0.00",
  "registration_deadline": "...", "start_date": "...",
  "status": "draft", "is_published": false,
  "slots_taken": 0, "created_at": "..."
}
```
- `organizer`: `ReadOnlyField(source='organizer.username')`.
- `game`, `tags`: zagnieżdżone, tylko do odczytu. `game_id` i `tag_ids` to `PrimaryKeyRelatedField(write_only=True)` (`source='game'` / `source='tags'`, `many=True`).
- Tylko do odczytu: `id`, `slug`, `status`, `slots_taken`, `created_at`.
- `validate_max_teams`: zakres 2–64.
- `validate()` (z obsługą `PATCH` przez `getattr(self.instance, ...)`):
  - `registration_deadline < start_date` → `{"registration_deadline": ["Termin zapisów musi upływać przed rozpoczęciem turnieju."]}`
  - przy tworzeniu oraz przy zmianie dat: `registration_deadline > now` → „Termin zapisów nie może być w przeszłości.”
  - `is_published=False` przy statusie `open`/`ongoing` → „Nie można ukryć turnieju w trakcie zapisów lub rozgrywek.”
  - reguły edycji zależne od statusu (INV-08) → 409 z widoku, nie z serializatora.

### D.2. Tworzenie, edycja, usuwanie

| Metoda | Ścieżka | Auth | Sukces | Błędy |
|---|---|---|---|---|
| POST | `tournaments/` | JWT, rola `organizer` | 201 | 400, 401, 403 |
| PUT/PATCH | `tournaments/{id}/` | JWT, właściciel | 200 | 400, 401, 403, 404, 409 (INV-08) |
| DELETE | `tournaments/{id}/` | JWT, właściciel | 204 | 401, 403, 404, 409 (status ≠ `draft`) |

`perform_create`: `serializer.save(organizer=request.user, status='draft')`.

Filtry `TournamentFilter`:

| Parametr | Lookup |
|---|---|
| `game` | `game__slug` (`exact`) |
| `tags` | `tags__slug` (`ModelMultipleChoiceFilter`, `to_field_name='slug'`, OR) |
| `status`, `format` | `exact` |
| `organizer` | `organizer__username` (`exact`) |
| `prize_pool_min` / `prize_pool_max` | `prize_pool` `gte` / `lte` |
| `entry_fee_min` / `entry_fee_max` | `entry_fee` `gte` / `lte` |
| `max_teams_min` / `max_teams_max` | `max_teams` `gte` / `lte` |
| `start_after` / `start_before` | `start_date` `gte` / `lte` |
| `has_free_slots` | `BooleanFilter`, `method`: `active_registrations_count < max_teams` |

`?search=` po `title`, `description`, `game__name`. `?ordering=` po `start_date`, `registration_deadline`, `prize_pool`, `entry_fee`, `created_at`. Domyślnie `start_date`.

### D.3. `POST tournaments/{id}/open-registration/`
Właściciel. Brak body. Przejście `draft → open` i ustawienie `is_published=True` (INV-08).
**200** → `TournamentModelSerializer`. Błędy: 401, 403, 404, **409** (status ≠ `draft`; `registration_deadline <= now`; organizator bez `is_verified`).

### D.4. `POST tournaments/{id}/start/`
Właściciel. Brak body. Przejście `open → ongoing`, anulowanie zgłoszeń `pending` i wygenerowanie meczów 1. rundy (INV-09).
**200**:
```json
{"detail": "Turniej został rozpoczęty.", "status": "ongoing",
 "cancelled_pending": 1, "matches_created": 2, "byes": ["CK"]}
```
Błędy: 401, 403, 404, **409** (status ≠ `open`; zapisy jeszcze trwają i turniej nie jest pełny; mniej niż 2 potwierdzone drużyny).

### D.5. `GET tournaments/{id}/registrations/`
Publiczny (w granicach widoczności turnieju). Organizator turnieju widzi wszystkie zgłoszenia, pozostali tylko `confirmed`. Paginacja i `TournamentRegistrationSerializer` (sekcja E).

### D.6. `POST tournaments/{id}/register/` — zgłoszenie drużyny

Request: `{"team": 3}` (`TournamentRegistrationCreateSerializer`, `team = PrimaryKeyRelatedField(queryset=Team.objects.all())`).

Sprawdzenia wykonywane **w tej kolejności** (pierwszy niespełniony warunek decyduje o odpowiedzi):

| # | Warunek | Kod | Komunikat |
|---|---|---|---|
| 1 | token | 401 | standard DRF |
| 2 | turniej widoczny dla użytkownika | 404 | standard DRF |
| 3 | `team` istnieje | 400 | standard `PrimaryKeyRelatedField` |
| 4 | `team.captain == request.user` | 403 | „Tylko kapitan drużyny może zgłosić ją do turnieju.” |
| — | *`transaction.atomic()` + `select_for_update()` na turnieju* | | |
| 5 | `status == 'open'` | 409 | „Zapisy do tego turnieju nie są otwarte.” |
| 6 | `now < registration_deadline` | 409 | „Termin zapisów do tego turnieju minął.” |
| 7 | brak zgłoszenia `pending`/`confirmed` tej drużyny | 409 | „Ta drużyna jest już zgłoszona do tego turnieju.” |
| 8 | brak zgłoszenia `cancelled` tej drużyny | 409 | „Zgłoszenie tej drużyny zostało anulowane – ponowna rejestracja do tego turnieju nie jest możliwa.” |
| 9 | rozmiar składu (INV-03) | 400 | `{"team": ["Drużyna musi liczyć od {n} do {n+2} zawodników (gra {gra} wymaga składu {n}-osobowego), a liczy {k}."]}` |
| 10 | żaden członek drużyny nie jest organizatorem turnieju (INV-12) | 400 | `{"team": ["Organizator turnieju nie może występować w zgłaszanej drużynie."]}` |
| 11 | żaden zawodnik nie gra w innej aktywnie zgłoszonej drużynie (INV-04) | 409 | „Zawodnicy {lista nicków} są już zgłoszeni do tego turnieju w innej drużynie.” |
| 12 | wolne miejsce (INV-01) | 409 | „Brak wolnych miejsc – turniej osiągnął limit {max_teams} drużyn.” |

**201**:
```json
{"id": 41, "tournament": 12, "team": {"id": 3, "name": "Clutch Kings", "tag": "CK"},
 "status": "pending", "registered_at": "2026-12-01T10:15:00+01:00", "slots_left": 2}
```
`IntegrityError` z `unique_tournament_team_registration` (wyścig) jest mapowany na **409** z komunikatem z wiersza 7.

### D.7. `GET tournaments/{id}/standings/`
Publiczny. Tabela liczona agregacją `Count`/`Sum` po meczach `completed`:
```json
[{"team_id": 3, "tag": "CK", "played": 3, "wins": 2, "draws": 1, "losses": 0,
  "points": 7, "score_for": 41, "score_against": 30}]
```
Punktacja: wygrana 3, remis 1, porażka 0. Sortowanie: `points` ↓, `(score_for − score_against)` ↓, `tag` ↑. Dla turnieju bez meczów zwraca `[]`.

### D.8. Metody niedozwolone
Akcje `@action` deklarują tylko metody opisane wyżej, każda inna daje **405** (np. `GET tournaments/{id}/register/`).

---

## E. Zgłoszenia: `/tournaments/api/v1/registrations/`

`TournamentRegistrationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet)`. Tworzenie przez `tournaments/{id}/register/`. `POST`, `PUT`, `PATCH` i `DELETE` na kolekcji lub obiekcie zwracają **405**.

**Widoczność** (`IsAuthenticated`, w przeciwnym razie **401**):
```python
Q(team__captain=user) | Q(team__members=user) | Q(tournament__organizer=user)
| Q(status='confirmed', tournament__is_published=True)
```
z `.distinct()`.

`TournamentRegistrationSerializer`:
```json
{"id": 41,
 "tournament": {"id": 12, "title": "ClutchCup Winter #1", "status": "open", "start_date": "..."},
 "team": {"id": 3, "name": "Clutch Kings", "tag": "CK"},
 "status": "confirmed", "registered_at": "...", "updated_at": "...",
 "can_cancel_until": "2026-12-11T18:00:00+01:00"}
```
`can_cancel_until` (`SerializerMethodField`) zwraca ostatni moment, w którym **kapitan** może anulować zgłoszenie (INV-06), albo `null`, jeśli już nie może.

Filtry `RegistrationFilter`: `tournament` (id), `team` (id), `status`, `registered_after` / `registered_before` (`gte`/`lte`).

| Metoda | Ścieżka | Auth | Sukces | Błędy |
|---|---|---|---|---|
| GET | `registrations/` | JWT | 200 | 401 |
| GET | `registrations/{id}/` | JWT | 200 | 401, 404 |
| POST | `registrations/{id}/confirm/` | JWT, organizator turnieju | 200 | 401, 403, 404, 400 (INV-03), 409 |
| POST | `registrations/{id}/cancel/` | JWT, kapitan lub organizator | 200 | 401, 403, 404, 409 |

### E.1. `confirm`
Przejście `pending → confirmed` w `transaction.atomic()` z blokadą turnieju. Warunki: turniej `open` (409 „Zgłoszenia można potwierdzać tylko w trakcie zapisów.”), zgłoszenie `pending` (409 „Można potwierdzić tylko oczekujące zgłoszenie.”), ponowna walidacja składu INV-03 (400) i INV-04 (409). Potwierdzanie po `registration_deadline` jest dozwolone, dopóki trwa status `open`.
**200** → `TournamentRegistrationSerializer`.

### E.2. `cancel`
Przejście `pending|confirmed → cancelled` według reguł czasowych INV-06. Body opcjonalne: `{"reason": "..."}` (maks. 200 znaków, nie jest zapisywane w modelu, trafia tylko do logów).
**200** → `TournamentRegistrationSerializer` (`status: "cancelled"`).

---

## F. Mecze: `/tournaments/api/v1/matches/`

`MatchViewSet(ReadOnlyModelViewSet)` + akcja `report_score`. Widoczne są mecze turniejów widocznych dla użytkownika (ta sama reguła co w D). `POST`, `PUT`, `PATCH` i `DELETE` → **405**.

`MatchSerializer`:
```json
{"id": 90, "tournament": 12, "round_number": 1,
 "team_a": {"id": 3, "tag": "CK", "name": "Clutch Kings"},
 "team_b": {"id": 5, "tag": "AIM", "name": "Aim Academy"},
 "score_a": 13, "score_b": 9, "winner": 3, "status": "completed",
 "completed_at": "..."}
```
Filtry `MatchFilter`: `tournament`, `round_number`, `round_min` / `round_max` (`gte`/`lte`), `status`, `team` (`method`: `Q(team_a=v) | Q(team_b=v)`).

### F.1. `POST matches/{id}/report-score/`
Organizator turnieju (`IsTournamentOwner` na `obj.tournament`). Request `MatchScoreSerializer`: `{"score_a": 13, "score_b": 9}`, oba pola `IntegerField(min_value=0, max_value=999)`.

| Warunek | Kod | Komunikat |
|---|---|---|
| turniej `ongoing` | 409 | „Wyniki można wprowadzać tylko w trwającym turnieju.” |
| mecz `scheduled` | 409 | „Wynik tego meczu został już wprowadzony.” |
| `single_elimination` i remis | 400 | `{"non_field_errors": ["W formacie pucharowym mecz nie może zakończyć się remisem."]}` |

**200** → `MatchSerializer` + pola `"round_completed": bool`, `"next_round_created": bool`, `"tournament_finished": bool` (INV-09, INV-11).

---

## G. Podsumowanie: `GET /tournaments/api/v1/summary/`

`TournamentSummaryView(APIView)`, `AllowAny`. Liczy wyłącznie turnieje widoczne publicznie (`is_published=True`).
```json
{
  "tournaments_total": 24,
  "tournaments_by_status": {"draft": 2, "open": 6, "ongoing": 3, "finished": 13},
  "prize_pool": {"total": "48000.00", "avg": "2000.00", "min": "0.00", "max": "15000.00"},
  "entry_fee": {"avg": "25.00", "min": "0.00", "max": "100.00"},
  "avg_max_teams": 14.7,
  "registrations": {"active": 120, "cancelled": 9},
  "top_games": [{"game": "Counter-Strike 2", "tournaments": 11}]
}
```
Agregacje: `Count`, `Sum`, `Avg`, `Min`, `Max`. `top_games` to 5 gier z największą liczbą turniejów (`annotate(Count)` + `order_by('-tournaments', 'name')[:5]`). Dla pustej bazy wartości liczbowe wynoszą `null` (kwoty) lub `0` (liczniki).

---

## H. Zbiorcza macierz uprawnień

| Endpoint | Anonim | Gracz | Kapitan drużyny | Organizator (cudzy turniej) | Organizator (własny) | Admin |
|---|---|---|---|---|---|---|
| GET games/tags/teams/tournaments | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| POST/PUT/DELETE games, tags | 401 | 403 | 403 | 403 | 403 | ✔ |
| POST teams | 401 | ✔ | ✔ | 403 | 403 | 403* |
| PUT/PATCH/DELETE teams/{id} | 401 | 403 | ✔ | 403 | 403 | 403* |
| POST tournaments | 401 | 403 | 403 | ✔ | ✔ | 403* |
| PUT/PATCH/DELETE, open-registration, start | 401 | 403 | 403 | 403 | ✔ | 403* |
| POST tournaments/{id}/register | 401 | 403 (nie kapitan) | ✔ | 403 (nie kapitan) | 403 (nie kapitan) / 400 (INV-12) | – |
| GET registrations | 401 | ✔ (swoje) | ✔ (swoje) | ✔ (swoje + publiczne) | ✔ | ✔ (wg reguły) |
| POST registrations/{id}/confirm | 401 | 403 | 403 | 403 | ✔ | 403* |
| POST registrations/{id}/cancel | 401 | 403 | ✔ (INV-06) | 403 | ✔ (INV-06) | 403* |
| POST matches/{id}/report-score | 401 | 403 | 403 | 403 | ✔ | 403* |

\* Admin działa w API według swojej roli w profilu. Pełne uprawnienia ma w panelu `admin/`.
