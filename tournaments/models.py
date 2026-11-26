from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import (
    MaxValueValidator,
    MinLengthValidator,
    MinValueValidator,
    RegexValidator,
)
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils.text import slugify

MAX_SUBSTITUTES = 2
MAX_TEAM_MEMBERS = 12
MIN_TEAMS_TO_START = 2
CANCELLATION_CUTOFF = timedelta(hours=24)


def unique_slug(instance, source):
    max_length = instance._meta.get_field('slug').max_length
    base = slugify(source)[:max_length] or instance._meta.model_name
    slug = base
    suffix_number = 2
    queryset = type(instance)._default_manager.exclude(pk=instance.pk)
    while queryset.filter(slug=slug).exists():
        suffix = f'-{suffix_number}'
        slug = f'{base[:max_length - len(suffix)]}{suffix}'
        suffix_number += 1
    return slug


class AutoSlugMixin:
    slug_source = 'name'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, getattr(self, self.slug_source))
        super().save(*args, **kwargs)


class UserProfile(models.Model):
    class Role(models.TextChoices):
        ORGANIZER = 'organizer', 'Organizator'
        PLAYER = 'player', 'Gracz'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
        verbose_name='użytkownik',
    )
    nickname = models.CharField(
        'pseudonim',
        max_length=32,
        unique=True,
        validators=[MinLengthValidator(3)],
        error_messages={'unique': 'Ten pseudonim jest już zajęty.'},
    )
    role = models.CharField(
        'rola', max_length=16, choices=Role.choices, default=Role.PLAYER
    )
    steam_id = models.CharField(
        'SteamID64',
        max_length=17,
        blank=True,
        default='',
        validators=[
            RegexValidator(
                r'^7656119\d{10}$',
                'Nieprawidłowy SteamID64 (17 cyfr, prefiks 7656119).',
            )
        ],
    )
    is_verified = models.BooleanField('zweryfikowany', default=False)

    class Meta:
        ordering = ['nickname']
        verbose_name = 'profil użytkownika'
        verbose_name_plural = 'profile użytkowników'
        constraints = [
            models.UniqueConstraint(
                fields=['steam_id'],
                condition=~Q(steam_id=''),
                name='unique_nonempty_steam_id',
                violation_error_message='Ten SteamID jest już przypisany do innego konta.',
            ),
        ]

    def __str__(self):
        return f'{self.nickname} ({self.get_role_display()})'


