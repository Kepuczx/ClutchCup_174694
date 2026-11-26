from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import Truncator
from rest_framework import serializers

from .exceptions import ConflictError
from .models import (
    CANCELLATION_CUTOFF,
    MAX_TEAM_MEMBERS,
    STEAM_ID_VALIDATOR,
    TEAM_TAG_VALIDATOR,
    Game,
    Match,
    Tag,
    Team,
    Tournament,
    TournamentRegistration,
    UserProfile,
)

User = get_user_model()

SHORT_DESCRIPTION_LENGTH = 120
LOCKED_TOURNAMENT_STATUSES = (Tournament.Status.OPEN, Tournament.Status.ONGOING)


def active_registrations_count(tournament):
    annotated = getattr(tournament, 'active_registrations_count', None)
    if annotated is not None:
        return annotated
    return tournament.registrations.filter(
        status__in=TournamentRegistration.ACTIVE_STATUSES
    ).count()


class UserBriefSerializer(serializers.ModelSerializer):
    nickname = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'username', 'nickname')

    def get_nickname(self, obj):
        profile = getattr(obj, 'profile', None)
        return profile.nickname if profile else None


class ProfileFieldsValidationMixin:
    def _profile_pk(self):
        return None

    def validate_nickname(self, value):
        value = value.strip()
        if len(value) < 3:
            raise serializers.ValidationError('Pseudonim musi mieć co najmniej 3 znaki.')
        if (
            UserProfile.objects.filter(nickname__iexact=value)
            .exclude(pk=self._profile_pk())
            .exists()
        ):
            raise serializers.ValidationError('Ten pseudonim jest już zajęty.')
        return value

    def validate_steam_id(self, value):
        if not value:
            return ''
        try:
            STEAM_ID_VALIDATOR(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        if (
            UserProfile.objects.filter(steam_id=value)
            .exclude(pk=self._profile_pk())
            .exists()
        ):
            raise serializers.ValidationError(
                'Ten SteamID jest już przypisany do innego konta.'
            )
        return value


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ('nickname', 'role', 'steam_id', 'is_verified')
        read_only_fields = fields


class UserRegisterSerializer(ProfileFieldsValidationMixin, serializers.ModelSerializer):
    username = serializers.CharField(
        max_length=150, validators=[UnicodeUsernameValidator()]
    )
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    password2 = serializers.CharField(write_only=True, style={'input_type': 'password'})
    nickname = serializers.CharField(write_only=True, max_length=32)
    role = serializers.ChoiceField(
        choices=UserProfile.Role.choices, write_only=True, default=UserProfile.Role.PLAYER
    )
    steam_id = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=17
    )
    profile = UserProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = (
            'id', 'username', 'email', 'password', 'password2',
            'nickname', 'role', 'steam_id', 'profile',
        )

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError('Ta nazwa użytkownika jest już zajęta.')
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({'password2': 'Hasła nie są identyczne.'})
        candidate = User(username=attrs['username'], email=attrs.get('email', ''))
        try:
            validate_password(attrs['password'], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': list(exc.messages)})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        validated_data.pop('password2')
        profile_data = {
            'nickname': validated_data.pop('nickname'),
            'role': validated_data.pop('role'),
            'steam_id': validated_data.pop('steam_id', ''),
        }
        user = User.objects.create_user(**validated_data)
        UserProfile.objects.create(user=user, **profile_data)
        return user


class CurrentUserProfileSerializer(ProfileFieldsValidationMixin, serializers.ModelSerializer):
    id = serializers.ReadOnlyField(source='user.id')
    username = serializers.ReadOnlyField(source='user.username')
    email = serializers.EmailField(source='user.email', required=False, allow_blank=True)
    nickname = serializers.CharField(max_length=32, required=False)
    steam_id = serializers.CharField(max_length=17, required=False, allow_blank=True)
    captained_teams = serializers.PrimaryKeyRelatedField(
        source='user.captained_teams', many=True, read_only=True
    )
    teams = serializers.PrimaryKeyRelatedField(source='user.teams', many=True, read_only=True)

    class Meta:
        model = UserProfile
        fields = (
            'id', 'username', 'email', 'nickname', 'role', 'steam_id',
            'is_verified', 'captained_teams', 'teams',
        )
        read_only_fields = ('role', 'is_verified')

    def _profile_pk(self):
        return getattr(self.instance, 'pk', None)

    @transaction.atomic
    def update(self, instance, validated_data):
        user_data = validated_data.pop('user', {})
        if 'email' in user_data:
            instance.user.email = user_data['email']
            instance.user.save(update_fields=['email'])
        return super().update(instance, validated_data)


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    new_password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    new_password2 = serializers.CharField(write_only=True, style={'input_type': 'password'})

    def validate_old_password(self, value):
        if not self.instance.check_password(value):
            raise serializers.ValidationError('Aktualne hasło jest nieprawidłowe.')
        return value

    def validate(self, attrs):
        if attrs['new_password'] != attrs['new_password2']:
            raise serializers.ValidationError({'new_password2': 'Hasła nie są identyczne.'})
        if attrs['new_password'] == attrs['old_password']:
            raise serializers.ValidationError(
                {'new_password': 'Nowe hasło musi różnić się od aktualnego.'}
            )
        try:
            validate_password(attrs['new_password'], user=self.instance)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'new_password': list(exc.messages)})
        return attrs

    def update(self, instance, validated_data):
        instance.set_password(validated_data['new_password'])
        instance.save(update_fields=['password'])
        return instance


class GameBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Game
        fields = ('id', 'name', 'slug', 'team_size')


class GameSerializer(serializers.ModelSerializer):
    tournaments_count = serializers.SerializerMethodField()

    class Meta:
        model = Game
        fields = ('id', 'name', 'slug', 'team_size', 'description', 'tournaments_count')
        read_only_fields = ('slug',)

    def get_tournaments_count(self, obj):
        annotated = getattr(obj, 'tournaments_count', None)
        return annotated if annotated is not None else obj.tournaments.count()

    def validate_team_size(self, value):
        if (
            self.instance is not None
            and value != self.instance.team_size
            and self.instance.tournaments.filter(status__in=LOCKED_TOURNAMENT_STATUSES).exists()
        ):
            raise ConflictError(
                'Nie można zmienić rozmiaru składu gry, która ma turnieje '
                'z otwartymi zapisami lub w trakcie.'
            )
        return value


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ('id', 'name', 'slug')
        read_only_fields = ('slug',)


class TeamBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ('id', 'name', 'tag')


class TeamListSerializer(serializers.ModelSerializer):
    captain_nickname = serializers.SerializerMethodField()
    members_count = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = ('id', 'name', 'slug', 'tag', 'captain_nickname', 'members_count')

    def get_captain_nickname(self, obj):
        profile = getattr(obj.captain, 'profile', None)
        return profile.nickname if profile else obj.captain.username

    def get_members_count(self, obj):
        annotated = getattr(obj, 'members_count', None)
        return annotated if annotated is not None else obj.members.count()


