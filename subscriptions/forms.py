from django import forms

from radar.attributes import LEVEL_LABELS
from radar.matching import MAX_KEYWORD_LENGTH, MAX_KEYWORDS, Filters, parse_keywords
from radar.models import Source
from radar.search import popular_cities

from .models import Frequency, Subscription
from .schedule import is_valid_timezone

KEYWORDS_FIELD_MAX_LENGTH = 400
FILTER_FIELDS = (
    "include_keywords",
    "exclude_keywords",
    "categories",
    "remote_only",
    "cities",
    "levels",
    "salary_min",
    "salary_unknown",
    "max_age_days",
)

FREQUENCY_HINTS = {
    Frequency.HOURLY: "Для активного пошуку, коли важлива кожна година",
    Frequency.EVERY_3H: "Баланс між швидкістю і тишею",
    Frequency.EVERY_6H: "Кілька разів за день",
    Frequency.EVERY_12H: "Зранку і ввечері: 09:00 і 21:00",
    Frequency.DAILY: "Один дайджест о 09:00",
    Frequency.OFF: "Лише ручна перевірка в боті",
}
RECOMMENDED_FREQUENCY = Frequency.EVERY_3H
QUIET_START_CHOICES = [(h, f"{h:02d}:00") for h in (20, 21, 22, 23, 0)]
QUIET_END_CHOICES = [(h, f"{h:02d}:00") for h in (6, 7, 8, 9, 10)]


SALARY_CHOICES = [(0, "Будь-яка")] + [
    (amount, f"від ${amount}") for amount in (500, 1000, 1500, 2000, 2500, 3000, 4000, 5000)
]
MAX_AGE_CHOICES = [(0, "За весь час"), (1, "За 24 години"), (3, "За 3 дні"), (7, "За тиждень")]
MAX_CITIES = 20


def city_choices(selected=()) -> list[tuple[str, str]]:
    """Popular cities plus any the radar already has (they may have dropped off the list)."""
    cities = popular_cities()
    extra = [c for c in selected if c not in cities]
    return [(c, c) for c in [*cities, *extra]]


class CityField(forms.MultipleChoiceField):
    """
    Chips come from the popular cities, but any reasonable city name is accepted:
    a radar keeps a city that's rare this week, and the bot saves the same values.
    """

    def valid_value(self, value) -> bool:
        return 0 < len(value) <= 60


def category_choices() -> list[tuple[str, str]]:
    categories = (
        Source.objects.filter(is_enabled=True)
        .order_by("category")
        .values_list("category", flat=True)
        .distinct()
    )
    return [(c, c) for c in categories]


