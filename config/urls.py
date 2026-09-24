from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("rag.urls")),
    path("text/", include("corpus.urls")),
]
