"""Draft every approvable answer once so the demo can approve offline (fills cache/llm).

    python scripts/warm_drafts.py            # the current state
    python scripts/demo_story.py --warm      # every state the demo story passes through
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pn import drafting, engine  # noqa: E402


def warm(s) -> None:
    ready = [r for rs in s.reqs.values() for r in rs if r.status == "ready"]

    def go(r):
        try:
            c = drafting.draft(r, s)["checks"]
            return r.id, "can approve" if c["can_approve"] else f"checker blocks: {c['errors'][:2]}"
        except Exception as e:  # report and keep going
            return r.id, f"failed: {e}"

    with ThreadPoolExecutor(8) as pool:
        for rid, msg in pool.map(go, ready):
            print("   warm", rid, msg)


if __name__ == "__main__":
    warm(engine.build())
