"""What people did. Two entry types, append-only JSON lines:
  sent   Jai approved this exact answer version and sent it (with a snapshot of the facts it used)
  asked  Jai sent someone a batched message (the briefing tracks the wait from here)
This is the "what did we tell each customer" record that does not exist
today. Updates owed are worked out by comparing each send's snapshot with the facts now.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "state" / "ledger.jsonl"


def read(path: Path = PATH) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append(entry: dict, path: Path = PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def reset(path: Path = PATH) -> None:
    if path.exists():
        path.unlink()
