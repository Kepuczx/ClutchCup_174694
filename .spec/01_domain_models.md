# 01 — Modele domenowe ClutchCup

Wszystkie modele są w `tournaments/models.py`. `User` to `django.contrib.auth.models.User` (bez własnego modelu użytkownika).

## 0. Stałe modułu

```python
MAX_SUBSTITUTES = 2                        # maks. liczba rezerwowych ponad game.team_size
MAX_TEAM_MEMBERS = 12                      # twardy limit członków drużyny
MIN_TEAMS_TO_START = 2
CANCELLATION_CUTOFF = timedelta(hours=24)  # patrz 03, INV-06
```

### Slugi

`Game`, `Tag`, `Team` i `Tournament` mają `slug` z `blank=True, unique=True`. Jeśli przy zapisie `slug` jest pusty, `save()` ustawia `slugify(<nazwa/tytuł>)`, a w razie kolizji dokleja `-2`, `-3`, … W API `slug` jest **tylko do odczytu** i po utworzeniu się nie zmienia (zmiana nazwy nie przelicza sluga).

## 1. Diagram relacji

```text
User 1───1 UserProfile
User 1───* Team (captain)            User *───* Team (members)
User 1───* Tournament (organizer)
Game 1───* Tournament                Tag *───* Tournament
Tournament 1───* TournamentRegistration *───1 Team
Tournament 1───* Match *───1 Team (team_a / team_b / winner)
```

## 2. `UserProfile`

| Pole | Typ | Parametry |
|---|---|---|
| `user` | `OneToOneField(User)` | `on_delete=CASCADE`, `related_name='profile'`, `verbose_name='użytkownik'` |
| `nickname` | `CharField` | `max_length=32`, `unique=True`, `MinLengthValidator(3)`, `verbose_name='pseudonim'` |
| `role` | `CharField` | `max_length=16`, `choices=Role.choices`, `default=Role.PLAYER`, `verbose_name='rola'` |
| `steam_id` | `CharField` | `max_length=17`, `blank=True`, `default=''`, `RegexValidator(r'^7656119\d{10}$', 'Nieprawidłowy SteamID64 (17 cyfr, prefiks 7656119).')`, `verbose_name='SteamID64'` |
| `is_verified` | `BooleanField` | `default=False`, `verbose_name='zweryfikowany'` |

```python
class Role(models.TextChoices):
    ORGANIZER = 'organizer', 'Organizator'
    PLAYER = 'player', 'Gracz'
```

- `Meta.ordering = ['nickname']`, `verbose_name = 'profil użytkownika'`, `verbose_name_plural = 'profile użytkowników'`.
- `Meta.constraints`: `UniqueConstraint(fields=['steam_id'], condition=~Q(steam_id=''), name='unique_nonempty_steam_id')`.
- `__str__`: `f'{self.nickname} ({self.get_role_display()})'`.
- Profil powstaje **w tej samej transakcji** co `User`, w `UserRegisterSerializer.create()` (bez sygnałów). Użytkownik utworzony przez `createsuperuser` może nie mieć profilu. Kod sprawdzający rolę musi wtedy zwrócić `False` (`getattr(user, 'profile', None)`), a nie rzucić wyjątek.
- `is_verified` zmienia wyłącznie admin.

## 3. `Game`

| Pole | Typ | Parametry |
|---|---|---|
| `name` | `CharField` | `max_length=100`, `unique=True`, `verbose_name='nazwa'` |
| `slug` | `SlugField` | `max_length=110`, `unique=True`, `blank=True`, `verbose_name='slug'` |
| `team_size` | `PositiveSmallIntegerField` | `default=5`, `MinValueValidator(1)`, `MaxValueValidator(10)`, `verbose_name='rozmiar składu'` |
| `description` | `TextField` | `blank=True`, `verbose_name='opis'` |

- `Meta.ordering = ['name']`, `verbose_name = 'gra'`, `verbose_name_plural = 'gry'`.
- `Meta.constraints`: `CheckConstraint(condition=Q(team_size__gte=1) & Q(team_size__lte=10), name='game_team_size_range')`.
- `__str__`: `self.name`.
- `team_size` jest **niezmienny**, jeśli istnieje turniej tej gry w statusie `open` lub `ongoing` (INV-05).

## 4. `Tag`

