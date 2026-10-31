from django import forms

from radar.matching import MAX_KEYWORD_LENGTH, MAX_KEYWORDS, parse_keywords
from radar.models import Source

from .models import Subscription

KEYWORDS_FIELD_MAX_LENGTH = 400


def category_choices() -> list[tuple[str, str]]:
    categories = (
        Source.objects.filter(is_enabled=True)
        .order_by("category")
        .values_list("category", flat=True)
        .distinct()
    )
    return [(c, c) for c in categories]


class FiltersForm(forms.Form):
    include_keywords = forms.CharField(
        label="Шукати слова",
        help_text="Через кому: python, django, junior. Підійде вакансія, де є хоча б одне.",
        required=False,
        max_length=KEYWORDS_FIELD_MAX_LENGTH,
        widget=forms.TextInput(attrs={"placeholder": "python, django", "autocomplete": "off"}),
    )
    exclude_keywords = forms.CharField(
        label="Сховати вакансії зі словами",
        help_text="Наприклад: senior, lead, php.",
        required=False,
        max_length=KEYWORDS_FIELD_MAX_LENGTH,
        widget=forms.TextInput(attrs={"placeholder": "senior, lead", "autocomplete": "off"}),
    )
    categories = forms.MultipleChoiceField(
        label="Категорії",
        help_text="Якщо нічого не вибрано, шукаємо в усіх.",
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    remote_only = forms.BooleanField(label="Тільки віддалено", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categories"].choices = category_choices()

    @classmethod
    def for_subscription(cls, subscription: Subscription, *args, **kwargs) -> "FiltersForm":
        initial = {
            "include_keywords": ", ".join(subscription.include_keywords),
            "exclude_keywords": ", ".join(subscription.exclude_keywords),
            "categories": subscription.categories,
            "remote_only": subscription.remote_only,
        }
        return cls(*args, initial=initial, **kwargs)

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
        subscription.save()
        return subscription
