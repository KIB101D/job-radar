from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    # Pages
    path("", views.home, name="home"),
    path("filters/", views.filters_new, name="filters"),
    path("bot/", views.bot, name="bot"),
    path("s/<str:token>/", views.detail, name="detail"),
    path("s/<str:token>/bot/", views.radar_bot, name="radar_bot"),
    path("s/<str:token>/connect/", views.connect, name="connect"),
    path("s/<str:token>/frequency/", views.frequency, name="frequency"),
    # Actions
    path("s/<str:token>/notifications/", views.notifications, name="notifications"),
    path("s/<str:token>/unlink/", views.unlink, name="unlink"),
    # HTMX fragments
    path("preview/", views.preview, name="preview"),
    path("feed/", views.feed, name="feed"),
    path("s/<str:token>/results/", views.results, name="results"),
    path("s/<str:token>/filters-form/", views.filters_form, name="filters_form"),
    path("s/<str:token>/live/", views.live, name="live"),
    path("s/<str:token>/link-status/", views.link_status, name="link_status"),
    path("s/<str:token>/next-slot/", views.next_slot, name="next_slot"),
]
