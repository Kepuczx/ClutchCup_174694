from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.test import TestCase
from django.utils import timezone
from rest_framework import serializers

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


def make_user(username, role=UserProfile.Role.PLAYER, **profile):
    user = User.objects.create_user(username=username, password=STRONG_PASSWORD)
    UserProfile.objects.create(user=user, nickname=f'{username}_nick', role=role, **profile)
    return user


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
