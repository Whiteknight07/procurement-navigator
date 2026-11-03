"""Walk the demo story from a clean inbox and ledger, printing what changes after each input.

Calls the model for cache misses, so it also warms cache/llm/. Leaves no inputs or ledger behind.
"""
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pn import drafting, engine, ledger  # noqa: E402
from pn.sources import INBOX  # noqa: E402
from scripts.warm_drafts import warm  # noqa: E402

WARM = "--warm" in sys.argv

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "simulated" / "inputs"


def snap(s):
    return {k: (f.status, f.value) for k, f in s.facts.items()}


def clean():
    for p in INBOX.glob("*.json"):
        p.unlink()
    ledger.reset()


clean()
try:
    t = time.time()
    s = engine.build()
    print("base build", round(time.time() - t, 1), "errors", s.errors, "dropped", s.dropped)
    for d, rs in s.reqs.items():
        print(d, {x: sum(1 for r in rs if r.status == x) for x in {r.status for r in rs}})
    # draft and send the SOC 2 answer for Acme
    r = next(r for r in s.reqs["acme"] if "soc2" in r.facts and r.status == "ready")
    d = drafting.draft(r, s)
    print("\nDRAFT", r.id, r.title)
    for x in d["sentences"]:
        print("  ", x["kind"], x["text"], x["claim_ids"])
    print("  notes", d["internal_notes"])
    print("  checks", d["checks"]["errors"], d["checks"]["warnings"])
    ledger.append({"type": "sent", "deal": "acme", "req": r.id, "version": d["version"],
                   "text": d["text"], "facts": d["facts_used"], "date": str(s.as_of)})
    prev = snap(s)
    for p in sorted(SAMPLES.glob("*.json")):
        shutil.copy(p, INBOX / p.name)
        t = time.time()
        s = engine.build()
        now = snap(s)
        print(f"\n### ADDED {p.name} ({round(time.time() - t, 1)}s) errors={s.errors} dropped={s.dropped}")
        for k in now:
            if now[k] != prev.get(k):
                print(f"   {k}: {prev.get(k)} -> {now[k]}  | {s.facts[k].reason[:120]}")
        if p.name.startswith("01"):
            for x in s.rounds.get("acme", []):
                print("   ROUND2", x["qid"], x["relation"], x["requirement_id"], "|",
                      x["added_part"][:80], "|", x["reused_number"])
        for u in s.plan["updates"]:
            print("   UPDATE", u["kind"], u["req"],
                  [(c["fact"], c["was"]["status"], c["now"]["status"]) for c in u["changes"]])
        if WARM:
            warm(s)
        prev = now
    print("\nFINAL TOP ACTIONS")
    for a in s.plan["actions"][:12]:
        print(f"  {a.kind:16} {a.who:22} unblocks {a.unblocks:2} | {a.title}")
    print("BLOCKERS", [(b["fact"], len(b["reqs"])) for b in s.plan["blockers"][:6]])
finally:
    clean()
