#!/usr/bin/env python3
"""Print p50/p95 from latency.jsonl (repo root by default)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from takehome_metrics import summarize_latency_jsonl, _log_paths  # noqa: E402


def main() -> None:
    path = _log_paths()[0]
    stats = summarize_latency_jsonl(path)
    if not stats:
        print(f"No data in {path}. Run a voice session first (uv run bot.py).")
        raise SystemExit(1)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
