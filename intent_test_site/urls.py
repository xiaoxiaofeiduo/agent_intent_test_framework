# -*- coding: utf-8 -*-

"""Django 项目路由。"""

from __future__ import annotations

from django.urls import include, path

urlpatterns = [
    path("", include("intent_console.urls")),
]
