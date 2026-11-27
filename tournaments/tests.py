from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.test import APITestCase

from .exceptions import ConflictError
from .models import (
    MAX_TEAM_MEMBERS,
    Game,
    Match,
    Tag,
    Team,
    Tournament,
    TournamentRegistration,
    UserProfile,
)
from .serializers import (
    ChangePasswordSerializer,
    CurrentUserProfileSerializer,
    GameSerializer,
    MatchScoreSerializer,
    MatchSerializer,
    TagSerializer,
    TeamListSerializer,
    TeamModelSerializer,
    TournamentListSerializer,
    TournamentModelSerializer,
    TournamentRegistrationSerializer,
    UserRegisterSerializer,
)

STRONG_PASSWORD = 'Trudne!Haslo123'
FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


def make_user(username, role=UserProfile.Role.PLAYER, **profile):
    user = User.objects.create_user(username=username, password=STRONG_PASSWORD)
    UserProfile.objects.create(user=user, nickname=f'{username}_nick', role=role, **profile)
    return user


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class SerializerTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.organizer = make_user('organizer', role=UserProfile.Role.ORGANIZER)
        cls.captain = make_user('captain')
        cls.players = [make_user(f'player{i}') for i in range(4)]
        cls.game = Game.objects.create(name='Counter-Strike 2', team_size=5)
        cls.tag_cs2 = Tag.objects.create(name='cs2')
        cls.tag_lan = Tag.objects.create(name='lan')
        cls.team = Team.objects.create(name='Clutch Kings', tag='CK', captain=cls.captain)
        cls.team.members.set([cls.captain, *cls.players])
        cls.tournament = Tournament.objects.create(
            title='ClutchCup Winter #1',
            description='Opis ' * 50,
            game=cls.game,
            organizer=cls.organizer,
            max_teams=8,
            entry_fee=Decimal('0.00'),
            registration_deadline=cls.now + timedelta(days=5),
            start_date=cls.now + timedelta(days=7),
        )
        cls.tournament.tags.set([cls.tag_cs2])

    def request_for(self, user):
        return SimpleNamespace(user=user)

    def open_tournament(self):
        Tournament.objects.filter(pk=self.tournament.pk).update(
            status=Tournament.Status.OPEN, is_published=True
        )
        self.tournament.refresh_from_db()


