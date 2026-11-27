from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .cbv_views import PlatformSummaryAPIView
from .viewsets import (
    GameViewSet,
    MatchViewSet,
    TagViewSet,
    TeamViewSet,
    TournamentRegistrationViewSet,
    TournamentViewSet,
)

app_name = 'tournaments'

router = DefaultRouter()
router.register('games', GameViewSet, basename='game')
router.register('tags', TagViewSet, basename='tag')
router.register('teams', TeamViewSet, basename='team')
router.register('tournaments', TournamentViewSet, basename='tournament')
router.register('registrations', TournamentRegistrationViewSet, basename='registration')
router.register('matches', MatchViewSet, basename='match')

urlpatterns = [
    path('api/v1/summary/', PlatformSummaryAPIView.as_view(), name='summary'),
    path('api/v1/', include(router.urls)),
]
