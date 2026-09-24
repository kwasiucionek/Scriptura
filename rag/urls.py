from django.contrib.auth import views as auth_views
from django.urls import path

from rag import views

app_name = "rag"

urlpatterns = [
    path("", views.index, name="index"),
    path("ask/stream", views.ask_stream, name="ask_stream"),
    path("conversations/", views.conversations, name="conversations"),
    path(
        "conversations/<int:pk>/", views.conversation_detail, name="conversation_detail"
    ),
    path(
        "conversations/<int:pk>/delete/",
        views.conversation_delete,
        name="conversation_delete",
    ),
    path("account/", views.account, name="account"),
    path("export/citations", views.export_citations, name="export_citations"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="rag/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