class UserRegisterSerializerTests(SerializerTestBase):
    def payload(self, **overrides):
        data = {
            'username': 'newbie',
            'email': 'newbie@example.com',
            'password': STRONG_PASSWORD,
            'password2': STRONG_PASSWORD,
            'nickname': 'NewbieNick',
            'role': 'player',
            'steam_id': '76561198000000001',
        }
        data.update(overrides)
        return data

    def test_creates_user_and_profile_without_exposing_password(self):
        serializer = UserRegisterSerializer(data=self.payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()
        self.assertTrue(user.check_password(STRONG_PASSWORD))
        self.assertEqual(user.profile.nickname, 'NewbieNick')
        self.assertNotIn('password', serializer.data)
        self.assertEqual(serializer.data['profile']['role'], 'player')

    def test_password_mismatch(self):
        serializer = UserRegisterSerializer(data=self.payload(password2='Inne!Haslo999'))
        self.assertFalse(serializer.is_valid())
        self.assertEqual(serializer.errors['password2'], ['Hasła nie są identyczne.'])

    def test_weak_password_rejected(self):
        serializer = UserRegisterSerializer(data=self.payload(password='123', password2='123'))
        self.assertFalse(serializer.is_valid())
        self.assertIn('password', serializer.errors)

    def test_duplicates_rejected(self):
        UserProfile.objects.filter(user=self.captain).update(steam_id='76561198000000001')
        serializer = UserRegisterSerializer(
            data=self.payload(username='CAPTAIN', nickname='captain_NICK')
        )
        self.assertFalse(serializer.is_valid())
        self.assertEqual(serializer.errors['username'], ['Ta nazwa użytkownika jest już zajęta.'])
        self.assertEqual(serializer.errors['nickname'], ['Ten pseudonim jest już zajęty.'])
        self.assertEqual(
            serializer.errors['steam_id'], ['Ten SteamID jest już przypisany do innego konta.']
        )

    def test_invalid_steam_id_and_role(self):
        serializer = UserRegisterSerializer(data=self.payload(steam_id='123', role='admin'))
        self.assertFalse(serializer.is_valid())
        self.assertIn('steam_id', serializer.errors)
        self.assertIn('role', serializer.errors)


class CurrentUserProfileSerializerTests(SerializerTestBase):
    def test_updates_email_and_ignores_read_only_fields(self):
        profile = self.captain.profile
        serializer = CurrentUserProfileSerializer(
            profile,
            data={'email': 'cap@example.com', 'role': 'organizer', 'is_verified': True},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        profile.refresh_from_db()
        self.captain.refresh_from_db()
        self.assertEqual(self.captain.email, 'cap@example.com')
        self.assertEqual(profile.role, 'player')
        self.assertFalse(profile.is_verified)
        self.assertEqual(serializer.data['captained_teams'], [self.team.pk])

    def test_own_nickname_is_not_a_duplicate(self):
        profile = self.captain.profile
        serializer = CurrentUserProfileSerializer(
            profile, data={'nickname': profile.nickname}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)


class ChangePasswordSerializerTests(SerializerTestBase):
    def test_wrong_old_password(self):
        serializer = ChangePasswordSerializer(
            self.captain,
            data={'old_password': 'zle', 'new_password': 'Nowe!Haslo456', 'new_password2': 'Nowe!Haslo456'},
        )
        self.assertFalse(serializer.is_valid())
        self.assertEqual(serializer.errors['old_password'], ['Aktualne hasło jest nieprawidłowe.'])

    def test_changes_password(self):
        serializer = ChangePasswordSerializer(
            self.captain,
            data={
                'old_password': STRONG_PASSWORD,
                'new_password': 'Nowe!Haslo456',
                'new_password2': 'Nowe!Haslo456',
            },
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.captain.refresh_from_db()
        self.assertTrue(self.captain.check_password('Nowe!Haslo456'))


class GameAndTagSerializerTests(SerializerTestBase):
    def test_game_duplicate_name_polish_message(self):
        serializer = GameSerializer(data={'name': 'Counter-Strike 2', 'team_size': 5})
        self.assertFalse(serializer.is_valid())
        self.assertEqual(serializer.errors['name'], ['Gra o tej nazwie już istnieje.'])

    def test_game_team_size_range(self):
        serializer = GameSerializer(data={'name': 'Chess', 'team_size': 11})
        self.assertFalse(serializer.is_valid())
        self.assertIn('team_size', serializer.errors)

    def test_game_team_size_locked_by_open_tournament(self):
        self.open_tournament()
        serializer = GameSerializer(self.game, data={'team_size': 4}, partial=True)
        with self.assertRaises(ConflictError):
            serializer.is_valid()

    def test_game_team_size_editable_without_active_tournaments(self):
        serializer = GameSerializer(self.game, data={'team_size': 4}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_tag_rejects_hash_and_uppercase(self):
        serializer = TagSerializer(data={'name': '#CS2'})
        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            serializer.errors['name'],
            ['Tag może zawierać tylko małe litery i cyfry, bez znaku #.'],
        )


class TeamSerializerTests(SerializerTestBase):
    def test_create_normalizes_tag_and_adds_captain(self):
        serializer = TeamModelSerializer(
            data={'name': 'Aim Academy', 'tag': 'aim', 'member_ids': [self.players[0].pk]},
            context={'request': self.request_for(self.captain)},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        team = serializer.save(captain=self.captain)
        self.assertEqual(team.tag, 'AIM')
        self.assertEqual(set(team.members.all()), {self.captain, self.players[0]})
        self.assertNotIn('member_ids', serializer.data)
        self.assertEqual(serializer.data['captain']['nickname'], 'captain_nick')

    def test_duplicate_name_case_insensitive_and_tag(self):
        serializer = TeamModelSerializer(
            data={'name': 'CLUTCH KINGS', 'tag': 'ck'},
            context={'request': self.request_for(self.captain)},
        )
        self.assertFalse(serializer.is_valid())
        self.assertEqual(serializer.errors['name'], ['Drużyna o tej nazwie już istnieje.'])
        self.assertEqual(serializer.errors['tag'], ['Drużyna o tym skrócie już istnieje.'])

    def test_invalid_tag_format(self):
        serializer = TeamModelSerializer(
            data={'name': 'X Team', 'tag': 'A-1'},
            context={'request': self.request_for(self.captain)},
        )
        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            serializer.errors['tag'], ['Skrót drużyny to 2–5 wielkich liter lub cyfr.']
        )

    def test_members_limit(self):
        extra = [make_user(f'extra{i}') for i in range(MAX_TEAM_MEMBERS)]
        serializer = TeamModelSerializer(
            self.team, data={'member_ids': [u.pk for u in extra]}, partial=True
        )
        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            serializer.errors['member_ids'],
            [f'Drużyna może liczyć maksymalnie {MAX_TEAM_MEMBERS} zawodników.'],
        )

    def test_update_keeps_own_name_and_captain_in_roster(self):
        serializer = TeamModelSerializer(
            self.team,
            data={'name': 'Clutch Kings', 'member_ids': [self.players[0].pk]},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.assertEqual(set(self.team.members.all()), {self.captain, self.players[0]})

    def test_roster_frozen_by_confirmed_registration(self):
        self.open_tournament()
        TournamentRegistration.objects.create(
            tournament=self.tournament, team=self.team,
            status=TournamentRegistration.Status.CONFIRMED,
        )
        serializer = TeamModelSerializer(
            self.team, data={'member_ids': [self.players[0].pk]}, partial=True
        )
        with self.assertRaises(ConflictError):
            serializer.is_valid()
        unchanged = TeamModelSerializer(
            self.team, data={'name': 'Clutch Kings Reborn'}, partial=True
        )
        self.assertTrue(unchanged.is_valid(), unchanged.errors)

    def test_list_serializer_uses_annotation(self):
        team = (
            Team.objects.select_related('captain__profile')
            .annotate(members_count=Count('members', distinct=True))
            .get(pk=self.team.pk)
        )
        with self.assertNumQueries(0):
            data = TeamListSerializer(team).data
        self.assertEqual(data['members_count'], 5)
        self.assertEqual(data['captain_nickname'], 'captain_nick')


class TournamentListSerializerTests(SerializerTestBase):
    def test_computed_fields(self):
        other_team = Team.objects.create(name='Aim Academy', tag='AIM', captain=self.players[0])
        cancelled_team = Team.objects.create(name='Gone', tag='GONE', captain=self.players[1])
        TournamentRegistration.objects.create(tournament=self.tournament, team=self.team)
        TournamentRegistration.objects.create(
            tournament=self.tournament, team=other_team,
            status=TournamentRegistration.Status.CONFIRMED,
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament, team=cancelled_team,
            status=TournamentRegistration.Status.CANCELLED,
        )
        tournament = (
            Tournament.objects.select_related('game')
            .prefetch_related('tags')
            .annotate(active_registrations_count=Count(
                'registrations',
                filter=Q(registrations__status__in=TournamentRegistration.ACTIVE_STATUSES),
            ))
            .get(pk=self.tournament.pk)
        )
        with self.assertNumQueries(0):
            data = TournamentListSerializer(tournament).data
        self.assertEqual(data['slots_taken'], 2)
        self.assertEqual(data['slots_left'], 6)
        self.assertTrue(data['is_free'])
        self.assertEqual(data['game_name'], 'Counter-Strike 2')
        self.assertEqual(data['tags'], ['cs2'])
        self.assertLessEqual(len(data['short_description']), 120)
        self.assertTrue(data['short_description'].endswith('…'))

    def test_paid_tournament_is_not_free(self):
        self.tournament.entry_fee = Decimal('25.00')
        self.assertFalse(TournamentListSerializer(self.tournament).data['is_free'])


class TournamentModelSerializerTests(SerializerTestBase):
    def payload(self, **overrides):
        data = {
            'title': 'ClutchCup Spring',
            'game_id': self.game.pk,
            'tag_ids': [self.tag_cs2.pk, self.tag_lan.pk],
            'max_teams': 16,
            'registration_deadline': (self.now + timedelta(days=10)).isoformat(),
            'start_date': (self.now + timedelta(days=12)).isoformat(),
        }
        data.update(overrides)
        return data

    def test_create_read_write_split(self):
        serializer = TournamentModelSerializer(
            data=self.payload(status='finished', organizer=self.captain.pk, slug='hack')
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        tournament = serializer.save(organizer=self.organizer)
        self.assertEqual(tournament.status, Tournament.Status.DRAFT)
        self.assertEqual(tournament.organizer, self.organizer)
        self.assertEqual(tournament.slug, 'clutchcup-spring')
        data = serializer.data
        self.assertEqual(data['organizer'], 'organizer')
        self.assertEqual(data['game']['team_size'], 5)
        self.assertEqual({tag['name'] for tag in data['tags']}, {'cs2', 'lan'})
        self.assertNotIn('game_id', data)
        self.assertNotIn('tag_ids', data)

    def test_required_fields_on_create(self):
        serializer = TournamentModelSerializer(data={})
        self.assertFalse(serializer.is_valid())
        for field in ('title', 'game_id', 'registration_deadline', 'start_date'):
            self.assertIn(field, serializer.errors)

    def test_deadline_must_precede_start(self):
        serializer = TournamentModelSerializer(data=self.payload(
            registration_deadline=(self.now + timedelta(days=12)).isoformat(),
            start_date=(self.now + timedelta(days=12)).isoformat(),
        ))
        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            serializer.errors['registration_deadline'],
            ['Termin zapisów musi upływać przed rozpoczęciem turnieju.'],
        )

    def test_deadline_in_past(self):
        serializer = TournamentModelSerializer(data=self.payload(
            registration_deadline=(self.now - timedelta(hours=1)).isoformat(),
        ))
        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            serializer.errors['registration_deadline'],
            ['Termin zapisów nie może być w przeszłości.'],
        )

    def test_patch_start_date_checked_against_existing_deadline(self):
        serializer = TournamentModelSerializer(
            self.tournament,
            data={'start_date': (self.now + timedelta(days=4)).isoformat()},
            partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('registration_deadline', serializer.errors)

    def test_patch_without_dates_skips_past_deadline_check(self):
        Tournament.objects.filter(pk=self.tournament.pk).update(
            registration_deadline=self.now - timedelta(days=1)
        )
        self.tournament.refresh_from_db()
        serializer = TournamentModelSerializer(
            self.tournament, data={'title': 'Nowy tytuł turnieju'}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_max_teams_range_and_active_registrations(self):
        serializer = TournamentModelSerializer(data=self.payload(max_teams=65))
        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            serializer.errors['max_teams'], ['Limit drużyn musi mieścić się w przedziale 2–64.']
        )
        other_team = Team.objects.create(name='Aim Academy', tag='AIM', captain=self.players[0])
        third_team = Team.objects.create(name='Third', tag='TRD', captain=self.players[1])
        for team in (self.team, other_team, third_team):
            TournamentRegistration.objects.create(tournament=self.tournament, team=team)
        serializer = TournamentModelSerializer(
            self.tournament, data={'max_teams': 2}, partial=True
        )
        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            serializer.errors['max_teams'],
            ['Limit nie może być mniejszy niż liczba aktywnych zgłoszeń (3).'],
        )

    def test_cannot_unpublish_open_tournament(self):
        self.open_tournament()
        serializer = TournamentModelSerializer(
            self.tournament, data={'is_published': False}, partial=True
        )
        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            serializer.errors['is_published'],
            ['Nie można ukryć turnieju w trakcie zapisów lub rozgrywek.'],
        )

    def test_unknown_tag_id(self):
        serializer = TournamentModelSerializer(data=self.payload(tag_ids=[9999]))
        self.assertFalse(serializer.is_valid())
        self.assertIn('tag_ids', serializer.errors)


class RegistrationAndMatchSerializerTests(SerializerTestBase):
    def test_can_cancel_until(self):
        self.open_tournament()
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament, team=self.team
        )
        start = self.tournament.start_date
        as_json = serializers.DateTimeField().to_representation
        data = TournamentRegistrationSerializer(registration).data
        self.assertEqual(data['can_cancel_until'], as_json(start))
        registration.status = TournamentRegistration.Status.CONFIRMED
        data = TournamentRegistrationSerializer(registration).data
        self.assertEqual(data['can_cancel_until'], as_json(start - timedelta(hours=24)))
        registration.tournament.start_date = timezone.now() + timedelta(hours=23)
        self.assertIsNone(TournamentRegistrationSerializer(registration).data['can_cancel_until'])
        registration.status = TournamentRegistration.Status.CANCELLED
        self.assertIsNone(TournamentRegistrationSerializer(registration).data['can_cancel_until'])

    def test_draft_tournament_has_no_cancel_window(self):
        registration = TournamentRegistration(
            tournament=self.tournament, team=self.team,
            status=TournamentRegistration.Status.PENDING,
        )
        self.assertIsNone(TournamentRegistrationSerializer(registration).data['can_cancel_until'])

    def test_match_score_validation(self):
        other_team = Team.objects.create(name='Aim Academy', tag='AIM', captain=self.players[0])
        match = Match.objects.create(
            tournament=self.tournament, team_a=self.team, team_b=other_team, round_number=1
        )
        draw = MatchScoreSerializer(match, data={'score_a': 10, 'score_b': 10})
        self.assertFalse(draw.is_valid())
        self.assertEqual(
            draw.errors['non_field_errors'],
            ['W formacie pucharowym mecz nie może zakończyć się remisem.'],
        )
        negative = MatchScoreSerializer(match, data={'score_a': -1, 'score_b': 3})
        self.assertFalse(negative.is_valid())
        self.assertIn('score_a', negative.errors)
        self.tournament.format = Tournament.Format.ROUND_ROBIN
        round_robin_draw = MatchScoreSerializer(match, data={'score_a': 10, 'score_b': 10})
        self.assertTrue(round_robin_draw.is_valid(), round_robin_draw.errors)
        data = MatchSerializer(match).data
        self.assertEqual(data['team_a']['tag'], 'CK')
        self.assertIsNone(data['winner'])


