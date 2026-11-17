"""Shared pytest setup for the whole test suite.

Force Qt onto the offscreen platform BEFORE any test module (and therefore any
PySide6 import) is collected, so the GUI/grading tests do not segfault on a
headless box. pytest imports conftest.py ahead of the test modules, which the
per-module ``os.environ.setdefault`` in the GUI tests could not guarantee under
every collection order.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
