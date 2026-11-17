"""Regenerate the golden decision logs in tests/replay/goldens/ (do this INTENTIONALLY, after
a deliberate pipeline or config change, and review the diff).

    uv run python scripts/update_goldens.py            # rewrite all goldens
    uv run python scripts/update_goldens.py --check    # exit 1 if any golden is out of date
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "tests" / "replay" / "goldens"


def generate(name: str, workdir: Path) -> str:
    """Canonical golden text for scenario ``name`` (renders, records, replays)."""
    from hwsorter.bench import (
        canonical_text,
        golden_parts,
        replay_decisions,
        scene_for_cfg,
        write_synthetic_recording,
    )
    from hwsorter.config import load_config

    cfg = load_config()
    rec = write_synthetic_recording(workdir / name, golden_parts(name), scene_for_cfg(cfg))
    _, records, _ = replay_decisions(cfg, str(rec), workdir / f"{name}.jsonl")
    return canonical_text(records)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 if out of date")
    args = ap.parse_args(argv)
    from hwsorter.bench import GOLDEN_SCENARIOS

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    stale = 0
    with tempfile.TemporaryDirectory(prefix="sorter-goldens-") as tmp:
        for name in GOLDEN_SCENARIOS:
            text = generate(name, Path(tmp))
            path = GOLDEN_DIR / f"{name}.jsonl"
            same = path.is_file() and path.read_text() == text
            if args.check:
                print(f"{'ok   ' if same else 'STALE'} {path.relative_to(ROOT)}")
                stale += not same
            else:
                path.write_text(text)
                print(
                    f"{'unchanged' if same else 'wrote    '} {path.relative_to(ROOT)} ({len(text)} bytes)"
                )
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
