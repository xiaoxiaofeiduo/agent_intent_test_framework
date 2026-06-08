# -*- coding: utf-8 -*-

"""Django 路由。"""

from __future__ import annotations

from django.urls import path

from intent_console import views


urlpatterns = [
    path("", views.index, name="index"),
    path("favicon.ico", views.favicon, name="favicon"),
    path("healthz", views.healthz, name="healthz"),
    path("api/cases", views.cases, name="cases"),
    path("api/preview", views.preview, name="preview"),
    path("api/run", views.run_cases, name="run_cases"),
    path("v1/chat/completions", views.mock_llm, name="mock_llm"),
]
