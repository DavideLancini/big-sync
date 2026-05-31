"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path

from common.views import contacts_dashboard, contacts_merge_action, email_dashboard, email_detail, home, home_stats_json, item_action, items_dashboard, login_view, logout_view, plaud_audio, plaud_dashboard, plaud_detail, plaud_upload, rss_article, rss_audio, rss_audio_start, rss_audio_status, rss_dashboard, run_command, source_placeholder, telegram_dashboard, whatsapp_dashboard
from usage.views import usage_dashboard

urlpatterns = [
    path("", home, name="home"),
    path("api/home-stats/", home_stats_json, name="home_stats_json"),
    path("telegram/", telegram_dashboard, name="telegram"),
    path("whatsapp/", whatsapp_dashboard, name="whatsapp"),
    path("plaud/", plaud_dashboard, name="plaud"),
    path("plaud/upload/", plaud_upload, name="plaud_upload"),
    path("plaud/<int:pk>/", plaud_detail, name="plaud_detail"),
    path("plaud/<int:pk>/audio/", plaud_audio, name="plaud_audio"),
    path("email/", email_dashboard, name="email"),
    path("email/<str:gmail_id>/", email_detail, name="email_detail"),
    path("teams/",         source_placeholder, {"source": "teams"},         name="teams"),
    path("clickup/",       source_placeholder, {"source": "clickup"},       name="clickup"),
    path("sms/",           source_placeholder, {"source": "sms"},           name="sms"),
    path("github/",        source_placeholder, {"source": "github"},        name="github"),
    path("gdrive/",        source_placeholder, {"source": "gdrive"},        name="gdrive"),
    path("homeassistant/", source_placeholder, {"source": "homeassistant"}, name="homeassistant"),
    path("rss/", rss_dashboard, name="rss"),
    path("rss/<int:pk>/", rss_article, name="rss_article"),
    path("rss/audio/<str:date_str>/<slug:topic_slug>/", rss_audio, name="rss_audio"),
    path("api/rss_audio_start/<str:date_str>/", rss_audio_start, name="rss_audio_start"),
    path("api/rss_audio_status/<str:date_str>/", rss_audio_status, name="rss_audio_status"),
    path("items/", items_dashboard, name="items"),
    path("items/<str:google_id>/", item_action, name="item_action"),
    path("contacts/", contacts_dashboard, name="contacts"),
    path("contacts/merge/", contacts_merge_action, name="contacts_merge"),
    path("usage/", usage_dashboard, name="usage"),
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("run/<str:action>/", run_command, name="run_command"),
    path("admin/", admin.site.urls),
]