| Pole | Typ | Parametry |
|---|---|---|
| `name` | `CharField` | `max_length=30`, `unique=True`, `RegexValidator(r'^[a-z0-9]+$', 'Tag może zawierać tylko małe litery i cyfry, bez znaku #.')`, `verbose_name='nazwa'` |
| `slug` | `SlugField` | `max_length=40`, `unique=True`, `blank=True` |

- `Meta.ordering = ['name']`, `verbose_name = 'tag'`, `verbose_name_plural = 'tagi'`.
- `__str__`: `f'#{self.name}'`. Przykłady danych: `cs2`, `5v5`, `lan`, `amateur`.

## 5. `Team`

| Pole | Typ | Parametry |
|---|---|---|
| `name` | `CharField` | `max_length=64`, `verbose_name='nazwa'` |
| `slug` | `SlugField` | `max_length=80`, `unique=True`, `blank=True` |
| `tag` | `CharField` | `max_length=5`, `RegexValidator(r'^[A-Z0-9]{2,5}$', 'Skrót drużyny to 2–5 wielkich liter lub cyfr.')`, `verbose_name='skrót'` |
| `captain` | `ForeignKey(User)` | `on_delete=PROTECT`, `related_name='captained_teams'`, `verbose_name='kapitan'` |
| `members` | `ManyToManyField(User)` | `related_name='teams'`, `blank=True`, `verbose_name='zawodnicy'` |
| `created_at` | `DateTimeField` | `auto_now_add=True`, `verbose_name='utworzono'` |

- `Meta.ordering = ['name']`, `verbose_name = 'drużyna'`, `verbose_name_plural = 'drużyny'`.
- `Meta.constraints`:
  - `UniqueConstraint(Lower('name'), name='unique_team_name_ci')`
  - `UniqueConstraint(fields=['tag'], name='unique_team_tag')`
- `__str__`: `f'[{self.tag}] {self.name}'`.
- Niezmienniki (egzekwowane w serializatorze i widoku, bo w M2M nie da się ich wyrazić constraintem):
  - kapitan zawsze należy do `members` (dodawany automatycznie w `perform_create` i przy każdej zmianie składu),
  - `1 <= members.count() <= MAX_TEAM_MEMBERS`.
- Drużyna **nie jest związana z grą**. Zgodność składu z grą sprawdza się w momencie zgłoszenia do turnieju (INV-03).
- `DRF` nie generuje walidatora dla constraintu wyrażeniowego `Lower('name')`, więc `TeamModelSerializer.validate_name()` sprawdza `Team.objects.filter(name__iexact=value).exclude(pk=instance.pk)` i zwraca błąd „Drużyna o tej nazwie już istnieje.”

## 6. `Tournament`

| Pole | Typ | Parametry |
|---|---|---|
| `title` | `CharField` | `max_length=150`, `MinLengthValidator(5)`, `verbose_name='tytuł'` |
| `slug` | `SlugField` | `max_length=170`, `unique=True`, `blank=True` |
| `game` | `ForeignKey(Game)` | `on_delete=PROTECT`, `related_name='tournaments'`, `verbose_name='gra'` |
| `organizer` | `ForeignKey(User)` | `on_delete=PROTECT`, `related_name='organized_tournaments'`, `verbose_name='organizator'` |
| `tags` | `ManyToManyField(Tag)` | `related_name='tournaments'`, `blank=True`, `verbose_name='tagi'` |
| `description` | `TextField` | `blank=True`, `verbose_name='opis'` |
| `format` | `CharField` | `max_length=32`, `choices=Format.choices`, `default=Format.SINGLE_ELIMINATION`, `verbose_name='format'` |
| `max_teams` | `PositiveSmallIntegerField` | `default=16`, `MinValueValidator(2)`, `MaxValueValidator(64)`, `verbose_name='limit drużyn'` |
| `prize_pool` | `DecimalField` | `max_digits=10`, `decimal_places=2`, `default=Decimal('0.00')`, `MinValueValidator(Decimal('0'))`, `verbose_name='pula nagród'` |
| `entry_fee` | `DecimalField` | `max_digits=8`, `decimal_places=2`, `default=Decimal('0.00')`, `MinValueValidator(Decimal('0'))`, `verbose_name='wpisowe'` |
| `registration_deadline` | `DateTimeField` | `verbose_name='koniec zapisów'` |
| `start_date` | `DateTimeField` | `verbose_name='data rozpoczęcia'` |
| `status` | `CharField` | `max_length=16`, `choices=Status.choices`, `default=Status.DRAFT`, `verbose_name='status'` |
| `is_published` | `BooleanField` | `default=False`, `verbose_name='opublikowany'` |
| `created_at` | `DateTimeField` | `auto_now_add=True` |