def url(name, *args):
    return reverse(f'tournaments:{name}', args=args)


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class ApiTestBase(APITestCase):
    def setUp(self):
        self.now = timezone.now()
        self.organizer = make_user('org', role=UserProfile.Role.ORGANIZER, is_verified=True)
        self.game = Game.objects.create(name='Counter-Strike 2', team_size=2)
        self.team = self.make_team('Clutch Kings', 'CK')
        self.captain = self.team.captain
        self.tournament = self.make_tournament()

    def make_team(self, name, tag, size=2):
        captain = make_user(f'{tag.lower()}_cap')
        team = Team.objects.create(name=name, tag=tag, captain=captain)
        team.members.set([captain, *(make_user(f'{tag.lower()}_p{i}') for i in range(size - 1))])
        return team

    def make_tournament(self, **overrides):
        data = {
            'title': 'ClutchCup Winter',
            'game': self.game,
            'organizer': self.organizer,
            'max_teams': 4,
            'status': Tournament.Status.OPEN,
            'is_published': True,
            'registration_deadline': self.now + timedelta(days=5),
            'start_date': self.now + timedelta(days=7),
        }
        data.update(overrides)
        return Tournament.objects.create(**data)

    def register(self, team, tournament=None):
        tournament = tournament or self.tournament
        self.client.force_authenticate(team.captain)
        return self.client.post(url('tournament-register', tournament.pk), {'team': team.pk})

    def registration(self, team, reg_status=TournamentRegistration.Status.CONFIRMED, tournament=None):
        return TournamentRegistration.objects.create(
            tournament=tournament or self.tournament, team=team, status=reg_status
        )

    def set_dates(self, tournament, deadline, start):
        Tournament.objects.filter(pk=tournament.pk).update(
            registration_deadline=deadline, start_date=start
        )
        tournament.refresh_from_db()

    def active_count(self, tournament=None):
        return (tournament or self.tournament).registrations.filter(
            status__in=TournamentRegistration.ACTIVE_STATUSES
        ).count()


