import django_filters
from django.db.models import F, Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field

from .models import Game, Match, Tag, Team, Tournament, TournamentRegistration


@extend_schema_field(OpenApiTypes.STR)
class SlugMultipleChoiceFilter(django_filters.ModelMultipleChoiceFilter):
    pass


class GameFilter(django_filters.FilterSet):
    team_size_min = django_filters.NumberFilter(field_name='team_size', lookup_expr='gte')
    team_size_max = django_filters.NumberFilter(field_name='team_size', lookup_expr='lte')

    class Meta:
        model = Game
        fields = ('team_size',)


class TeamFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')
    tag = django_filters.CharFilter(lookup_expr='iexact')
    member = django_filters.NumberFilter(field_name='members', distinct=True)
    members_min = django_filters.NumberFilter(field_name='members_count', lookup_expr='gte')
    members_max = django_filters.NumberFilter(field_name='members_count', lookup_expr='lte')
    created_after = django_filters.IsoDateTimeFilter(field_name='created_at', lookup_expr='gte')
    created_before = django_filters.IsoDateTimeFilter(field_name='created_at', lookup_expr='lte')

    class Meta:
        model = Team
        fields = ('name', 'tag', 'captain', 'member')


class TournamentFilter(django_filters.FilterSet):
    game = django_filters.CharFilter(field_name='game__slug')
    tags = SlugMultipleChoiceFilter(
        field_name='tags__slug', to_field_name='slug', queryset=Tag.objects.all()
    )
    organizer = django_filters.CharFilter(field_name='organizer__username')
    prize_pool_min = django_filters.NumberFilter(field_name='prize_pool', lookup_expr='gte')
    prize_pool_max = django_filters.NumberFilter(field_name='prize_pool', lookup_expr='lte')
    entry_fee_min = django_filters.NumberFilter(field_name='entry_fee', lookup_expr='gte')
    entry_fee_max = django_filters.NumberFilter(field_name='entry_fee', lookup_expr='lte')
    max_teams_min = django_filters.NumberFilter(field_name='max_teams', lookup_expr='gte')
    max_teams_max = django_filters.NumberFilter(field_name='max_teams', lookup_expr='lte')
    start_after = django_filters.IsoDateTimeFilter(field_name='start_date', lookup_expr='gte')
    start_before = django_filters.IsoDateTimeFilter(field_name='start_date', lookup_expr='lte')
    has_free_slots = django_filters.BooleanFilter(method='filter_has_free_slots')

    class Meta:
        model = Tournament
        fields = ('game', 'tags', 'status', 'format', 'organizer')

    def filter_has_free_slots(self, queryset, name, value):
        free = Q(active_registrations_count__lt=F('max_teams'))
        return queryset.filter(free if value else ~free)


class RegistrationFilter(django_filters.FilterSet):
    registered_after = django_filters.IsoDateTimeFilter(
        field_name='registered_at', lookup_expr='gte'
    )
    registered_before = django_filters.IsoDateTimeFilter(
        field_name='registered_at', lookup_expr='lte'
    )

    class Meta:
        model = TournamentRegistration
        fields = ('tournament', 'team', 'status')


class MatchFilter(django_filters.FilterSet):
    round_min = django_filters.NumberFilter(field_name='round_number', lookup_expr='gte')
    round_max = django_filters.NumberFilter(field_name='round_number', lookup_expr='lte')
    team = django_filters.NumberFilter(method='filter_team')

    class Meta:
        model = Match
        fields = ('tournament', 'round_number', 'status')

    def filter_team(self, queryset, name, value):
        return queryset.filter(Q(team_a=value) | Q(team_b=value))
