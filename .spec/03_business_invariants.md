# 03 — Niezmienniki i reguły biznesowe ClutchCup

Reguły z tego pliku są **żelazne**. Żaden endpoint API nie może doprowadzić bazy do stanu, który je narusza, niezależnie od kolejności i współbieżności żądań. Każda reguła ma identyfikator `INV-xx`, do którego odwołują się `02_api_contracts.md` i testy.

Oznaczenia:
- `now` = `django.utils.timezone.now()`, wartość pobrana **raz** na początku obsługi żądania, już po założeniu blokady.
- „aktywne zgłoszenie” = `TournamentRegistration` ze statusem `pending` lub `confirmed`.
- `T` = turniej, `R` = zgłoszenie, `n` = `T.game.team_size`.

---

## INV-01 — Limit drużyn nigdy nie jest przekroczony

**Niezmiennik:** dla każdego turnieju w każdej chwili
`T.registrations.filter(status__in=ACTIVE_STATUSES).count() <= T.max_teams`.

**Egzekwowanie:** akcja `POST tournaments/{id}/register/`:

```python
with transaction.atomic():
    tournament = (
        Tournament.objects
        .select_for_update()
        .select_related('game')
        .get(pk=pk)
    )
    now = timezone.now()
    # ... INV-02, INV-07, INV-03, INV-12, INV-04 ...
    active = tournament.registrations.filter(
        status__in=TournamentRegistration.ACTIVE_STATUSES
    ).count()
    if active >= tournament.max_teams:
        raise ConflictError(
            f'Brak wolnych miejsc – turniej osiągnął limit {tournament.max_teams} drużyn.'
        )
    registration = TournamentRegistration.objects.create(
        tournament=tournament, team=team, status=TournamentRegistration.Status.PENDING
    )
```

Zasady:
1. Licznik miejsc jest **liczony w bazie wewnątrz transakcji**, po blokadzie. Nie wolno opierać się na adnotacji `active_registrations_count` z `get_queryset()` ani na wartości z serializatora.
2. Liczą się zarówno `pending`, jak i `confirmed`. Oczekujące zgłoszenie rezerwuje miejsce, więc organizator nie może „przepotwierdzić” turnieju.
3. Na `select_for_update()` nie wolno nakładać `prefetch_related`. Dozwolone jest `select_related` do relacji niepustych (`game`).
4. **SQLite:** Django ignoruje `select_for_update()` dla SQLite, bo silnik nie obsługuje `FOR UPDATE`. Ochronę zapewnia ustawienie `DATABASES['default']['OPTIONS']['transaction_mode'] = 'IMMEDIATE'`. Każde `atomic()` zaczyna się wtedy od `BEGIN IMMEDIATE`, które od razu zakłada blokadę zapisu. Dwie równoległe rejestracje wykonują się więc sekwencyjnie, a druga widzi już zgłoszenie pierwszej. `timeout: 20` sprawia, że druga transakcja czeka, zamiast od razu dostać `database is locked`. `select_for_update()` zostaje w kodzie, bo dokumentuje intencję i chroni po zmianie silnika.
5. Ten sam licznik obowiązuje przy **zmniejszaniu `max_teams`** w turnieju `open` (INV-08): edycja w `atomic()` z blokadą, a `nowe max_teams >= active` (w przeciwnym razie 400 `{"max_teams": ["Limit nie może być mniejszy niż liczba aktywnych zgłoszeń ({active})."]}`).

**Testy:** (a) `max_teams=2`, dwie rejestracje dają 201, trzecia 409, a liczba zgłoszeń w bazie nadal wynosi 2; (b) po anulowaniu jednego zgłoszenia kolejna rejestracja dostaje 201; (c) `PATCH max_teams=1` przy 2 aktywnych daje 400.

---

## INV-02 — Okno zapisów

Zgłoszenie można utworzyć wyłącznie, gdy jednocześnie:
- `T.status == 'open'` (to implikuje `T.is_published == True`) → inaczej **409** „Zapisy do tego turnieju nie są otwarte.”
- `now < T.registration_deadline` (ostra nierówność: w chwili `== deadline` zapisy są już zamknięte) → inaczej **409** „Termin zapisów do tego turnieju minął.”

Upływ `registration_deadline` **nie zmienia** statusu automatycznie, bo w projekcie nie ma zadań w tle. Turniej pozostaje `open`, ale reguła czasowa blokuje nowe zgłoszenia. Status zmienia dopiero akcja `start`.

**Testy:** rejestracja do `draft` daje 409; do `open` po terminie daje 409; do `ongoing` daje 409.

---

## INV-03 — Rozmiar składu względem `game.team_size`

