#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Django 管理入口。"""

from __future__ import annotations

import os
import sys


def main() -> None:
    """执行 Django 命令。"""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "intent_test_site.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
