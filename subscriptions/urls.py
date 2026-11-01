from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path("", views.home, name="home"),
    path("preview/", views.preview, name="preview"),
    path("feed/", views.feed, name="feed"),
    path("s/<str:token>/", views.detail, name="detail"),
    path("s/<str:token>/connect/", views.connect, name="connect"),
    path("s/<str:token>/frequency/", views.frequency, name="frequency"),
    path("s/<str:token>/notifications/", views.notifications, name="notifications"),
    path("s/<str:token>/unlink/", views.unlink, name="unlink"),
    path("s/<str:token>/results/", views.results, name="results"),
    path("s/<str:token>/live/", views.live, name="live"),
    path("s/<str:token>/link-status/", views.link_status, name="link_status"),
    path("s/<str:token>/next-slot/", views.next_slot, name="next_slot"),
]
