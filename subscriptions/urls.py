from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path("", views.home, name="home"),
    path("feed/", views.feed, name="feed"),
    path("s/<str:token>/", views.detail, name="detail"),
    path("s/<str:token>/results/", views.results, name="results"),
    path("s/<str:token>/new-count/", views.new_count, name="new_count"),
]
