"""Shared pytest setup for the whole test suite.

Force Qt onto the offscreen platform BEFORE any test module (and therefore any
PySide6 import) is collected, so the GUI/grading tests do not segfault on a
headless box. pytest imports conftest.py ahead of the test modules, which the
per-module ``os.environ.setdefault`` in the GUI tests could not guarantee under
every collection order.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _isolated_root() -> str:
    """A throwaway project root with the tracked config but no machine-local state.

    Top-level entries of the real root are symlinked in, except ``config/`` whose entries are
    symlinked minus ``local.toml`` and ``calibration/``, and the output folders. Pointing
    HWSORTER_ROOT here makes the suite identical on every machine.
    """
    import atexit
    import shutil
    import tempfile
    from pathlib import Path

    from hwsorter.config import project_root

    real = project_root()
    tmp = Path(tempfile.mkdtemp(prefix="hwsorter-test-root-"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    for entry in real.iterdir():
        if entry.name not in {"config", "sessions", "recordings", "logs", "reports"}:
            (tmp / entry.name).symlink_to(entry)
    (tmp / "config").mkdir()
    for entry in (real / "config").iterdir():
        if entry.name not in {"local.toml", "calibration"}:
            (tmp / "config" / entry.name).symlink_to(entry)
    return str(tmp)


def pytest_configure(config):
    # At configure time (not a fixture) so module/session-scoped fixtures are covered too.
    # Tests needing their own root still override it with monkeypatch.setenv.
    os.environ["HWSORTER_ROOT"] = _isolated_root()
