"""Punto de entrada de «Setup Python App» de cPanel (V2 Networks)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wsgi import application  # noqa: E402,F401