class TournamentVisibilityApiTests(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.other_organizer = make_user('org2', role=UserProfile.Role.ORGANIZER)
        self.private_draft = self.make_tournament(
            title='Prywatny szkic', organizer=self.other_organizer,
            status=Tournament.Status.DRAFT, is_published=False,
        )

    def test_anonymous_sees_only_published(self):
        response = self.client.get(url('tournament-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([t['id'] for t in response.data['results']], [self.tournament.pk])

    def test_foreign_draft_is_404_and_own_draft_visible(self):
        self.client.force_authenticate(self.organizer)
        response = self.client.get(url('tournament-detail', self.private_draft.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.client.force_authenticate(self.other_organizer)
        response = self.client.get(url('tournament-detail', self.private_draft.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_query_count_is_constant(self):
        for i in range(3):
            self.make_tournament(title=f'Dodatkowy turniej {i}')
        with self.assertNumQueries(3):
            self.client.get(url('tournament-list'))

    def test_tags_filter_does_not_inflate_slot_counter(self):
        cs2, lan = Tag.objects.create(name='cs2'), Tag.objects.create(name='lan')
        self.tournament.tags.set([cs2, lan])
        self.registration(self.team)
        response = self.client.get(url('tournament-list'), {'tags': ['cs2', 'lan']})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['slots_taken'], 1)

    def test_has_free_slots_filter(self):
        full = self.make_tournament(title='Pełny turniej', max_teams=2)
        self.registration(self.team, tournament=full)
        self.registration(self.make_team('Aim Academy', 'AIM'), tournament=full)
        response = self.client.get(url('tournament-list'), {'has_free_slots': 'false'})
        self.assertEqual([t['id'] for t in response.data['results']], [full.pk])

    def test_upcoming_lists_published_future_draft_and_open(self):
        announced = self.make_tournament(title='Zapowiedź', status=Tournament.Status.DRAFT)
        self.make_tournament(title='W trakcie', status=Tournament.Status.ONGOING)
        response = self.client.get(url('tournament-upcoming'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {t['id'] for t in response.data['results']}, {self.tournament.pk, announced.pk}
        )


class RegisterApiTests(ApiTestBase):
    def test_success(self):
        response = self.register(self.team)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['status'], 'pending')
        self.assertEqual(response.data['slots_left'], 3)

    def test_anonymous_401(self):
        response = self.client.post(
            url('tournament-register', self.tournament.pk), {'team': self.team.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invisible_tournament_404(self):
        draft = self.make_tournament(
            title='Szkic prywatny', status=Tournament.Status.DRAFT, is_published=False
        )
        self.assertEqual(self.register(self.team, draft).status_code, status.HTTP_404_NOT_FOUND)

    def test_unknown_team_400_and_not_captain_403(self):
        self.client.force_authenticate(self.captain)
        response = self.client.post(url('tournament-register', self.tournament.pk), {'team': 999})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        member = self.team.members.exclude(pk=self.captain.pk).get()
        self.client.force_authenticate(member)
        response = self.client.post(
            url('tournament-register', self.tournament.pk), {'team': self.team.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.data['detail'], 'Tylko kapitan drużyny może zgłosić ją do turnieju.'
        )

    def test_not_open_and_deadline_409(self):
        announced = self.make_tournament(title='Zapowiedź', status=Tournament.Status.DRAFT)
        self.assertEqual(self.register(self.team, announced).status_code, status.HTTP_409_CONFLICT)
        self.set_dates(self.tournament, self.now - timedelta(minutes=1), self.now + timedelta(days=1))
        response = self.register(self.team)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['detail'], 'Termin zapisów do tego turnieju minął.')

    def test_duplicate_and_reregistration_after_cancel_409(self):
        self.assertEqual(self.register(self.team).status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.register(self.team).status_code, status.HTTP_409_CONFLICT)
        self.tournament.registrations.update(status=TournamentRegistration.Status.CANCELLED)
        response = self.register(self.team)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('anulowane', response.data['detail'])

    def test_roster_size_bounds(self):
        too_small = self.make_team('Solo', 'SOLO', size=1)
        too_big = self.make_team('Tłum', 'CROWD', size=5)
        just_right = self.make_team('Max', 'MAX', size=4)
        response = self.register(too_small)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('od 2 do 4 zawodników', response.data['team'][0])
        self.assertEqual(self.register(too_big).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.register(just_right).status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.active_count(), 1)

    def test_organizer_in_team_400(self):
        self.team.members.add(self.organizer)
        response = self.register(self.team)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data['team'],
            ['Organizator turnieju nie może występować w zgłaszanej drużynie.'],
        )

    def test_player_in_two_teams_409(self):
        other = self.make_team('Aim Academy', 'AIM')
        self.registration(other, TournamentRegistration.Status.PENDING)
        self.team.members.add(other.captain)
        response = self.register(self.team)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('aim_cap_nick', response.data['detail'])

    def test_capacity_never_exceeded(self):
        self.tournament.max_teams = 2
        self.tournament.save()
        teams = [self.team, self.make_team('Aim Academy', 'AIM'), self.make_team('Third', 'TRD')]
        codes = [self.register(team).status_code for team in teams]
        self.assertEqual(codes, [201, 201, 409])
        self.assertEqual(self.active_count(), 2)
        self.tournament.registrations.filter(team=teams[0]).update(
            status=TournamentRegistration.Status.CANCELLED
        )
        self.assertEqual(self.register(teams[2]).status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.active_count(), 2)

    def test_get_on_register_405(self):
        self.client.force_authenticate(self.captain)
        response = self.client.get(url('tournament-register', self.tournament.pk))
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class RegistrationLifecycleApiTests(ApiTestBase):
    def cancel(self, registration, user):
        self.client.force_authenticate(user)
        return self.client.post(url('registration-cancel', registration.pk), {'reason': 'test'})

    def test_confirm_and_reconfirm(self):
        registration = self.registration(self.team, TournamentRegistration.Status.PENDING)
        self.client.force_authenticate(self.organizer)
        response = self.client.post(url('registration-confirm', registration.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'confirmed')
        response = self.client.post(url('registration-confirm', registration.pk))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_confirm_rechecks_roster(self):
        registration = self.registration(self.team, TournamentRegistration.Status.PENDING)
        self.team.members.remove(*self.team.members.exclude(pk=self.captain.pk))
        self.client.force_authenticate(self.organizer)
        response = self.client.post(url('registration-confirm', registration.pk))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        registration.refresh_from_db()
        self.assertEqual(registration.status, TournamentRegistration.Status.PENDING)

    def test_captain_cancels_pending_one_day_before_start(self):
        self.set_dates(self.tournament, self.now + timedelta(hours=12), self.now + timedelta(hours=23))
        registration = self.registration(self.team, TournamentRegistration.Status.PENDING)
        self.assertEqual(self.cancel(registration, self.captain).status_code, status.HTTP_200_OK)

    def test_captain_cancels_confirmed_48h_before_start(self):
        self.set_dates(self.tournament, self.now + timedelta(hours=24), self.now + timedelta(hours=48))
        registration = self.registration(self.team)
        response = self.cancel(registration, self.captain)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'cancelled')

    def test_captain_cannot_cancel_confirmed_within_24h(self):
        self.set_dates(self.tournament, self.now + timedelta(hours=22), self.now + timedelta(hours=23))
        registration = self.registration(self.team)
        response = self.cancel(registration, self.captain)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('24 godziny', response.data['detail'])
        registration.refresh_from_db()
        self.assertEqual(registration.status, TournamentRegistration.Status.CONFIRMED)

    def test_organizer_cancels_confirmed_one_hour_before_start(self):
        self.set_dates(self.tournament, self.now + timedelta(minutes=30), self.now + timedelta(hours=1))
        registration = self.registration(self.team)
        self.assertEqual(self.cancel(registration, self.organizer).status_code, status.HTTP_200_OK)

    def test_double_cancel_and_after_start_409(self):
        registration = self.registration(self.team)
        self.assertEqual(self.cancel(registration, self.captain).status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.cancel(registration, self.captain).status_code, status.HTTP_409_CONFLICT
        )
        other = self.registration(self.make_team('Aim Academy', 'AIM'))
        Tournament.objects.filter(pk=self.tournament.pk).update(status=Tournament.Status.ONGOING)
        self.assertEqual(self.cancel(other, self.organizer).status_code, status.HTTP_409_CONFLICT)

    def test_stranger_403_or_404(self):
        stranger = make_user('stranger')
        confirmed = self.registration(self.team)
        pending = self.registration(
            self.make_team('Aim Academy', 'AIM'), TournamentRegistration.Status.PENDING
        )
        self.assertEqual(self.cancel(confirmed, stranger).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.cancel(pending, stranger).status_code, status.HTTP_404_NOT_FOUND)

    def test_plain_write_methods_are_405(self):
        registration = self.registration(self.team)
        self.client.force_authenticate(self.organizer)
        detail = url('registration-detail', registration.pk)
        not_allowed = status.HTTP_405_METHOD_NOT_ALLOWED
        self.assertEqual(self.client.delete(detail).status_code, not_allowed)
        self.assertEqual(self.client.patch(detail, {'status': 'cancelled'}).status_code, not_allowed)
        self.assertEqual(self.client.post(url('registration-list'), {}).status_code, not_allowed)
        self.assertTrue(TournamentRegistration.objects.filter(pk=registration.pk).exists())

    def test_list_requires_auth_and_hides_foreign_pending(self):
        response = self.client.get(url('registration-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.registration(self.team, TournamentRegistration.Status.PENDING)
        self.client.force_authenticate(make_user('stranger'))
        self.assertEqual(self.client.get(url('registration-list')).data['count'], 0)
        self.client.force_authenticate(self.captain)
        self.assertEqual(self.client.get(url('registration-list')).data['count'], 1)


class TournamentLifecycleApiTests(ApiTestBase):
    def start(self, tournament=None):
        self.client.force_authenticate(self.organizer)
        return self.client.post(url('tournament-start', (tournament or self.tournament).pk))

    def report(self, match, score_a, score_b):
        return self.client.post(
            url('match-report-score', match.pk), {'score_a': score_a, 'score_b': score_b}
        )

    def test_open_registration(self):
        draft = self.make_tournament(status=Tournament.Status.DRAFT, is_published=False)
        self.client.force_authenticate(self.organizer)
        response = self.client.post(url('tournament-open-registration', draft.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual((response.data['status'], response.data['is_published']), ('open', True))
        response = self.client.post(url('tournament-open-registration', draft.pk))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_unverified_organizer_cannot_open(self):
        UserProfile.objects.filter(user=self.organizer).update(is_verified=False)
        draft = self.make_tournament(status=Tournament.Status.DRAFT, is_published=False)
        self.client.force_authenticate(self.organizer)
        response = self.client.post(url('tournament-open-registration', draft.pk))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_create_forces_draft_and_owner(self):
        self.client.force_authenticate(self.organizer)
        response = self.client.post(url('tournament-list'), {
            'title': 'Nowy turniej', 'game_id': self.game.pk, 'status': 'open',
            'registration_deadline': (self.now + timedelta(days=1)).isoformat(),
            'start_date': (self.now + timedelta(days=2)).isoformat(),
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual((response.data['status'], response.data['organizer']), ('draft', 'org'))

    def test_open_tournament_edit_rules(self):
        self.client.force_authenticate(self.organizer)
        detail = url('tournament-detail', self.tournament.pk)
        other_game = Game.objects.create(name='Valorant', team_size=5)
        response = self.client.patch(detail, {'game_id': other_game.pk})
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        response = self.client.patch(detail, {'prize_pool': '-1'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        Tournament.objects.filter(pk=self.tournament.pk).update(prize_pool=Decimal('500'))
        response = self.client.patch(detail, {'prize_pool': '400'})
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        response = self.client.patch(detail, {'prize_pool': '600', 'title': 'Nowy tytuł'})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(self.client.delete(detail).status_code, status.HTTP_409_CONFLICT)

    def test_ongoing_tournament_is_read_only(self):
        Tournament.objects.filter(pk=self.tournament.pk).update(status=Tournament.Status.ONGOING)
        self.client.force_authenticate(self.organizer)
        response = self.client.patch(
            url('tournament-detail', self.tournament.pk), {'title': 'Zmiana tytułu'}
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_delete_draft_204(self):
        draft = self.make_tournament(status=Tournament.Status.DRAFT, is_published=False)
        self.client.force_authenticate(self.organizer)
        response = self.client.delete(url('tournament-detail', draft.pk))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_start_requires_closed_registration_or_full(self):
        self.registration(self.team)
        self.registration(self.make_team('Aim Academy', 'AIM'))
        self.assertEqual(self.start().status_code, status.HTTP_409_CONFLICT)
        self.tournament.max_teams = 2
        self.tournament.save()
        self.assertEqual(self.start().status_code, status.HTTP_200_OK)

    def test_start_requires_two_confirmed_teams(self):
        self.set_dates(self.tournament, self.now - timedelta(hours=1), self.now + timedelta(hours=1))
        self.registration(self.team)
        self.registration(
            self.make_team('Aim Academy', 'AIM'), TournamentRegistration.Status.PENDING
        )
        self.assertEqual(self.start().status_code, status.HTTP_409_CONFLICT)
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, Tournament.Status.OPEN)
        self.assertFalse(self.tournament.matches.exists())

    def test_single_elimination_full_run_with_bye(self):
        self.set_dates(self.tournament, self.now - timedelta(hours=1), self.now + timedelta(hours=1))
        aim, trd = self.make_team('Aim Academy', 'AIM'), self.make_team('Third', 'TRD')
        for team in (self.team, aim, trd):
            self.registration(team)
        self.registration(self.make_team('Late', 'LATE'), TournamentRegistration.Status.PENDING)

        response = self.start()
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        summary = (
            response.data['matches_created'], response.data['byes'],
            response.data['cancelled_pending'],
        )
        self.assertEqual(summary, (1, ['CK'], 1))
        semi = self.tournament.matches.get()
        self.assertEqual({semi.team_a, semi.team_b}, {aim, trd})

        self.assertEqual(self.report(semi, 10, 10).status_code, status.HTTP_400_BAD_REQUEST)
        response = self.report(semi, 13, 7)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['next_round_created'])
        self.assertEqual(self.report(semi, 1, 0).status_code, status.HTTP_409_CONFLICT)

        final = self.tournament.matches.get(round_number=2)
        self.assertEqual((final.team_a, final.team_b), (self.team, semi.team_a))
        response = self.report(final, 16, 14)
        self.assertTrue(response.data['tournament_finished'])
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, Tournament.Status.FINISHED)
        self.assertEqual(self.report(final, 1, 0).status_code, status.HTTP_409_CONFLICT)

    def test_round_robin_schedule_draws_and_standings(self):
        Tournament.objects.filter(pk=self.tournament.pk).update(
            format=Tournament.Format.ROUND_ROBIN
        )
        self.set_dates(self.tournament, self.now - timedelta(hours=1), self.now + timedelta(hours=1))
        teams = [self.team] + [self.make_team(f'Team {tag}', tag) for tag in ('BB', 'CC', 'DD')]
        for team in teams:
            self.registration(team)
        self.assertEqual(self.start().data['matches_created'], 6)
        rounds = self.tournament.matches.values('round_number').annotate(n=Count('id'))
        self.assertEqual({r['round_number']: r['n'] for r in rounds}, {1: 2, 2: 2, 3: 2})
        pairs = {frozenset((m.team_a_id, m.team_b_id)) for m in self.tournament.matches.all()}
        self.assertEqual(len(pairs), 6)

        matches = list(self.tournament.matches.order_by('id'))
        for match in matches[:-1]:
            self.assertFalse(self.report(match, 5, 3).data['tournament_finished'])
        response = self.report(matches[-1], 2, 2)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['tournament_finished'])
        self.assertIsNone(response.data['winner'])

        standings = self.client.get(url('tournament-standings', self.tournament.pk)).data
        self.assertEqual(sum(row['played'] for row in standings), 12)
        self.assertEqual(sum(row['draws'] for row in standings), 2)
        points = [row['points'] for row in standings]
        self.assertEqual(points, sorted(points, reverse=True))

    def test_report_score_outside_ongoing_409(self):
        match = Match.objects.create(
            tournament=self.tournament, team_a=self.team,
            team_b=self.make_team('Aim Academy', 'AIM'), round_number=1,
        )
        self.client.force_authenticate(self.organizer)
        self.assertEqual(self.report(match, 1, 0).status_code, status.HTTP_409_CONFLICT)

    def test_matches_are_read_only(self):
        self.client.force_authenticate(self.organizer)
        response = self.client.post(url('match-list'), {})
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class DictionaryAndTeamApiTests(ApiTestBase):
    def test_game_write_requires_staff(self):
        payload = {'name': 'Valorant', 'team_size': 5}
        response = self.client.post(url('game-list'), payload)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.client.force_authenticate(self.captain)
        response = self.client.post(url('game-list'), payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        admin = User.objects.create_superuser('root', 'root@example.com', STRONG_PASSWORD)
        self.client.force_authenticate(admin)
        response = self.client.post(url('game-list'), payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        response = self.client.delete(url('game-detail', self.game.pk))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        response = self.client.patch(url('game-detail', self.game.pk), {'team_size': 3})
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_team_create_sets_captain_and_delete_protected(self):
        player = make_user('creator')
        self.client.force_authenticate(player)
        response = self.client.post(url('team-list'), {'name': 'Nowi', 'tag': 'new'})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['captain']['id'], player.pk)
        self.assertEqual([m['id'] for m in response.data['members']], [player.pk])
        self.registration(self.team, TournamentRegistration.Status.CANCELLED)
        self.client.force_authenticate(self.captain)
        response = self.client.delete(url('team-detail', self.team.pk))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)


class PlatformSummaryApiTests(ApiTestBase):
    def test_summary_aggregates(self):
        Tournament.objects.filter(pk=self.tournament.pk).update(prize_pool=Decimal('1000'))
        self.make_tournament(
            title='Drugi', prize_pool=Decimal('500'), entry_fee=Decimal('20'), max_teams=8
        )
        self.make_tournament(
            title='Ukryty', status=Tournament.Status.DRAFT, is_published=False,
            prize_pool=Decimal('9999'),
        )
        self.registration(self.team)
        response = self.client.get(url('summary'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertEqual(data['tournaments_total'], 2)
        self.assertEqual(
            data['tournaments_by_status'], {'draft': 0, 'open': 2, 'ongoing': 0, 'finished': 0}
        )
        self.assertEqual(
            data['prize_pool'],
            {'total': '1500.00', 'avg': '750.00', 'min': '500.00', 'max': '1000.00'},
        )
        self.assertEqual(data['entry_fee']['max'], '20.00')
        self.assertEqual(data['avg_max_teams'], 6.0)
        self.assertEqual(data['registrations'], {'active': 1, 'cancelled': 0})
        self.assertEqual(data['top_games'], [{'game': 'Counter-Strike 2', 'tournaments': 2}])

    def test_summary_on_empty_database(self):
        Tournament.objects.all().delete()
        data = self.client.get(url('summary')).data
        self.assertEqual(data['tournaments_total'], 0)
        self.assertIsNone(data['prize_pool']['avg'])
        self.assertIsNone(data['avg_max_teams'])
        self.assertEqual(data['top_games'], [])
