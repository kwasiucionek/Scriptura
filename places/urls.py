from django.urls import path

from places import views

app_name = "places"

urlpatterns = [
    path("mapa/", views.map_page, name="map"),
    path("mapa/dane.json", views.map_data, name="map_data"),
]
