"""Score the built state against the hand-written answer key in evals/expectations.py.

    .venv/bin/python -m evals.run

Builds from the committed LLM cache first (offline). If any model call misses the cache, that
build is redone online (needs OPENROUTER_API_KEY in .env). Inputs added for the round-2 and story
checks are copied into state/inbox/ and always removed afterwards; the ledger used is a temporary
file, never state/ledger.jsonl. Writes evals/results.md.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from pn import engine, llm, resolve
from pn.sources import INBOX

from . import expectations as K

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "simulated" / "inputs"
RESULTS = Path(__file__).resolve().parent / "results.md"

checks: list[dict] = []
builds: list[str] = []


def check(section: str, name: str, ok: bool, reason: str) -> None:
    checks.append({"section": section, "name": name, "ok": bool(ok), "reason": reason})
    print(f"  {'PASS' if ok else 'FAIL'}  {name} — {reason}")


# ---------- building ----------

def build(label: str, ledger_path: Path):
    """Offline first; if any model call misses the cache, rebuild online."""
    misses: list[str] = []
    orig = llm.call_json

    def watched(*a, **k):
        try:
            return orig(*a, **k)
        except llm.LLMError as e:
            if k.get("offline"):
                misses.append(str(e))
            raise

    llm.call_json = watched
    t = time.time()
    try:
        st = engine.build(as_of=K.AS_OF, offline=True, ledger_path=ledger_path)
    finally:
        llm.call_json = orig
    mode = "offline (cache)"
    if misses:
        st = engine.build(as_of=K.AS_OF, offline=False, ledger_path=ledger_path)
        mode = f"online ({len(misses)} cache misses)"
    builds.append(f"{label}: {mode}, {time.time() - t:.1f}s" + (f", errors: {st.errors}" if st.errors else ""))
    print(f"[build] {builds[-1]}")
    return st


class Inbox:
    """Copy sample inputs into state/inbox/ and always remove exactly those files."""

    def __init__(self):
        self.added: list[Path] = []

    def add(self, name: str) -> None:
        dst = INBOX / name
        shutil.copy(SAMPLES / name, dst)
        self.added.append(dst)

    def clear(self) -> None:
        for p in self.added:
            p.unlink(missing_ok=True)
        self.added = []


# ---------- helpers ----------

def winners(st):
    return [(k, f) for k, f in st.facts.items() if f.winner]


def req_with_ask(st, deal, ask_id):
    return next((r for r in st.reqs[deal] if ask_id in r.ask_ids), None)


def req_by_id(st, rid):
    deal = rid.split(":")[0]
    return next((r for r in st.reqs.get(deal, []) if r.id == rid), None)


# ---------- base case ----------

def base_checks(st):
    print("\n## Traps")
    sec = "Traps"
    hints = [c for f in st.facts.values() for c in f.hints]
    bad = [k for k, f in winners(st) if f.winner.applies_to == "other_customer" or f.winner.tier >= 9
           or any(n.lower() in f.winner.quote.lower() for n in K.OTHER_CUSTOMERS)
           or any(s in st.sources[f.winner.source_id].title for s in K.OTHER_CUSTOMER_SOURCES)]
    check(sec, "Other-customer claims (Meridian, BluePeak) never win a fact", hints and not bad,
          f"{len(hints)} other-customer claims seen as hints; winning anyway: {bad or 'none'}")

    drafts = [c for c in st.claims if c.assertion == "questionnaire_draft"]
    bad = [k for k, f in winners(st) if f.winner.assertion == "questionnaire_draft"]
    check(sec, "Questionnaire draft answers never win a fact", drafts and not bad,
          f"{len(drafts)} draft claims read; winning anyway: {bad or 'none'}")

    d = K.DRAFT_RETENTION
    fl = [x["text"] for x in st.draft_flags if x["fact"] == d["fact"] and d["draft"] in x["text"]]
    ok = any("disagrees" in t and d["truth"] in t for t in fl)
    check(sec, f"\"{d['draft']}\" draft flagged on {d['fact']} as disagreeing with {d['truth']}", ok,
          fl[0] if fl else "no flag on this fact")

    d = K.DRAFT_RTO
    fl = [x["text"] for x in st.draft_flags if x["fact"] == d["fact"] and d["draft"] in x["text"]
          and "[acme]" in x["text"]]
    ok = bool(fl) and any("nothing behind it" in t or "another customer" in t for t in fl) \
        and st.facts[d["fact"]].status != "verified"
    check(sec, f"\"{d['draft']}\" RTO/RPO draft flagged (nothing behind it / another customer's)", ok,
          (fl[0] if fl else "no flag") + f" · fact status {st.facts[d['fact']].status}")

    for deal in st.deal_names:
        r0 = [r for r in st.reqs[deal] if r.status == "missing_text"]
        templ = {a.id for a in st.asks.values() if a.deal == deal and a.template}
        leaked = [r.id for r in st.reqs[deal] if r.status != "missing_text" and templ & set(r.ask_ids)]
        drafted = [c.id for c in drafts if any(c.note.startswith(f"typed into {a.split(':')[-1]}") for a in templ)
                   and c.deal == deal]
        action = any(a.kind == "request_text" and deal in a.deals for a in st.plan["actions"])
        ok = (len(templ) == K.TEMPLATE_CONTROLS_PER_DEAL and len(r0) == 1 and not r0[0].facts
              and not leaked and not drafted and action)
        check(sec, f"{st.deal_names[deal]}: {K.TEMPLATE_CONTROLS_PER_DEAL} template controls detected, never answered",
              ok, f"{len(templ)} detected; held as '{r0[0].title if r0 else '-'}'; in other requirements: "
                  f"{leaked or 'none'}; request-text action: {action}")

    bad = [k for k, f in winners(st) if f.status == "verified"
           and (f.winner.applies_to == "generic_platform" or f.winner.tier == 5)]
    gen = [k for k, f in winners(st) if f.winner.applies_to == "generic_platform"]
    check(sec, "A generic product doc never makes a fact verified", not bad,
          f"{len(gen)} facts whose best source is generic, all unverified" if not bad else f"verified: {bad}")

    print("\n## Evidence and time")
    sec = "Evidence and time"
    f = st.facts[K.EXPIRED]
    check(sec, f"{K.EXPIRED} expired as of {K.AS_OF}", st.as_of == K.AS_OF and f.status == "expired",
          f"{f.status}: {f.reason}")
    k, max_age = K.STALE
    f = st.facts[k]
    age = (st.as_of - f.winner.as_of_date).days if f.winner and f.winner.as_of_date else None
    check(sec, f"{k} stale (Feb 2026, older than {max_age} days)", f.status == "stale" and age and age > max_age,
          f"{f.status}: {f.reason}")
    f = st.facts[K.NOT_VERIFIED]
    check(sec, f"{K.NOT_VERIFIED} not verified (only a chat claim; evidence file has no remediation proof)",
          f.status != "verified",
          f"{f.status}, value \"{f.value}\" from {st.sources[f.winner.source_id].title if f.winner else '-'}; "
          f"{f.reason}")
    f = st.facts[K.SOC2]
    cav = [x for x in [f.value or "", f.reason] + f.warnings if "2026" in x]
    check(sec, f"{K.SOC2} verified, with the 2026-audit caveat", f.status == "verified" and bool(cav),
          f"{f.status}: \"{f.value}\"; caveat: {cav[0] if cav else 'none'}")

    print("\n## Root causes")
    sec = "Root causes"
    roots = [b["fact"] for b in st.plan["blockers"]]
    f = st.facts[K.ROOT_BLOCKER]
    b = next((b for b in st.plan["blockers"] if b["fact"] == K.ROOT_BLOCKER), None)
    check(sec, f"{K.ROOT_BLOCKER} (customer-only) is a root blocker",
          b is not None and f.spec["proof"] == "customer" and not f.blocked_by,
          f"holds up {len(b['reqs'])} requirements, e.g. {b['chains'][0] if b['chains'] else ''}" if b
          else f"not a root; roots: {roots[:5]}")
    f = st.facts[K.UNDECIDED]
    check(sec, f"{K.UNDECIDED} undecided", f.status == "undecided", f"{f.status}: {f.reason}")
    dk, sub = K.DPA
    reach, todo = set(), list(st.facts[dk].blocked_by)
    while todo:
        x = todo.pop()
        if x not in reach:
            reach.add(x)
            todo += st.facts[x].blocked_by
    rc = resolve.root_causes(dk, st.facts)
    check(sec, f"{dk} blocked chain reaches {sub}", sub in reach and K.ROOT_BLOCKER in rc,
          f"blocked by {sorted(reach)}; root causes {rc}")
    acts = [a for a in st.plan["actions"] if a.kind == "ask_customer" and K.ROOT_BLOCKER in a.facts]
    check(sec, "A customer-ask action exists for Acme residency", bool(acts),
          f"'{acts[0].title}' → {acts[0].who}, unblocks {acts[0].unblocks}" if acts else "no such action")

    print("\n## Requirements discovery")
    sec = "Requirements discovery"
    elena = [a for a in st.asks.values() if a.deal == "acme" and a.origin == "customer"
             and (a.who or "").lower().startswith("elena")]
    for topic, words, facts in K.ELENA_ASKS:
        a = next((a for a in elena if all(w in a.text.lower() for w in words[:1])
                  and any(w in a.text.lower() for w in words)), None)
        r = req_with_ask(st, "acme", a.id) if a else None
        ok = bool(r) and r.status not in ("umbrella",) and bool(set(r.facts) & set(facts))
        check(sec, f"Elena's ask '{topic}' lands in a requirement", ok,
              f"\"{a.text}\" → {r.id} {r.title} {r.facts}" if r else
              (f"\"{a.text}\" in no requirement" if a else "ask not extracted"))
    ins = [a for a in st.instructions["acme"] if (a.who or "").lower().startswith("marcus")]
    used = {i for r in st.reqs["acme"] for i in r.ask_ids}
    found = [next((a for a in ins if all(w in a.text.lower() for w in words)), None)
             for words in K.MARCUS_INSTRUCTIONS]
    check(sec, "Marcus's instructions recognised as instructions, not requirements",
          all(found) and not any(a.id in used for a in found if a),
          "; ".join(f"\"{a.text}\"" if a else "missing" for a in found))
    tl = st.timeline["northstar"]
    launch = [t for t in tl if (t.date_text or "").startswith(K.NORTHSTAR_LAUNCH)]
    cad = [t for t in tl if K.NORTHSTAR_CADENCE in t.text.lower()]
    check(sec, "Northstar timeline has the Nov 16 launch and the Wednesday cadence", bool(launch and cad),
          f"{[t.text for t in tl]}")
    rm = [r for r in st.reqs["northstar"] if "records_management" in r.facts]
    check(sec, "Records Management captured for Northstar", bool(rm),
          f"{rm[0].id} {rm[0].title} ({rm[0].status})" if rm else "no requirement links records_management")


def round2_checks(st):
    print("\n## Round 2 matching")
    sec = "Round 2 matching"
    recs = {x["qid"]: x for x in st.rounds.get("acme", [])}
    for qid, exp in K.ROUND2.items():
        x = recs.get(qid)
        if not x:
            check(sec, f"{qid}", False, "no match record")
            continue
        r = req_by_id(st, x["requirement_id"])
        probs = []
        if x["relation"] not in exp["relations"]:
            probs.append(f"relation {x['relation']} not in {exp['relations']}")
        if exp.get("has_ask") and (not r or exp["has_ask"] not in r.ask_ids):
            probs.append(f"not matched to the requirement holding {exp['has_ask']}")
        if exp.get("not_ask") and r and exp["not_ask"] in r.ask_ids:
            probs.append(f"wrongly matched to the requirement holding {exp['not_ask']}")
        if exp.get("facts_any") and (not r or not set(r.facts) & set(exp["facts_any"])):
            probs.append(f"requirement facts {r.facts if r else []} lack {exp['facts_any']}")
        if exp.get("added") and not any(w in x["added_part"].lower() for w in exp["added"]):
            probs.append(f"added part \"{x['added_part']}\" does not mention {exp['added']}")
        if exp.get("reused") and not x.get("reused_number"):
            probs.append("reused question number not flagged")
        if exp.get("not_predicted") and r and r.status == "predicted":
            probs.append("requirement still 'predicted'")
        desc = f"{x['relation']} → {x['requirement_id']} {r.title if r else ''}"
        if x.get("reused_number"):
            desc += " · reused number flagged"
        check(sec, f"{qid}: {exp['why']}", not probs, desc if not probs else f"{desc} · " + "; ".join(probs))


def story_checks(base, ledger_path: Path, inbox: Inbox):
    print("\n## Story (six inputs in order)")
    sec = "Story"
    soc2_req = next(r for r in base.reqs["acme"] if "soc2" in r.facts)
    f = base.facts[K.SOC2]
    import json
    ledger_path.write_text(json.dumps({
        "type": "sent", "deal": "acme", "req": soc2_req.id, "version": "eval",
        "text": "SOC 2 Type II 2025 final; 2026 audit in progress.",
        "facts": {K.SOC2: {"value": f.value, "status": f.status}}, "date": str(K.AS_OF)}) + "\n")
    after = {}
    for name in K.STORY_INPUTS:
        inbox.add(name)
        after[name] = build(f"story + {name}", ledger_path)

    s = after["02_elena_residency.json"]
    f = s.facts["acme.residency_requirement"]
    check(sec, "After Elena's email: Acme residency verified", f.status == "verified",
          f"{f.status}: \"{f.value}\" ({f.reason})")

    s = after["03_anthony_config.json"]
    a, fb = s.facts["acme.retention_app_logs"], s.facts["acme.fallback_routing"]
    check(sec, "After Anthony's config: Acme app logs 14 days verified, fallback no longer undecided",
          a.status == "verified" and "14" in (a.value or "") and fb.status != "undecided",
          f"app logs {a.status} \"{a.value}\"; fallback {fb.status} \"{fb.value}\"")

    s = after["04_anthony_soc2.json"]
    f = s.facts[K.SOC2]
    check(sec, "After Anthony's SOC 2 chat: soc2 conflicting (chat is not evidence)",
          f.status == "conflicting", f"{f.status}: {f.reason}")
    u = [x for x in s.plan["updates"] if x["req"] == soc2_req.id]
    check(sec, "After Anthony's SOC 2 chat: sent SOC 2 answer at risk, no update owed yet",
          bool(u) and u[0]["kind"] == "at_risk", f"{u[0]['kind'] if u else 'nothing flagged'} on {soc2_req.id}")

    s = after["05_soc2_report.json"]
    f = s.facts[K.SOC2]
    check(sec, "After the Vanta report: soc2 verified as the 2026 report",
          f.status == "verified" and "2026" in (f.value or ""), f"{f.status}: \"{f.value}\"")
    u = [x for x in s.plan["updates"] if x["req"] == soc2_req.id]
    act = any(a.kind == "send_update" for a in s.plan["actions"])
    check(sec, "After the Vanta report: update owed to Acme for the sent SOC 2 answer",
          bool(u) and u[0]["kind"] == "update_owed" and act,
          f"{u[0]['kind'] if u else 'nothing'} on {soc2_req.id}; send_update action: {act}")

    s = after["06_insurance_renewal.json"]
    f = s.facts[K.EXPIRED]
    check(sec, "After the insurance renewal: insurance verified", f.status == "verified",
          f"{f.status}: \"{f.value}\" ({f.reason})")


# ---------- report ----------

def write_results() -> tuple[int, int]:
    n_ok = sum(c["ok"] for c in checks)
    lines = [f"# Eval results", "",
             f"Run {datetime.now():%Y-%m-%d %H:%M} · as-of {K.AS_OF} · model {llm.MODEL} ({llm.REASONING})", "",
             f"**Score: {n_ok} / {len(checks)} checks pass**", "", "Builds:", ""]
    lines += [f"- {b}" for b in builds]
    sec = None
    for c in checks:
        if c["section"] != sec:
            sec = c["section"]
            lines += ["", f"## {sec}", "", "| | Check | Why |", "|---|---|---|"]
        reason = c["reason"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {'PASS' if c['ok'] else '**FAIL**'} | {c['name']} | {reason} |")
    RESULTS.write_text("\n".join(lines) + "\n")
    return n_ok, len(checks)


def main() -> int:
    existing = sorted(INBOX.glob("*.json"))
    if existing:
        print(f"state/inbox/ already has inputs ({[p.name for p in existing]}); the base checks assume "
              f"none. Clear them (Reset demo in the app) and rerun.")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="pn-eval-"))
    inbox = Inbox()
    try:
        empty = tmp / "empty-ledger.jsonl"   # never created: an empty ledger
        base = build("base case", empty)
        base_checks(base)
        try:
            inbox.add(K.ROUND2_INPUT)
            round2_checks(build("base + round 2", empty))
        finally:
            inbox.clear()
        try:
            story_checks(base, tmp / "ledger.jsonl", inbox)
        finally:
            inbox.clear()
    finally:
        inbox.clear()
        shutil.rmtree(tmp, ignore_errors=True)
    n_ok, n = write_results()
    print(f"\nScore: {n_ok} / {n} checks pass · written to {RESULTS.relative_to(ROOT)}")
    return 0 if n_ok == n else 1


if __name__ == "__main__":
    sys.exit(main())
