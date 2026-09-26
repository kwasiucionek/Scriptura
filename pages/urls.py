from django.urls import path

from pages import views

app_name = "pages"

urlpatterns = [
    path("o-projekcie/", views.about, name="about"),
    path("jak-korzystac/", views.guide, name="guide"),
    path("korpus/", views.corpus, name="corpus"),
    path("dla-autorow/", views.for_authors, name="for_authors"),
    path("autor/", views.author, name="author"),
]