```python
class Format(models.TextChoices):
    SINGLE_ELIMINATION = 'single_elimination', 'Pojedyncza eliminacja'
    ROUND_ROBIN = 'round_robin', 'Każdy z każdym'

class Status(models.TextChoices):
    DRAFT = 'draft', 'Szkic'
    OPEN = 'open', 'Zapisy otwarte'
    ONGOING = 'ongoing', 'W trakcie'
    FINISHED = 'finished', 'Zakończony'
```

- `Meta.ordering = ['start_date', 'id']`, `verbose_name = 'turniej'`, `verbose_name_plural = 'turnieje'`.
- `Meta.constraints` (w Django 6 parametr nazywa się `condition`, nie `check`):
  - `CheckConstraint(condition=Q(registration_deadline__lt=F('start_date')), name='tournament_deadline_before_start')`
  - `CheckConstraint(condition=Q(max_teams__gte=2) & Q(max_teams__lte=64), name='tournament_max_teams_range')`
  - `CheckConstraint(condition=Q(prize_pool__gte=0) & Q(entry_fee__gte=0), name='tournament_non_negative_money')`
  - `CheckConstraint(condition=Q(is_published=True) | Q(status__in=['draft', 'finished']), name='tournament_active_must_be_published')`
- `Meta.indexes`: `Index(fields=['status', 'is_published'])`, `Index(fields=['start_date'])`.
- `__str__`: `f'{self.title} ({self.game.name})'`.

### 6.1. Znaczenie `status` × `is_published`

| `status` | `is_published` | Znaczenie | Widoczny dla innych |
|---|---|---|---|
| `draft` | `False` | prywatny szkic | nie |
| `draft` | `True` | zapowiedź, zapisy jeszcze zamknięte | tak |
| `open` | `True` (wymuszone) | zapisy trwają | tak |
| `ongoing` | `True` (wymuszone) | rozgrywki trwają | tak |
| `finished` | `True` | archiwum publiczne | tak |
| `finished` | `False` | archiwum ukryte | nie |

### 6.2. Maszyna stanów

```text
draft ──open-registration──▶ open ──start──▶ ongoing ──(ostatni mecz zakończony)──▶ finished
```

Przejść wstecz nie ma. `status` jest polem **tylko do odczytu** w API i zmieniają go wyłącznie akcje (INV-08).

## 7. `TournamentRegistration`

| Pole | Typ | Parametry |
|---|---|---|
| `tournament` | `ForeignKey(Tournament)` | `on_delete=CASCADE`, `related_name='registrations'`, `verbose_name='turniej'` |
| `team` | `ForeignKey(Team)` | `on_delete=PROTECT`, `related_name='registrations'`, `verbose_name='drużyna'` |
| `status` | `CharField` | `max_length=16`, `choices=Status.choices`, `default=Status.PENDING`, `verbose_name='status'` |
| `registered_at` | `DateTimeField` | `auto_now_add=True`, `verbose_name='data zgłoszenia'` |
| `updated_at` | `DateTimeField` | `auto_now=True`, `verbose_name='ostatnia zmiana'` |

```python
class Status(models.TextChoices):
    PENDING = 'pending', 'Oczekujące'
    CONFIRMED = 'confirmed', 'Potwierdzone'
    CANCELLED = 'cancelled', 'Anulowane'

ACTIVE_STATUSES = (Status.PENDING, Status.CONFIRMED)
```

- `Meta.ordering = ['registered_at', 'id']` (to także kolejność rozstawienia, patrz `03`, INV-09), `verbose_name = 'zgłoszenie'`, `verbose_name_plural = 'zgłoszenia'`.
- `Meta.constraints`: `UniqueConstraint(fields=['tournament', 'team'], name='unique_tournament_team_registration')`. Para jest unikalna **bez względu na status**, więc anulowanego zgłoszenia nie da się ponowić (INV-07).
- `__str__`: `f'{self.team} → {self.tournament.title} [{self.get_status_display()}]'`.
- Przejścia: `pending → confirmed`, `pending → cancelled`, `confirmed → cancelled`. Stan `cancelled` jest końcowy.

