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

from common.views import home, login_view, logout_view, run_command, source_placeholder, telegram_dashboard

urlpatterns = [
    path("", home, name="home"),
    path("telegram/", telegram_dashboard, name="telegram"),
    path("whatsapp/",      source_placeholder, {"source": "whatsapp"},      name="whatsapp"),
    path("email/",         source_placeholder, {"source": "email"},         name="email"),
    path("teams/",         source_placeholder, {"source": "teams"},         name="teams"),
    path("clickup/",       source_placeholder, {"source": "clickup"},       name="clickup"),
    path("sms/",           source_placeholder, {"source": "sms"},           name="sms"),
    path("github/",        source_placeholder, {"source": "github"},        name="github"),
    path("gdrive/",        source_placeholder, {"source": "gdrive"},        name="gdrive"),
    path("homeassistant/", source_placeholder, {"source": "homeassistant"}, name="homeassistant"),
    path("rss/",           source_placeholder, {"source": "rss"},           name="rss"),
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("run/<str:action>/", run_command, name="run_command"),
    path("admin/", admin.site.urls),
]