class TimezoneField(forms.CharField):
    """Hidden field filled by the browser (Intl API). Invalid values are ignored."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("max_length", 50)
        kwargs.setdefault("widget", forms.HiddenInput(attrs={"data-timezone": ""}))
        super().__init__(**kwargs)

    def clean(self, value):
        value = super().clean(value)
        return value if is_valid_timezone(value) else ""


class FiltersForm(forms.Form):
    include_keywords = forms.CharField(
        label="Ключові слова",
        help_text="Підійде вакансія, де є хоча б одне з них.",
        required=False,
        max_length=KEYWORDS_FIELD_MAX_LENGTH,
        widget=forms.TextInput(
            attrs={"placeholder": "Додати слово, напр. FastAPI", "autocomplete": "off"}
        ),
    )
    exclude_keywords = forms.CharField(
        label="Виключені слова",
        help_text="Вакансії з цими словами не показуємо.",
        required=False,
        max_length=KEYWORDS_FIELD_MAX_LENGTH,
        widget=forms.TextInput(attrs={"placeholder": "Напр. senior, lead", "autocomplete": "off"}),
    )
    categories = forms.MultipleChoiceField(
        label="Категорії DOU",
        help_text="Можна кілька. Якщо нічого не вибрано, шукаємо в усіх.",
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    levels = forms.MultipleChoiceField(
        label="Рівень",
        help_text="За назвою вакансії. Якщо рівня в назві немає, вакансію показуємо.",
        required=False,
        choices=list(LEVEL_LABELS.items()),
        widget=forms.CheckboxSelectMultiple,
    )
    remote_only = forms.BooleanField(label="Віддалено", required=False)
    cities = CityField(
        label="Місто",
        help_text="Підійде віддалена робота або будь-яке з вибраних міст.",
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    salary_min = forms.TypedChoiceField(
        label="Зарплата", choices=SALARY_CHOICES, coerce=int, required=False, empty_value=0
    )
    salary_unknown = forms.BooleanField(
        label="Показувати вакансії без зарплати",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"data-default-checked": ""}),
    )
    max_age_days = forms.TypedChoiceField(
        label="Свіжість",
        help_text="Лише для списку на сайті. Бот і так надсилає тільки нове.",
        choices=MAX_AGE_CHOICES,
        coerce=int,
        required=False,
        empty_value=0,
    )
    timezone = TimezoneField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categories"].choices = category_choices()
        selected = self.data.getlist("cities") if hasattr(self.data, "getlist") else []
        selected = selected or self.initial.get("cities") or []
        self.fields["cities"].choices = city_choices(selected)

    @classmethod
    def for_subscription(cls, subscription: Subscription, *args, **kwargs) -> "FiltersForm":
        initial = {
            "include_keywords": ", ".join(subscription.include_keywords),
            "exclude_keywords": ", ".join(subscription.exclude_keywords),
            "categories": subscription.categories,
            "remote_only": subscription.remote_only,
            "cities": subscription.cities,
            "levels": subscription.levels,
            "salary_min": subscription.salary_min,
            "salary_unknown": subscription.salary_unknown,
            "max_age_days": subscription.max_age_days,
        }
        return cls(*args, initial=initial, **kwargs)

    def clean_cities(self) -> list[str]:
        cities = list(dict.fromkeys(self.cleaned_data.get("cities") or []))
        if len(cities) > MAX_CITIES:
            raise forms.ValidationError(f"Не більше {MAX_CITIES} міст.")
        return cities

    def filters(self) -> Filters:
        """The filters in this (valid) form, saved or not: for the live preview."""
        data = self.cleaned_data
        return Filters(
            include=tuple(data["include_keywords"]),
            exclude=tuple(data["exclude_keywords"]),
            categories=frozenset(data["categories"]),
            remote_only=data["remote_only"],
            cities=frozenset(data["cities"]),
            levels=frozenset(data["levels"]),
            salary_min=data["salary_min"] or 0,
            salary_unknown=data["salary_unknown"],
        )

    def _clean_keywords(self, field_name: str) -> list[str]:
        keywords = parse_keywords(self.cleaned_data.get(field_name, ""))
        if len(keywords) > MAX_KEYWORDS:
            raise forms.ValidationError(f"Не більше {MAX_KEYWORDS} слів.")
        too_long = [k for k in keywords if len(k) > MAX_KEYWORD_LENGTH]
        if too_long:
            raise forms.ValidationError(
                f"«{too_long[0][:20]}…» задовге: максимум {MAX_KEYWORD_LENGTH} символів."
            )
        return keywords

    def clean_include_keywords(self) -> list[str]:
        return self._clean_keywords("include_keywords")

    def clean_exclude_keywords(self) -> list[str]:
        return self._clean_keywords("exclude_keywords")

    def save(self, subscription: Subscription | None = None) -> Subscription:
        subscription = subscription or Subscription()
        data = self.cleaned_data
        subscription.include_keywords = data["include_keywords"]
        subscription.exclude_keywords = data["exclude_keywords"]
        subscription.categories = sorted(data["categories"])
        subscription.remote_only = data["remote_only"]
        subscription.cities = data["cities"]
        subscription.levels = [level for level in LEVEL_LABELS if level in data["levels"]]
        subscription.salary_min = data["salary_min"] or 0
        subscription.salary_unknown = data["salary_unknown"]
        subscription.max_age_days = data["max_age_days"] or 0
        if data.get("timezone") and subscription.pk is None:
            # The browser's zone on creation; later changes come from the
            # notifications form, so editing filters abroad doesn't move digests.
            subscription.timezone = data["timezone"]
        if subscription.pk is None:
            subscription.save()
        else:
            # Only the filter columns: a full save would write back every other
            # column as loaded at the start of the request (pause, frequency...),
            # undoing a change the bot made in the meantime.
            subscription.save(update_fields=[*FILTER_FIELDS, "updated_at"])
        return subscription


class NotificationsForm(forms.ModelForm):
    """Frequency + quiet hours, shared by the radar page card and the step-3 page."""

    quiet_hours_start = forms.TypedChoiceField(label="з", choices=QUIET_START_CHOICES, coerce=int)
    quiet_hours_end = forms.TypedChoiceField(label="до", choices=QUIET_END_CHOICES, coerce=int)
    timezone = TimezoneField()

    class Meta:
        model = Subscription
        fields = ["frequency", "quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end"]
        widgets = {"frequency": forms.RadioSelect}
        labels = {"frequency": "Автосповіщення", "quiet_hours_enabled": "Тихі години"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Keep a stored value selectable even if it's outside the default lists.
        for name in ("quiet_hours_start", "quiet_hours_end"):
            field = self.fields[name]
            current = getattr(self.instance, name, None)
            if current is not None and current not in dict(field.choices):
                field.choices = sorted([*field.choices, (current, f"{current:02d}:00")])

    def frequency_options(self):
        """Radio options with design hints, for templates."""
        current = self.data.get("frequency") if self.is_bound else self.instance.frequency
        return [
            {
                "value": value,
                "label": label,
                "hint": FREQUENCY_HINTS[value],
                "recommended": value == RECOMMENDED_FREQUENCY,
                "checked": value == current,
                "id": f"freq-{value}",
            }
            for value, label in Frequency.choices
        ]

    def save(self, commit=True):
        subscription = super().save(commit=False)
        if self.cleaned_data.get("timezone"):
            subscription.timezone = self.cleaned_data["timezone"]
        if commit:
            fields = [*self.Meta.fields, "updated_at"]
            if self.cleaned_data.get("timezone"):
                fields.append("timezone")
            subscription.save(update_fields=fields)  # never touch columns the bot owns
        return subscription