## 8. `Match`

| Pole | Typ | Parametry |
|---|---|---|
| `tournament` | `ForeignKey(Tournament)` | `on_delete=CASCADE`, `related_name='matches'` |
| `team_a` | `ForeignKey(Team)` | `on_delete=PROTECT`, `related_name='matches_as_team_a'`, `verbose_name='drużyna A'` |
| `team_b` | `ForeignKey(Team)` | `on_delete=PROTECT`, `related_name='matches_as_team_b'`, `verbose_name='drużyna B'` |
| `winner` | `ForeignKey(Team)` | `on_delete=PROTECT`, `null=True`, `blank=True`, `related_name='won_matches'`, `verbose_name='zwycięzca'` |
| `round_number` | `PositiveSmallIntegerField` | `MinValueValidator(1)`, `verbose_name='runda'` |
| `score_a` | `PositiveSmallIntegerField` | `default=0`, `verbose_name='wynik A'` |
| `score_b` | `PositiveSmallIntegerField` | `default=0`, `verbose_name='wynik B'` |
| `status` | `CharField` | `max_length=16`, `choices=Status.choices`, `default=Status.SCHEDULED` |
| `completed_at` | `DateTimeField` | `null=True`, `blank=True` |

```python
class Status(models.TextChoices):
    SCHEDULED = 'scheduled', 'Zaplanowany'
    COMPLETED = 'completed', 'Rozegrany'
```

- `Meta.ordering = ['tournament', 'round_number', 'id']`, `verbose_name = 'mecz'`, `verbose_name_plural = 'mecze'`.
- `Meta.constraints`:
  - `CheckConstraint(condition=~Q(team_a=F('team_b')), name='match_distinct_teams')`
  - `CheckConstraint(condition=Q(winner__isnull=True) | Q(winner=F('team_a')) | Q(winner=F('team_b')), name='match_winner_is_participant')`
  - `CheckConstraint(condition=Q(status='scheduled', winner__isnull=True, completed_at__isnull=True) | Q(status='completed', completed_at__isnull=False), name='match_status_consistency')`
- `__str__`: `f'R{self.round_number}: {self.team_a.tag} vs {self.team_b.tag}'`.
- Mecze tworzy **wyłącznie system** (akcja `start` i automatyczne przejście do kolejnej rundy). API nie udostępnia ich tworzenia ani usuwania.
- `winner = NULL` przy `status = completed` jest dopuszczalne tylko w formacie `round_robin` (remis).

## 9. Panel administracyjny (`admin.py`)

| Model | `list_display` | `list_filter` | `search_fields` | Inne |
|---|---|---|---|---|
| `UserProfile` | `nickname`, `user`, `role`, `is_verified` | `role`, `is_verified` | `nickname`, `user__username`, `steam_id` | `list_editable = ('is_verified',)` |
| `Game` | `name`, `team_size`, `tournaments_count` | `team_size` | `name` | `prepopulated_fields = {'slug': ('name',)}`, `@admin.display(description='Liczba turniejów')` |
| `Tag` | `__str__`, `slug` | – | `name` | `prepopulated_fields` |
| `Team` | `name`, `tag`, `captain`, `members_count`, `created_at` | `created_at` | `name`, `tag`, `captain__username` | `filter_horizontal = ('members',)`, `prepopulated_fields` |
| `Tournament` | `title`, `game`, `organizer`, `status`, `is_published`, `start_date`, `slots_display` | `status`, `format`, `game`, `is_published`, `tags` | `title`, `description` | `filter_horizontal = ('tags',)`, `list_editable = ('is_published',)`, `fieldsets` (Podstawowe / Zasady / Terminy / Publikacja), `@admin.display(description='Zajęte miejsca')` |
| `TournamentRegistration` | `team`, `tournament`, `status`, `registered_at` | `status`, `tournament` | `team__name`, `tournament__title` | `list_select_related` |
| `Match` | `__str__`, `tournament`, `round_number`, `score_a`, `score_b`, `winner`, `status` | `status`, `tournament` | `team_a__name`, `team_b__name` | `list_select_related` |

Uwaga: zmiany w panelu admina **omijają** reguły z `03` egzekwowane w widokach API. Panel służy do administracji i naprawy danych, a nie do normalnej obsługi turnieju.