**Reguła:** drużyna może być aktywnie zgłoszona do turnieju tylko wtedy, gdy
`n <= team.members.count() <= n + MAX_SUBSTITUTES` (gdzie `MAX_SUBSTITUTES = 2`).

Przykłady dla `team_size = 5`: 4 zawodników to za mało, 5–7 jest poprawne, 8 to za dużo.
Dla `team_size = 1` (gry 1v1): 1–3 zawodników.

**Egzekwowanie:**
- przy `register`, wewnątrz transakcji, na świeżo policzonym `team.members.count()` → **400** `{"team": ["Drużyna musi liczyć od {n} do {n + 2} zawodników (gra {game.name} wymaga składu {n}-osobowego), a liczy {k}."]}`,
- **ponownie przy `confirm`**, bo skład drużyny z zgłoszeniem `pending` może się zmienić (INV-04). Błąd jest taki sam, a zgłoszenie zostaje `pending`.

Kapitan wlicza się do składu.

**Testy:** skład `n−1` daje 400; `n` daje 201; `n+2` daje 201; `n+3` daje 400; skład zmniejszony po rejestracji sprawia, że `confirm` daje 400, a status pozostaje `pending`.

---

## INV-04 — Integralność składów w obrębie turnieju

1. **Jeden zawodnik, jedna drużyna na turniej.** Użytkownik nie może należeć do dwóch drużyn, które mają aktywne zgłoszenia w tym samym turnieju. Sprawdzane przy `register` i `confirm`:
   ```python
   other_registrations = TournamentRegistration.objects.filter(
       tournament=tournament,
       status__in=TournamentRegistration.ACTIVE_STATUSES,
   ).exclude(team=team)
   clashing = (
       team.members
       .filter(teams__registrations__in=other_registrations)
       .select_related('profile')
       .distinct()
   )
   ```
   Łańcuch `teams__registrations__in` wiąże użytkownika z **tą samą** drużyną, która ma aktywne zgłoszenie. Wykluczenie `team=team` odrzuca zgłoszenie bieżącej drużyny. Naruszenie daje **409** „Zawodnicy {nick1, nick2} są już zgłoszeni do tego turnieju w innej drużynie.”
2. **Zamrożenie składu.** Jeśli drużyna ma **potwierdzone** zgłoszenie do turnieju w statusie `open` lub `ongoing`, zmiana `member_ids` (PUT/PATCH `teams/{id}/`) daje **409** „Skład drużyny jest zamrożony, ponieważ drużyna ma potwierdzone zgłoszenie do trwającego turnieju.” Zmiana `name`/`tag` pozostaje dozwolona. Przy zgłoszeniach `pending` skład można zmieniać, a poprawność ponownie weryfikuje `confirm`.
3. Kapitan zawsze należy do `members`. Usunięcie go z `member_ids` jest ignorowane, bo kapitan jest dopisywany ponownie.

---

## INV-05 — Niezmienność rozmiaru składu gry

`Game.team_size` nie może się zmienić, jeśli istnieje turniej tej gry w statusie `open` lub `ongoing`, bo unieważniłoby to potwierdzone zgłoszenia. Naruszenie daje **409** „Nie można zmienić rozmiaru składu gry, która ma turnieje z otwartymi zapisami lub w trakcie.”

---

## INV-06 — Reguły czasowe anulowania zgłoszenia

Anulowanie (`POST registrations/{id}/cancel/`) wykonuje się w `transaction.atomic()` z blokadą `select_for_update()` na **turnieju**, a następnie na zgłoszeniu (kolejność blokad z `00`, §9). Dzięki temu anulowanie nie przeplata się z akcją `start`.

Stała: `CANCELLATION_CUTOFF = timedelta(hours=24)`.

| Kto | Status zgłoszenia | Status turnieju | Warunek czasowy | Wynik |
|---|---|---|---|---|
| dowolny | `cancelled` | dowolny | – | **409** „To zgłoszenie zostało już anulowane.” |
| dowolny | dowolny | `ongoing` / `finished` | – | **409** „Nie można anulować zgłoszenia po rozpoczęciu turnieju.” |
| kapitan | `pending` | `open` | brak (do momentu startu) | **200**, `cancelled` |
| kapitan | `confirmed` | `open` | `now < T.start_date − CANCELLATION_CUTOFF` | **200**, `cancelled` |
| kapitan | `confirmed` | `open` | `now >= T.start_date − CANCELLATION_CUTOFF` | **409** „Potwierdzone zgłoszenie można anulować najpóźniej 24 godziny przed rozpoczęciem turnieju. Skontaktuj się z organizatorem.” |
| organizator turnieju | `pending` / `confirmed` | `open` | brak (do momentu startu) | **200**, `cancelled` (odrzucenie lub wykluczenie) |
| inny użytkownik | – | – | – | **403** (`IsRegistrationParty`) lub **404** (zgłoszenie niewidoczne) |