class Game(AutoSlugMixin, models.Model):
    name = models.CharField(
        'nazwa',
        max_length=100,
        unique=True,
        error_messages={'unique': 'Gra o tej nazwie już istnieje.'},
    )
    slug = models.SlugField('slug', max_length=110, unique=True, blank=True)
    team_size = models.PositiveSmallIntegerField(
        'rozmiar składu',
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    description = models.TextField('opis', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'gra'
        verbose_name_plural = 'gry'
        constraints = [
            models.CheckConstraint(
                condition=Q(team_size__gte=1) & Q(team_size__lte=10),
                name='game_team_size_range',
                violation_error_message='Rozmiar składu musi mieścić się w przedziale 1–10.',
            ),
        ]

    def __str__(self):
        return self.name


class Tag(AutoSlugMixin, models.Model):
    name = models.CharField(
        'nazwa',
        max_length=30,
        unique=True,
        validators=[
            RegexValidator(
                r'^[a-z0-9]+$',
                'Tag może zawierać tylko małe litery i cyfry, bez znaku #.',
            )
        ],
        error_messages={'unique': 'Taki tag już istnieje.'},
    )
    slug = models.SlugField('slug', max_length=40, unique=True, blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'tag'
        verbose_name_plural = 'tagi'

    def __str__(self):
        return f'#{self.name}'


class Team(AutoSlugMixin, models.Model):
    name = models.CharField('nazwa', max_length=64)
    slug = models.SlugField('slug', max_length=80, unique=True, blank=True)
    tag = models.CharField(
        'skrót',
        max_length=5,
        validators=[
            RegexValidator(
                r'^[A-Z0-9]{2,5}$',
                'Skrót drużyny to 2–5 wielkich liter lub cyfr.',
            )
        ],
    )
    captain = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='captained_teams',
        verbose_name='kapitan',
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='teams',
        blank=True,
        verbose_name='zawodnicy',
    )
    created_at = models.DateTimeField('utworzono', auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'drużyna'
        verbose_name_plural = 'drużyny'
        constraints = [
            models.UniqueConstraint(
                Lower('name'),
                name='unique_team_name_ci',
                violation_error_message='Drużyna o tej nazwie już istnieje.',
            ),
            models.UniqueConstraint(
                fields=['tag'],
                name='unique_team_tag',
                violation_error_message='Drużyna o tym skrócie już istnieje.',
            ),
        ]

    def __str__(self):
        return f'[{self.tag}] {self.name}'


class Tournament(AutoSlugMixin, models.Model):
    slug_source = 'title'

    class Format(models.TextChoices):
        SINGLE_ELIMINATION = 'single_elimination', 'Pojedyncza eliminacja'
        ROUND_ROBIN = 'round_robin', 'Każdy z każdym'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Szkic'
        OPEN = 'open', 'Zapisy otwarte'
        ONGOING = 'ongoing', 'W trakcie'
        FINISHED = 'finished', 'Zakończony'

    title = models.CharField(
        'tytuł', max_length=150, validators=[MinLengthValidator(5)]
    )
    slug = models.SlugField('slug', max_length=170, unique=True, blank=True)
    game = models.ForeignKey(
        Game,
        on_delete=models.PROTECT,
        related_name='tournaments',
        verbose_name='gra',
    )
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='organized_tournaments',
        verbose_name='organizator',
    )
    tags = models.ManyToManyField(
        Tag, related_name='tournaments', blank=True, verbose_name='tagi'
    )
    description = models.TextField('opis', blank=True)
    format = models.CharField(
        'format',
        max_length=32,
        choices=Format.choices,
        default=Format.SINGLE_ELIMINATION,
    )
    max_teams = models.PositiveSmallIntegerField(
        'limit drużyn',
        default=16,
        validators=[MinValueValidator(2), MaxValueValidator(64)],
    )
    prize_pool = models.DecimalField(
        'pula nagród',
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    entry_fee = models.DecimalField(
        'wpisowe',
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    registration_deadline = models.DateTimeField('koniec zapisów')
    start_date = models.DateTimeField('data rozpoczęcia')
    status = models.CharField(
        'status', max_length=16, choices=Status.choices, default=Status.DRAFT
    )
    is_published = models.BooleanField('opublikowany', default=False)
    created_at = models.DateTimeField('utworzono', auto_now_add=True)

    class Meta:
        ordering = ['start_date', 'id']
        verbose_name = 'turniej'
        verbose_name_plural = 'turnieje'
        constraints = [
            models.CheckConstraint(
                condition=Q(registration_deadline__lt=F('start_date')),
                name='tournament_deadline_before_start',
                violation_error_message='Termin zapisów musi upływać przed rozpoczęciem turnieju.',
            ),
            models.CheckConstraint(
                condition=Q(max_teams__gte=2) & Q(max_teams__lte=64),
                name='tournament_max_teams_range',
                violation_error_message='Limit drużyn musi mieścić się w przedziale 2–64.',
            ),
            models.CheckConstraint(
                condition=Q(prize_pool__gte=0) & Q(entry_fee__gte=0),
                name='tournament_non_negative_money',
                violation_error_message='Pula nagród i wpisowe nie mogą być ujemne.',
            ),
            models.CheckConstraint(
                condition=Q(is_published=True) | Q(status__in=['draft', 'finished']),
                name='tournament_active_must_be_published',
                violation_error_message='Turniej z otwartymi zapisami lub w trakcie musi być opublikowany.',
            ),
        ]
        indexes = [
            models.Index(fields=['status', 'is_published']),
            models.Index(fields=['start_date']),
        ]

    def __str__(self):
        return f'{self.title} ({self.game.name})'


class TournamentRegistration(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Oczekujące'
        CONFIRMED = 'confirmed', 'Potwierdzone'
        CANCELLED = 'cancelled', 'Anulowane'

    ACTIVE_STATUSES = (Status.PENDING, Status.CONFIRMED)

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        related_name='registrations',
        verbose_name='turniej',
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name='registrations',
        verbose_name='drużyna',
    )
    status = models.CharField(
        'status', max_length=16, choices=Status.choices, default=Status.PENDING
    )
    registered_at = models.DateTimeField('data zgłoszenia', auto_now_add=True)
    updated_at = models.DateTimeField('ostatnia zmiana', auto_now=True)

    class Meta:
        ordering = ['registered_at', 'id']
        verbose_name = 'zgłoszenie'
        verbose_name_plural = 'zgłoszenia'
        constraints = [
            models.UniqueConstraint(
                fields=['tournament', 'team'],
                name='unique_tournament_team_registration',
                violation_error_message='Ta drużyna jest już zgłoszona do tego turnieju.',
            ),
        ]

    def __str__(self):
        return f'{self.team} → {self.tournament.title} [{self.get_status_display()}]'


class Match(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Zaplanowany'
        COMPLETED = 'completed', 'Rozegrany'

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        related_name='matches',
        verbose_name='turniej',
    )
    team_a = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name='matches_as_team_a',
        verbose_name='drużyna A',
    )
    team_b = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name='matches_as_team_b',
        verbose_name='drużyna B',
    )
    winner = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='won_matches',
        verbose_name='zwycięzca',
    )
    round_number = models.PositiveSmallIntegerField(
        'runda', validators=[MinValueValidator(1)]
    )
    score_a = models.PositiveSmallIntegerField('wynik A', default=0)
    score_b = models.PositiveSmallIntegerField('wynik B', default=0)
    status = models.CharField(
        'status', max_length=16, choices=Status.choices, default=Status.SCHEDULED
    )
    completed_at = models.DateTimeField('data rozegrania', null=True, blank=True)

    class Meta:
        ordering = ['tournament', 'round_number', 'id']
        verbose_name = 'mecz'
        verbose_name_plural = 'mecze'
        constraints = [
            models.CheckConstraint(
                condition=~Q(team_a=F('team_b')),
                name='match_distinct_teams',
                violation_error_message='Drużyna nie może grać sama ze sobą.',
            ),
            models.CheckConstraint(
                condition=(
                    Q(winner__isnull=True)
                    | Q(winner=F('team_a'))
                    | Q(winner=F('team_b'))
                ),
                name='match_winner_is_participant',
                violation_error_message='Zwycięzcą może być tylko jedna z drużyn biorących udział w meczu.',
            ),
            models.CheckConstraint(
                condition=(
                    Q(status='scheduled', winner__isnull=True, completed_at__isnull=True)
                    | Q(status='completed', completed_at__isnull=False)
                ),
                name='match_status_consistency',
                violation_error_message='Zaplanowany mecz nie może mieć zwycięzcy ani daty rozegrania, a rozegrany musi mieć datę rozegrania.',
            ),
        ]

    def __str__(self):
        return f'R{self.round_number}: {self.team_a.tag} vs {self.team_b.tag}'
