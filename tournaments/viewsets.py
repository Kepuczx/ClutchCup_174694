import logging

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q, ProtectedError, Sum
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from .exceptions import ConflictError
from .filters import GameFilter, MatchFilter, RegistrationFilter, TeamFilter, TournamentFilter
from .models import (
    CANCELLATION_CUTOFF,
    MAX_SUBSTITUTES,
    MIN_TEAMS_TO_START,
    Game,
    Match,
    Tag,
    Team,
    Tournament,
    TournamentRegistration,
)
from .serializers import (
    GameSerializer,
    MatchScoreSerializer,
    MatchSerializer,
    RegistrationCancelSerializer,
    TagSerializer,
    TeamListSerializer,
    TeamModelSerializer,
    TournamentListSerializer,
    TournamentModelSerializer,
    TournamentRegistrationCreateSerializer,
    TournamentRegistrationSerializer,
)

logger = logging.getLogger(__name__)
User = get_user_model()

Status = Tournament.Status
RegStatus = TournamentRegistration.Status
ACTIVE = TournamentRegistration.ACTIVE_STATUSES

MSG_DUPLICATE_REGISTRATION = 'Ta drużyna jest już zgłoszona do tego turnieju.'
OPEN_STATE_LOCKED_FIELDS = {'game': 'game_id', 'format': 'format', 'entry_fee': 'entry_fee'}


def visible_tournaments_q(user, prefix=''):
    published = Q(**{f'{prefix}is_published': True})
    if user.is_authenticated:
        return published | Q(**{f'{prefix}organizer': user})
    return published


def lock_tournament(pk):
    return Tournament.objects.select_for_update().select_related('game', 'organizer').get(pk=pk)


def wrong_status(tournament):
    return ConflictError(
        f'Operacja niedozwolona dla turnieju w statusie „{tournament.get_status_display()}”.'
    )


def ensure_team_eligible(tournament, team):
    team_size = tournament.game.team_size
    members_count = team.members.count()
    if not team_size <= members_count <= team_size + MAX_SUBSTITUTES:
        raise ValidationError({'team': [
            f'Drużyna musi liczyć od {team_size} do {team_size + MAX_SUBSTITUTES} zawodników '
            f'(gra {tournament.game.name} wymaga składu {team_size}-osobowego), '
            f'a liczy {members_count}.'
        ]})
    if team.members.filter(pk=tournament.organizer_id).exists():
        raise ValidationError({'team': [
            'Organizator turnieju nie może występować w zgłaszanej drużynie.'
        ]})
    other_registrations = TournamentRegistration.objects.filter(
        tournament=tournament, status__in=ACTIVE
    ).exclude(team=team)
    clashing = (
        User.objects.filter(
            pk__in=team.members.values('pk'),
            teams__registrations__in=other_registrations,
        )
        .select_related('profile')
        .distinct()
        .order_by('username')
    )
    if clashing:
        names = ', '.join(
            getattr(getattr(user, 'profile', None), 'nickname', user.username)
            for user in clashing
        )
        raise ConflictError(
            f'Zawodnicy {names} są już zgłoszeni do tego turnieju w innej drużynie.'
        )


def single_elimination_pairs(teams):
    return [(teams[i], teams[i + 1]) for i in range(0, len(teams) - 1, 2)]