Uwagi:
- Granica jest ostra: dokładnie w chwili `start_date − 24h` kapitan **nie może** już anulować potwierdzonego zgłoszenia.
- Jeśli użytkownik jest jednocześnie kapitanem i organizatorem, stosuje się regułę organizatora. Przy INV-12 taka sytuacja nie powinna wystąpić.
- `can_cancel_until` w `TournamentRegistrationSerializer` dla kapitana zwraca `T.start_date − CANCELLATION_CUTOFF` (zgłoszenie `confirmed`) lub `T.start_date` (zgłoszenie `pending`), a `null`, gdy anulowanie nie jest już możliwe.
- Anulowanie zwalnia miejsce w limicie INV-01 natychmiast po zatwierdzeniu transakcji.

**Testy:** kapitan anuluje `pending` dzień przed startem → 200; kapitan anuluje `confirmed` 48h przed startem → 200; kapitan anuluje `confirmed` 23h przed startem → 409 i status bez zmian; organizator anuluje `confirmed` 1h przed startem → 200; ponowne anulowanie → 409; anulowanie w `ongoing` → 409; obcy użytkownik → 403/404.

---

## INV-07 — Jedno zgłoszenie na parę turniej–drużyna

- W bazie obowiązuje `UniqueConstraint(fields=['tournament', 'team'])` **bez warunku na status**.
- Anulowanie jest ostateczne: drużyna, której zgłoszenie anulowano, nie może ponownie zgłosić się do tego samego turnieju (**409**, komunikat z `02`, D.6 wiersz 8). Chroni to przed nadużyciem „anuluj i zapisz się ponownie”, które zmieniałoby kolejność rozstawienia (INV-09).
- `IntegrityError` z tego constraintu (np. dwa równoległe żądania tej samej drużyny) jest przechwytywany i mapowany na **409**, a nie na 500.

---

## INV-08 — Cykl życia turnieju i reguły edycji

**Przejścia** (wyłącznie przez akcje, pole `status` jest tylko do odczytu):

| Akcja | Z | Do | Warunki (inaczej **409**) |
|---|---|---|---|
| `open-registration` | `draft` | `open` | `now < registration_deadline` („Nie można otworzyć zapisów – termin zapisów już minął.”); `organizer.profile.is_verified` („Tylko zweryfikowany organizator może otworzyć zapisy.”). Ustawia `is_published = True`. |
| `start` | `open` | `ongoing` | patrz INV-09 |
| automatycznie w `report-score` | `ongoing` | `finished` | patrz INV-11 |

Akcja wywołana w złym statusie daje **409** „Operacja niedozwolona dla turnieju w statusie {status_display}.”

**Edycja (PUT/PATCH) i usuwanie:**

| Status | Edycja | Usunięcie |
|---|---|---|
| `draft` | wszystkie pola zapisywalne | dozwolone (**204**) |
| `open` | dozwolone: `title`, `description`, `tag_ids`, `prize_pool` (tylko w górę), `max_teams` (INV-01 pkt 5), `start_date` / `registration_deadline` (tylko przesunięcie w przód i z zachowaniem `deadline < start`). Zablokowane: `game_id`, `format`, `entry_fee`, `is_published=False` | **409** |
| `ongoing` / `finished` | **409** dla każdej zmiany, z wyjątkiem `is_published` w `finished` | **409** |

Komunikaty: „Pola {lista} nie mogą być zmieniane po otwarciu zapisów.”, „Pulę nagród można jedynie zwiększyć po otwarciu zapisów.”, „Turnieju w statusie {status_display} nie można edytować.”, „Można usunąć wyłącznie turniej w statusie szkicu.”

---

## INV-09 — Start turnieju i generowanie pierwszej rundy

`POST tournaments/{id}/start/` w `transaction.atomic()` z blokadą turnieju:

1. `T.status == 'open'`, inaczej 409.
2. `now >= T.registration_deadline` **lub** `confirmed_count == T.max_teams`, inaczej 409 „Turniej można rozpocząć po zakończeniu zapisów lub po zapełnieniu wszystkich miejsc potwierdzonymi drużynami.”
3. `confirmed_count >= MIN_TEAMS_TO_START` (2), inaczej 409 „Do rozpoczęcia turnieju potrzebne są co najmniej 2 potwierdzone drużyny.” Turniej pozostaje `open`.
4. Wszystkie zgłoszenia `pending` → `cancelled` (jednym `update()`).
5. Rozstawienie: potwierdzone drużyny w kolejności `registered_at, id` → `seeds[0..k-1]`.
6. Generowanie meczów (`bulk_create`, `round_number=1`):
   - **`single_elimination`:** `P` = najmniejsza potęga 2 `>= k`, `byes = P − k`. Drużyny `seeds[0..byes-1]` dostają wolny los (bez meczu w rundzie 1). Pozostałe `r = seeds[byes:]` grają w parach `(r[i], r[len(r)-1-i])` dla `i < len(r)/2`. Liczba `len(r) = 2k − P` jest zawsze parzysta.
   - **`round_robin`:** algorytm kołowy (circle method). Przy nieparzystym `k` dodaje się „pauzę” (`None`), a pary z `None` są pomijane. Powstaje `k−1` rund (lub `k` przy nieparzystym `k`), wszystkie mecze od razu, `round_number` = numer rundy. Łącznie `k(k−1)/2` meczów.
