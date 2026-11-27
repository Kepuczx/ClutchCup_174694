from decimal import Decimal

from django.db.models import Avg, Count, Max, Min, Q, Sum
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Game, Tournament, TournamentRegistration

TOP_GAMES_LIMIT = 5


def money(value):
    return None if value is None else str(Decimal(value).quantize(Decimal('0.01')))


class PlatformSummaryAPIView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        published = Tournament.objects.filter(is_published=True)

        by_status = dict.fromkeys(Tournament.Status.values, 0)
        for row in published.values('status').annotate(total=Count('id')):
            by_status[row['status']] = row['total']

        stats = published.aggregate(
            total=Count('id'),
            prize_total=Sum('prize_pool'),
            prize_avg=Avg('prize_pool'),
            prize_min=Min('prize_pool'),
            prize_max=Max('prize_pool'),
            fee_avg=Avg('entry_fee'),
            fee_min=Min('entry_fee'),
            fee_max=Max('entry_fee'),
            avg_max_teams=Avg('max_teams'),
        )

        registrations = TournamentRegistration.objects.filter(
            tournament__is_published=True
        ).aggregate(
            active=Count('id', filter=Q(status__in=TournamentRegistration.ACTIVE_STATUSES)),
            cancelled=Count('id', filter=Q(status=TournamentRegistration.Status.CANCELLED)),
        )

        top_games = (
            Game.objects.annotate(
                published_tournaments=Count(
                    'tournaments', filter=Q(tournaments__is_published=True)
                )
            )
            .filter(published_tournaments__gt=0)
            .order_by('-published_tournaments', 'name')[:TOP_GAMES_LIMIT]
        )

        avg_max_teams = stats['avg_max_teams']
        return Response({
            'tournaments_total': stats['total'],
            'tournaments_by_status': by_status,
            'prize_pool': {
                'total': money(stats['prize_total']),
                'avg': money(stats['prize_avg']),
                'min': money(stats['prize_min']),
                'max': money(stats['prize_max']),
            },
            'entry_fee': {
                'avg': money(stats['fee_avg']),
                'min': money(stats['fee_min']),
                'max': money(stats['fee_max']),
            },
            'avg_max_teams': None if avg_max_teams is None else round(avg_max_teams, 1),
            'registrations': registrations,
            'top_games': [
                {'game': game.name, 'tournaments': game.published_tournaments}
                for game in top_games
            ],
        })
