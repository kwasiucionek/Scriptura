from django.urls import path

from corpus import views

app_name = "corpus"

urlpatterns = [
    path("", views.index, name="index"),
    path("ref/", views.reference, name="reference"),
    path("concordance/", views.concordance, name="concordance"),
    path("search/", views.search, name="search"),
]