7. `T.status = 'ongoing'`.

Wynik jest deterministyczny: te same dane wejściowe zawsze dają te same pary.

**Testy:** 4 drużyny SE → 2 mecze; 3 drużyny SE → 1 mecz + 1 bye (`seeds[0]`); 4 drużyny RR → 6 meczów w 3 rundach; 1 potwierdzona → 409 i brak meczów; start przed terminem przy niepełnym turnieju → 409; `pending` po starcie mają status `cancelled`.

---

## INV-10 — Ochrona historii

- `Team` z jakimkolwiek zgłoszeniem lub meczem nie może zostać usunięty (`on_delete=PROTECT`). `ProtectedError` jest mapowany na **409** „Nie można usunąć drużyny, która uczestniczyła w turniejach.”
- `Game` z turniejami: **409** „Nie można usunąć gry, do której przypisano turnieje.”
- `User` będący organizatorem lub kapitanem jest chroniony przez `PROTECT` (dotyczy tylko panelu admina, bo API nie usuwa kont).

---

## INV-11 — Wyniki meczów i zakończenie turnieju

`POST matches/{id}/report-score/` w `transaction.atomic()`: blokada turnieju, potem meczu.

1. `T.status == 'ongoing'` i `match.status == 'scheduled'`, inaczej 409 (`02`, F.1).
2. `single_elimination`: `score_a != score_b`, inaczej 400. `round_robin`: remis dozwolony (`winner = None`).
3. `winner` = drużyna z wyższym wynikiem; `status = 'completed'`; `completed_at = now`.
4. **Przejście rundy (`single_elimination`)**, jeśli wszystkie mecze rundy `r` są `completed`:
   - uczestnicy rundy `r+1`: dla `r == 1` najpierw drużyny z wolnym losem (w kolejności rozstawienia), potem zwycięzcy meczów rundy 1 (wg `match.id`); dla `r >= 2` zwycięzcy meczów rundy `r` (wg `match.id`);
   - pary kolejne: `(u[0], u[1]), (u[2], u[3]), …`;
   - jeśli runda `r` miała dokładnie 1 mecz (finał), nowa runda nie powstaje i `T.status = 'finished'`.
5. **`round_robin`:** gdy wszystkie mecze turnieju są `completed` → `T.status = 'finished'`.
6. Wynik jest niezmienny. Korekta jest możliwa tylko w panelu admina i nie przelicza drabinki.

**Testy:** wynik w `open` → 409; ponowny wynik → 409; remis w SE → 400; ukończenie obu półfinałów tworzy finał; wynik finału zmienia turniej na `finished`; obcy organizator → 403.

---

## INV-12 — Konflikt interesów

Żaden członek zgłaszanej drużyny (w tym kapitan) nie może być organizatorem turnieju. Naruszenie przy `register` lub `confirm` daje **400** `{"team": ["Organizator turnieju nie może występować w zgłaszanej drużynie."]}`.

---

## Podsumowanie: gdzie egzekwowana jest każda reguła

| INV | Constraint DB | Serializator | Akcja w `atomic()` + blokada |
|---|---|---|---|
| 01 | – | – | `register`, `update` (open) |
| 02 | – | – | `register` |
| 03 | – | – | `register`, `confirm` |
| 04 | – | `TeamModelSerializer` (pkt 3) | `register`, `confirm`, `update` drużyny (pkt 2) |
| 05 | – | `GameSerializer.validate_team_size` | – |
| 06 | – | `can_cancel_until` (informacyjnie) | `cancel` |
| 07 | `unique_tournament_team_registration` | – | `register` |
| 08 | `tournament_active_must_be_published`, `tournament_deadline_before_start` | `TournamentModelSerializer.validate` | `open-registration`, `update`, `destroy` |
| 09 | `match_distinct_teams` | – | `start` |
| 10 | `on_delete=PROTECT` | – | `destroy` (mapowanie `ProtectedError`) |
| 11 | `match_winner_is_participant`, `match_status_consistency` | `MatchScoreSerializer` | `report-score` |
| 12 | – | – | `register`, `confirm` |