class TeamModelSerializer(serializers.ModelSerializer):
    name = serializers.CharField(max_length=64)
    tag = serializers.CharField(max_length=5)
    captain = UserBriefSerializer(read_only=True)
    members = UserBriefSerializer(many=True, read_only=True)
    member_ids = serializers.PrimaryKeyRelatedField(
        source='members',
        many=True,
        write_only=True,
        required=False,
        queryset=User.objects.all(),
    )

    class Meta:
        model = Team
        fields = ('id', 'name', 'slug', 'tag', 'captain', 'members', 'member_ids', 'created_at')
        read_only_fields = ('slug', 'created_at')

    def _captain(self):
        if self.instance is not None:
            return self.instance.captain
        request = self.context.get('request')
        return getattr(request, 'user', None)

    def validate_name(self, value):
        if (
            Team.objects.filter(name__iexact=value)
            .exclude(pk=getattr(self.instance, 'pk', None))
            .exists()
        ):
            raise serializers.ValidationError('Drużyna o tej nazwie już istnieje.')
        return value

    def validate_tag(self, value):
        value = value.upper()
        try:
            TEAM_TAG_VALIDATOR(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        if Team.objects.filter(tag=value).exclude(pk=getattr(self.instance, 'pk', None)).exists():
            raise serializers.ValidationError('Drużyna o tym skrócie już istnieje.')
        return value

    def validate_member_ids(self, value):
        captain = self._captain()
        member_pks = {user.pk for user in value}
        if captain is not None:
            member_pks.add(captain.pk)
        if len(member_pks) > MAX_TEAM_MEMBERS:
            raise serializers.ValidationError(
                f'Drużyna może liczyć maksymalnie {MAX_TEAM_MEMBERS} zawodników.'
            )
        if self.instance is not None:
            current_pks = set(self.instance.members.values_list('pk', flat=True))
            roster_frozen = self.instance.registrations.filter(
                status=TournamentRegistration.Status.CONFIRMED,
                tournament__status__in=LOCKED_TOURNAMENT_STATUSES,
            ).exists()
            if member_pks != current_pks and roster_frozen:
                raise ConflictError(
                    'Skład drużyny jest zamrożony, ponieważ drużyna ma potwierdzone '
                    'zgłoszenie do trwającego turnieju.'
                )
        return value

    @transaction.atomic
    def create(self, validated_data):
        members = validated_data.pop('members', [])
        team = Team.objects.create(**validated_data)
        team.members.set({*members, team.captain})
        return team

    @transaction.atomic
    def update(self, instance, validated_data):
        members = validated_data.pop('members', None)
        instance = super().update(instance, validated_data)
        if members is not None:
            instance.members.set({*members, instance.captain})
        return instance


class TournamentListSerializer(serializers.ModelSerializer):
    game_name = serializers.ReadOnlyField(source='game.name')
    tags = serializers.SlugRelatedField(many=True, read_only=True, slug_field='name')
    short_description = serializers.SerializerMethodField()
    slots_taken = serializers.SerializerMethodField()
    slots_left = serializers.SerializerMethodField()
    is_free = serializers.SerializerMethodField()

    class Meta:
        model = Tournament
        fields = (
            'id', 'title', 'slug', 'short_description', 'game_name', 'format', 'status',
            'start_date', 'registration_deadline', 'prize_pool', 'entry_fee', 'is_free',
            'max_teams', 'slots_taken', 'slots_left', 'tags',
        )

    def get_short_description(self, obj):
        return Truncator(obj.description).chars(SHORT_DESCRIPTION_LENGTH)

    def get_slots_taken(self, obj):
        return active_registrations_count(obj)

    def get_slots_left(self, obj):
        return max(obj.max_teams - active_registrations_count(obj), 0)

    def get_is_free(self, obj):
        return obj.entry_fee == 0


class TournamentModelSerializer(serializers.ModelSerializer):
    game = GameBriefSerializer(read_only=True)
    game_id = serializers.PrimaryKeyRelatedField(
        source='game', write_only=True, queryset=Game.objects.all()
    )
    organizer = serializers.ReadOnlyField(source='organizer.username')
    tags = TagSerializer(many=True, read_only=True)
    tag_ids = serializers.PrimaryKeyRelatedField(
        source='tags', many=True, write_only=True, required=False, queryset=Tag.objects.all()
    )
    max_teams = serializers.IntegerField(required=False)
    slots_taken = serializers.SerializerMethodField()

    class Meta:
        model = Tournament
        fields = (
            'id', 'title', 'slug', 'description', 'game', 'game_id', 'organizer',
            'tags', 'tag_ids', 'format', 'max_teams', 'prize_pool', 'entry_fee',
            'registration_deadline', 'start_date', 'status', 'is_published',
            'slots_taken', 'created_at',
        )
        read_only_fields = ('slug', 'status', 'created_at')

    def get_slots_taken(self, obj):
        return active_registrations_count(obj)

    def validate_max_teams(self, value):
        if not 2 <= value <= 64:
            raise serializers.ValidationError('Limit drużyn musi mieścić się w przedziale 2–64.')
        if self.instance is not None:
            active = active_registrations_count(self.instance)
            if value < active:
                raise serializers.ValidationError(
                    f'Limit nie może być mniejszy niż liczba aktywnych zgłoszeń ({active}).'
                )
        return value

    def validate(self, attrs):
        deadline = attrs.get(
            'registration_deadline', getattr(self.instance, 'registration_deadline', None)
        )
        start_date = attrs.get('start_date', getattr(self.instance, 'start_date', None))
        status = getattr(self.instance, 'status', Tournament.Status.DRAFT)
        is_published = attrs.get('is_published', getattr(self.instance, 'is_published', False))

        if deadline and start_date and deadline >= start_date:
            raise serializers.ValidationError({
                'registration_deadline': 'Termin zapisów musi upływać przed rozpoczęciem turnieju.'
            })

        deadline_changed = 'registration_deadline' in attrs and (
            self.instance is None or attrs['registration_deadline'] != self.instance.registration_deadline
        )
        if deadline_changed and deadline <= timezone.now():
            raise serializers.ValidationError({
                'registration_deadline': 'Termin zapisów nie może być w przeszłości.'
            })

        if not is_published and status in LOCKED_TOURNAMENT_STATUSES:
            raise serializers.ValidationError({
                'is_published': 'Nie można ukryć turnieju w trakcie zapisów lub rozgrywek.'
            })
        return attrs


class TournamentBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tournament
        fields = ('id', 'title', 'status', 'start_date')


class TournamentRegistrationSerializer(serializers.ModelSerializer):
    tournament = TournamentBriefSerializer(read_only=True)
    team = TeamBriefSerializer(read_only=True)
    can_cancel_until = serializers.SerializerMethodField()

    class Meta:
        model = TournamentRegistration
        fields = (
            'id', 'tournament', 'team', 'status', 'registered_at', 'updated_at',
            'can_cancel_until',
        )
        read_only_fields = fields

    def get_can_cancel_until(self, obj):
        if obj.tournament.status != Tournament.Status.OPEN:
            return None
        if obj.status == TournamentRegistration.Status.PENDING:
            deadline = obj.tournament.start_date
        elif obj.status == TournamentRegistration.Status.CONFIRMED:
            deadline = obj.tournament.start_date - CANCELLATION_CUTOFF
        else:
            return None
        if deadline <= timezone.now():
            return None
        return serializers.DateTimeField().to_representation(deadline)


class TournamentRegistrationCreateSerializer(serializers.Serializer):
    team = serializers.PrimaryKeyRelatedField(
        queryset=Team.objects.select_related('captain')
    )


class RegistrationCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True)


class MatchSerializer(serializers.ModelSerializer):
    team_a = TeamBriefSerializer(read_only=True)
    team_b = TeamBriefSerializer(read_only=True)

    class Meta:
        model = Match
        fields = (
            'id', 'tournament', 'round_number', 'team_a', 'team_b',
            'score_a', 'score_b', 'winner', 'status', 'completed_at',
        )
        read_only_fields = fields


class MatchScoreSerializer(serializers.Serializer):
    score_a = serializers.IntegerField(min_value=0, max_value=999)
    score_b = serializers.IntegerField(min_value=0, max_value=999)

    def validate(self, attrs):
        is_elimination = (
            self.instance is not None
            and self.instance.tournament.format == Tournament.Format.SINGLE_ELIMINATION
        )
        if is_elimination and attrs['score_a'] == attrs['score_b']:
            raise serializers.ValidationError(
                'W formacie pucharowym mecz nie może zakończyć się remisem.'
            )
        return attrs
