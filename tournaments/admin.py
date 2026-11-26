from django.contrib import admin
from django.db.models import Count, Q

from .models import (
    Game,
    Match,
    Tag,
    Team,
    Tournament,
    TournamentRegistration,
    UserProfile,
)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('nickname', 'user', 'role', 'is_verified')
    list_filter = ('role', 'is_verified')
    search_fields = ('nickname', 'user__username', 'steam_id')
    list_editable = ('is_verified',)
    list_select_related = ('user',)


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ('name', 'team_size', 'tournaments_count')
    list_filter = ('team_size',)
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            tournaments_total=Count('tournaments')
        )

    @admin.display(description='Liczba turniejów', ordering='tournaments_total')
    def tournaments_count(self, obj):
        return obj.tournaments_total


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'slug')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'tag', 'captain', 'members_count', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('name', 'tag', 'captain__username')
    prepopulated_fields = {'slug': ('name',)}
    filter_horizontal = ('members',)
    list_select_related = ('captain',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            members_total=Count('members', distinct=True)
        )

    @admin.display(description='Liczba zawodników', ordering='members_total')
    def members_count(self, obj):
        return obj.members_total


class TournamentRegistrationInline(admin.TabularInline):
    model = TournamentRegistration
    extra = 0
    fields = ('team', 'status', 'registered_at')
    readonly_fields = ('registered_at',)


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'game',
        'organizer',
        'status',
        'is_published',
        'start_date',
        'slots_display',
    )
    list_filter = ('status', 'format', 'game', 'is_published', 'tags')
    search_fields = ('title', 'description')
    prepopulated_fields = {'slug': ('title',)}
    list_editable = ('is_published',)
    filter_horizontal = ('tags',)
    list_select_related = ('game', 'organizer')
    date_hierarchy = 'start_date'
    inlines = (TournamentRegistrationInline,)
    fieldsets = (
        ('Podstawowe', {
            'fields': ('title', 'slug', 'game', 'organizer', 'tags', 'description'),
        }),
        ('Zasady', {
            'fields': ('format', 'max_teams', 'prize_pool', 'entry_fee'),
        }),
        ('Terminy', {
            'fields': ('registration_deadline', 'start_date'),
        }),
        ('Publikacja', {
            'fields': ('status', 'is_published'),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            active_registrations=Count(
                'registrations',
                filter=Q(
                    registrations__status__in=TournamentRegistration.ACTIVE_STATUSES
                ),
            )
        )

    @admin.display(description='Zajęte miejsca', ordering='active_registrations')
    def slots_display(self, obj):
        return f'{obj.active_registrations}/{obj.max_teams}'


@admin.register(TournamentRegistration)
class TournamentRegistrationAdmin(admin.ModelAdmin):
    list_display = ('team', 'tournament', 'status', 'registered_at')
    list_filter = ('status', 'tournament')
    search_fields = ('team__name', 'tournament__title')
    list_select_related = ('team', 'tournament__game')
    readonly_fields = ('registered_at', 'updated_at')


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = (
        '__str__',
        'tournament',
        'round_number',
        'score_a',
        'score_b',
        'winner',
        'status',
    )
    list_filter = ('status', 'tournament')
    search_fields = ('team_a__name', 'team_b__name')
    list_select_related = ('tournament__game', 'team_a', 'team_b', 'winner')
    fieldsets = (
        ('Pojedynek', {
            'fields': ('tournament', 'round_number', 'team_a', 'team_b'),
        }),
        ('Wynik', {
            'fields': ('score_a', 'score_b', 'winner', 'status', 'completed_at'),
        }),
    )