def single_elimination_first_round(seeds):
    bracket_size = 1
    while bracket_size < len(seeds):
        bracket_size *= 2
    byes = bracket_size - len(seeds)
    playing = seeds[byes:]
    pairs = [(playing[i], playing[-1 - i]) for i in range(len(playing) // 2)]
    return pairs, seeds[:byes]


def round_robin_schedule(seeds):
    slots = list(seeds)
    if len(slots) % 2:
        slots.append(None)
    size = len(slots)
    rounds = []
    for _ in range(size - 1):
        rounds.append([
            (slots[i], slots[size - 1 - i])
            for i in range(size // 2)
            if slots[i] is not None and slots[size - 1 - i] is not None
        ])
        slots = [slots[0], slots[-1], *slots[1:-1]]
    return rounds


def confirmed_seeds(tournament):
    return [
        registration.team
        for registration in tournament.registrations.filter(status=RegStatus.CONFIRMED)
        .select_related('team')
        .order_by('registered_at', 'id')
    ]


class GameViewSet(viewsets.ModelViewSet):
    queryset = Game.objects.annotate(tournaments_count=Count('tournaments', distinct=True))
    serializer_class = GameSerializer
    filterset_class = GameFilter
    search_fields = ('name',)
    ordering_fields = ('name', 'team_size', 'tournaments_count')

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [AllowAny()]
        return [IsAdminUser()]

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ConflictError('Nie można usunąć gry, do której przypisano turnieje.')


class TagViewSet(viewsets.ModelViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    search_fields = ('name',)
    ordering_fields = ('name',)

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [AllowAny()]
        return [IsAdminUser()]


class TeamViewSet(viewsets.ModelViewSet):
    queryset = (
        Team.objects.select_related('captain__profile')
        .prefetch_related('members__profile')
        .annotate(members_count=Count('members', distinct=True))
    )
    filterset_class = TeamFilter
    search_fields = ('name', 'tag')
    ordering_fields = ('name', 'created_at', 'members_count')

    def get_serializer_class(self):
        if self.action == 'list':
            return TeamListSerializer
        return TeamModelSerializer

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [AllowAny()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(captain=self.request.user)

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ConflictError('Nie można usunąć drużyny, która uczestniczyła w turniejach.')


class TournamentViewSet(viewsets.ModelViewSet):
    filterset_class = TournamentFilter
    search_fields = ('title', 'description', 'game__name')
    ordering_fields = ('start_date', 'registration_deadline', 'prize_pool', 'entry_fee', 'created_at')
    ordering = ('start_date', 'id')

    def get_queryset(self):
        return (
            Tournament.objects.select_related('game', 'organizer')
            .prefetch_related('tags')
            .annotate(active_registrations_count=Count(
                'registrations',
                filter=Q(registrations__status__in=ACTIVE),
                distinct=True,
            ))
            .filter(visible_tournaments_q(self.request.user))
        )

    def get_serializer_class(self):
        if self.action in ('list', 'upcoming'):
            return TournamentListSerializer
        if self.action == 'register':
            return TournamentRegistrationCreateSerializer
        if self.action == 'registrations':
            return TournamentRegistrationSerializer
        return TournamentModelSerializer

    def get_permissions(self):
        if self.action in ('list', 'retrieve', 'upcoming', 'registrations', 'standings'):
            return [AllowAny()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(organizer=self.request.user, status=Status.DRAFT)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        with transaction.atomic():
            tournament = lock_tournament(instance.pk)
            serializer = self.get_serializer(tournament, data=request.data, partial=partial)
            self._ensure_status_allows_edit(tournament, serializer)
            serializer.is_valid(raise_exception=True)
            if tournament.status == Status.OPEN:
                self._ensure_open_state_edit(tournament, serializer.validated_data)
            serializer.save()
        return Response(serializer.data)

    def _ensure_status_allows_edit(self, tournament, serializer):
        if tournament.status in (Status.DRAFT, Status.OPEN):
            return
        writable = {name for name, field in serializer.fields.items() if not field.read_only}
        changed = writable & set(self.request.data)
        if tournament.status == Status.FINISHED and changed <= {'is_published'}:
            return
        raise ConflictError(
            f'Turnieju w statusie „{tournament.get_status_display()}” nie można edytować.'
        )

    def _ensure_open_state_edit(self, tournament, data):
        locked = [
            api_name for field, api_name in OPEN_STATE_LOCKED_FIELDS.items()
            if field in data and data[field] != getattr(tournament, field)
        ]
        if locked:
            raise ConflictError(
                f'Pola {", ".join(locked)} nie mogą być zmieniane po otwarciu zapisów.'
            )
        if 'prize_pool' in data and data['prize_pool'] < tournament.prize_pool:
            raise ConflictError('Pulę nagród można jedynie zwiększyć po otwarciu zapisów.')
        for field in ('registration_deadline', 'start_date'):
            if field in data and data[field] < getattr(tournament, field):
                raise ConflictError('Po otwarciu zapisów terminy można jedynie przesuwać na później.')

    def perform_destroy(self, instance):
        if instance.status != Status.DRAFT:
            raise ConflictError('Można usunąć wyłącznie turniej w statusie szkicu.')
        instance.delete()

    @extend_schema(responses=TournamentListSerializer(many=True))
    @action(detail=False, methods=['get'])
    def upcoming(self, request):
        queryset = self.filter_queryset(self.get_queryset()).filter(
            is_published=True,
            status__in=(Status.DRAFT, Status.OPEN),
            start_date__gt=timezone.now(),
        ).order_by('start_date', 'id')
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(request=None, responses=TournamentModelSerializer)
    @action(detail=True, methods=['post'], url_path='open-registration')
    def open_registration(self, request, pk=None):
        instance = self.get_object()
        with transaction.atomic():
            tournament = lock_tournament(instance.pk)
            if tournament.status != Status.DRAFT:
                raise wrong_status(tournament)
            if tournament.registration_deadline <= timezone.now():
                raise ConflictError('Nie można otworzyć zapisów – termin zapisów już minął.')
            profile = getattr(tournament.organizer, 'profile', None)
            if not (profile and profile.is_verified):
                raise ConflictError('Tylko zweryfikowany organizator może otworzyć zapisy.')
            tournament.status = Status.OPEN
            tournament.is_published = True
            tournament.save(update_fields=['status', 'is_published'])
        return Response(TournamentModelSerializer(tournament).data)

    @extend_schema(
        request=TournamentRegistrationCreateSerializer,
        responses={201: TournamentRegistrationSerializer},
    )
    @action(detail=True, methods=['post'])
    def register(self, request, pk=None):
        instance = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        team = serializer.validated_data['team']
        if team.captain_id != request.user.pk:
            raise PermissionDenied('Tylko kapitan drużyny może zgłosić ją do turnieju.')
        try:
            with transaction.atomic():
                registration, slots_left = self._register_team(instance.pk, team)
        except IntegrityError:
            raise ConflictError(MSG_DUPLICATE_REGISTRATION)
        data = TournamentRegistrationSerializer(registration).data
        data['slots_left'] = slots_left
        return Response(data, status=status.HTTP_201_CREATED)

    def _register_team(self, tournament_pk, team):
        tournament = lock_tournament(tournament_pk)
        now = timezone.now()
        if tournament.status != Status.OPEN:
            raise ConflictError('Zapisy do tego turnieju nie są otwarte.')
        if now >= tournament.registration_deadline:
            raise ConflictError('Termin zapisów do tego turnieju minął.')
        existing = tournament.registrations.filter(team=team).first()
        if existing and existing.status in ACTIVE:
            raise ConflictError(MSG_DUPLICATE_REGISTRATION)
        if existing:
            raise ConflictError(
                'Zgłoszenie tej drużyny zostało anulowane – ponowna rejestracja '
                'do tego turnieju nie jest możliwa.'
            )
        ensure_team_eligible(tournament, team)
        active = tournament.registrations.filter(status__in=ACTIVE).count()
        if active >= tournament.max_teams:
            raise ConflictError(
                f'Brak wolnych miejsc – turniej osiągnął limit {tournament.max_teams} drużyn.'
            )
        registration = TournamentRegistration.objects.create(
            tournament=tournament, team=team, status=RegStatus.PENDING
        )
        return registration, tournament.max_teams - active - 1

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        instance = self.get_object()
        with transaction.atomic():
            tournament = lock_tournament(instance.pk)
            if tournament.status != Status.OPEN:
                raise wrong_status(tournament)
            confirmed_count = tournament.registrations.filter(status=RegStatus.CONFIRMED).count()
            registration_closed = timezone.now() >= tournament.registration_deadline
            if not (registration_closed or confirmed_count == tournament.max_teams):
                raise ConflictError(
                    'Turniej można rozpocząć po zakończeniu zapisów lub po zapełnieniu '
                    'wszystkich miejsc potwierdzonymi drużynami.'
                )
            if confirmed_count < MIN_TEAMS_TO_START:
                raise ConflictError(
                    f'Do rozpoczęcia turnieju potrzebne są co najmniej '
                    f'{MIN_TEAMS_TO_START} potwierdzone drużyny.'
                )
            cancelled_pending = tournament.registrations.filter(
                status=RegStatus.PENDING
            ).update(status=RegStatus.CANCELLED, updated_at=timezone.now())
            seeds = confirmed_seeds(tournament)
            if tournament.format == Tournament.Format.SINGLE_ELIMINATION:
                pairs, byes = single_elimination_first_round(seeds)
                matches = [
                    Match(tournament=tournament, round_number=1, team_a=a, team_b=b)
                    for a, b in pairs
                ]
            else:
                byes = []
                matches = [
                    Match(tournament=tournament, round_number=number, team_a=a, team_b=b)
                    for number, pairs in enumerate(round_robin_schedule(seeds), start=1)
                    for a, b in pairs
                ]
            Match.objects.bulk_create(matches)
            tournament.status = Status.ONGOING
            tournament.save(update_fields=['status'])
        return Response({
            'detail': 'Turniej został rozpoczęty.',
            'status': tournament.status,
            'cancelled_pending': cancelled_pending,
            'matches_created': len(matches),
            'byes': [team.tag for team in byes],
        })

    @extend_schema(responses=TournamentRegistrationSerializer(many=True))
    @action(detail=True, methods=['get'])
    def registrations(self, request, pk=None):
        tournament = self.get_object()
        queryset = tournament.registrations.select_related('tournament', 'team')
        if tournament.organizer_id != request.user.pk:
            queryset = queryset.filter(status=RegStatus.CONFIRMED)
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=['get'])
    def standings(self, request, pk=None):
        tournament = self.get_object()
        if not tournament.matches.exists():
            return Response([])
        table = {
            team.pk: {
                'team_id': team.pk, 'tag': team.tag, 'played': 0, 'wins': 0, 'draws': 0,
                'losses': 0, 'points': 0, 'score_for': 0, 'score_against': 0,
            }
            for team in confirmed_seeds(tournament)
        }
        completed = tournament.matches.filter(status=Match.Status.COMPLETED)
        for side, opponent in (('team_a', 'team_b'), ('team_b', 'team_a')):
            rows = completed.values(side).annotate(
                played=Count('id'),
                wins=Count('id', filter=Q(winner=F(side))),
                draws=Count('id', filter=Q(winner__isnull=True)),
                score_for=Sum(f'score_{side[-1]}'),
                score_against=Sum(f'score_{opponent[-1]}'),
            )
            for row in rows:
                entry = table.get(row[side])
                if entry is None:
                    continue
                for key in ('played', 'wins', 'draws', 'score_for', 'score_against'):
                    entry[key] += row[key]
        for entry in table.values():
            entry['losses'] = entry['played'] - entry['wins'] - entry['draws']
            entry['points'] = 3 * entry['wins'] + entry['draws']
        ranking = sorted(
            table.values(),
            key=lambda e: (-e['points'], -(e['score_for'] - e['score_against']), e['tag']),
        )
        return Response(ranking)


class TournamentRegistrationViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    permission_classes = (IsAuthenticated,)
    filterset_class = RegistrationFilter
    ordering_fields = ('registered_at', 'updated_at')

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return TournamentRegistration.objects.none()
        user = self.request.user
        return (
            TournamentRegistration.objects.select_related('tournament__game', 'team__captain')
            .filter(
                Q(team__captain=user)
                | Q(team__members=user)
                | Q(tournament__organizer=user)
                | Q(status=RegStatus.CONFIRMED, tournament__is_published=True)
            )
            .distinct()
        )

    def get_serializer_class(self):
        if self.action == 'cancel':
            return RegistrationCancelSerializer
        return TournamentRegistrationSerializer

    @extend_schema(request=None, responses=TournamentRegistrationSerializer)
    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        instance = self.get_object()
        with transaction.atomic():
            tournament = lock_tournament(instance.tournament_id)
            registration = (
                TournamentRegistration.objects.select_for_update()
                .select_related('team')
                .get(pk=instance.pk)
            )
            if tournament.status != Status.OPEN:
                raise ConflictError('Zgłoszenia można potwierdzać tylko w trakcie zapisów.')
            if registration.status != RegStatus.PENDING:
                raise ConflictError('Można potwierdzić tylko oczekujące zgłoszenie.')
            ensure_team_eligible(tournament, registration.team)
            registration.status = RegStatus.CONFIRMED
            registration.save(update_fields=['status', 'updated_at'])
        registration.tournament = tournament
        return Response(TournamentRegistrationSerializer(registration).data)

    @extend_schema(request=RegistrationCancelSerializer, responses=TournamentRegistrationSerializer)
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        instance = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        is_organizer = instance.tournament.organizer_id == request.user.pk
        is_captain = instance.team.captain_id == request.user.pk
        if not (is_organizer or is_captain):
            raise PermissionDenied(
                'Tylko kapitan zgłoszonej drużyny lub organizator turnieju może anulować '
                'to zgłoszenie.'
            )
        with transaction.atomic():
            tournament = lock_tournament(instance.tournament_id)
            registration = (
                TournamentRegistration.objects.select_for_update()
                .select_related('team')
                .get(pk=instance.pk)
            )
            if registration.status == RegStatus.CANCELLED:
                raise ConflictError('To zgłoszenie zostało już anulowane.')
            if tournament.status != Status.OPEN:
                raise ConflictError('Nie można anulować zgłoszenia po rozpoczęciu turnieju.')
            cutoff = tournament.start_date - CANCELLATION_CUTOFF
            if (
                not is_organizer
                and registration.status == RegStatus.CONFIRMED
                and timezone.now() >= cutoff
            ):
                raise ConflictError(
                    'Potwierdzone zgłoszenie można anulować najpóźniej 24 godziny przed '
                    'rozpoczęciem turnieju. Skontaktuj się z organizatorem.'
                )
            registration.status = RegStatus.CANCELLED
            registration.save(update_fields=['status', 'updated_at'])
        logger.info(
            'Zgłoszenie %s anulowane przez %s. Powód: %s',
            registration.pk, request.user.username, serializer.validated_data.get('reason', ''),
        )
        registration.tournament = tournament
        return Response(TournamentRegistrationSerializer(registration).data)


class MatchViewSet(viewsets.ReadOnlyModelViewSet):
    filterset_class = MatchFilter
    ordering_fields = ('round_number', 'completed_at')

    def get_queryset(self):
        return Match.objects.select_related('tournament', 'team_a', 'team_b', 'winner').filter(
            visible_tournaments_q(self.request.user, prefix='tournament__')
        )

    def get_serializer_class(self):
        if self.action == 'report_score':
            return MatchScoreSerializer
        return MatchSerializer

    def get_permissions(self):
        if self.action == 'report_score':
            return [IsAuthenticated()]
        return [AllowAny()]

    @extend_schema(request=MatchScoreSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=['post'], url_path='report-score')
    def report_score(self, request, pk=None):
        instance = self.get_object()
        with transaction.atomic():
            tournament = lock_tournament(instance.tournament_id)
            match = (
                Match.objects.select_for_update()
                .select_related('team_a', 'team_b')
                .get(pk=instance.pk)
            )
            match.tournament = tournament
            if tournament.status != Status.ONGOING:
                raise ConflictError('Wyniki można wprowadzać tylko w trwającym turnieju.')
            if match.status != Match.Status.SCHEDULED:
                raise ConflictError('Wynik tego meczu został już wprowadzony.')
            serializer = self.get_serializer(match, data=request.data)
            serializer.is_valid(raise_exception=True)
            match.score_a = serializer.validated_data['score_a']
            match.score_b = serializer.validated_data['score_b']
            if match.score_a != match.score_b:
                match.winner = match.team_a if match.score_a > match.score_b else match.team_b
            match.status = Match.Status.COMPLETED
            match.completed_at = timezone.now()
            match.save()
            progress = self._advance_tournament(tournament, match.round_number)
        return Response({**MatchSerializer(match).data, **progress})

    def _advance_tournament(self, tournament, round_number):
        progress = {
            'round_completed': False,
            'next_round_created': False,
            'tournament_finished': False,
        }
        round_matches = tournament.matches.filter(round_number=round_number)
        progress['round_completed'] = not round_matches.filter(
            status=Match.Status.SCHEDULED
        ).exists()

        if tournament.format == Tournament.Format.ROUND_ROBIN:
            if not tournament.matches.filter(status=Match.Status.SCHEDULED).exists():
                progress['tournament_finished'] = True
        elif progress['round_completed']:
            winners = [m.winner for m in round_matches.select_related('winner').order_by('id')]
            advancing = winners
            if round_number == 1:
                played = {pk for m in round_matches for pk in (m.team_a_id, m.team_b_id)}
                advancing = [t for t in confirmed_seeds(tournament) if t.pk not in played] + winners
            if len(advancing) == 1:
                progress['tournament_finished'] = True
            else:
                Match.objects.bulk_create(
                    Match(tournament=tournament, round_number=round_number + 1, team_a=a, team_b=b)
                    for a, b in single_elimination_pairs(advancing)
                )
                progress['next_round_created'] = True

        if progress['tournament_finished']:
            tournament.status = Status.FINISHED
            tournament.save(update_fields=['status'])
        return progress
