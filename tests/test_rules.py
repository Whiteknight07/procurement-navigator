"""Deterministic tests for the fixed rules. No network: every object is built by hand."""

from __future__ import annotations

from datetime import date

import pytest

from pn import ledger, planner, resolve
from pn.models import Claim, Fact, Source
from pn.sources import load_yaml, parse_questionnaire

AS_OF = date(2026, 11, 3)
POLICY = load_yaml("policy.yaml")


def spec(proof="owner_confirmation", owner="Engineering", **kw):
    return {"level": "deal", "track": "Privacy", "owner": owner, "proof": proof,
            "question": "q?", **kw}


def src(system="slack", sid="s1", deal="acme", d=None):
    return Source(id=sid, deal=deal, system=system, title=f"{deal}/{sid}", text="", date=d)


_n = 0


def claim(value="14 days", *, system="slack", speaker=None, assertion="stated",
          applies_to="this_deal", as_of_date=None, valid_until=None, sp=None, side="supplier",
          src_date=None):
    """A claim with its tier already assigned, the way the engine does it."""
    global _n
    _n += 1
    c = Claim(id=f"C-{_n}", source_id=f"s{_n}", deal="acme", fact="x", value=value, quote=value,
              speaker=speaker, applies_to=applies_to, assertion=assertion,
              as_of_date=as_of_date, valid_until=valid_until)
    c.speaker_side = side
    resolve.assign_tier(c, src(system, f"s{_n}", d=src_date), sp or spec(), POLICY)
    return c


def resolve_one(claims, sp=None, labels=None):
    f = Fact(key="acme.x", name="x", deal="acme", spec=sp or spec())
    return resolve.resolve_fact(f, claims, AS_OF, POLICY, labels or {}, [])


# ---------- trust tiers ----------

def test_tiers():
    t = POLICY["tiers"]
    assert claim(system="vanta", assertion="evidence").tier == t["evidence"]
    assert claim(speaker="Anthony").tier == t["owner_confirmation"]
    assert claim(speaker="Anthony", assertion="planned").tier == t["internal_chat"]
    assert claim(speaker="Parsa").tier == t["internal_chat"]
    assert claim(system="drive").tier == t["deal_document"]
    assert claim(system="drive", applies_to="generic_platform").tier == t["generic_document"]
    assert claim(applies_to="other_customer", system="vanta", assertion="evidence").tier == t["other_customer"]
    assert claim(assertion="questionnaire_draft", system="questionnaire").tier == t["questionnaire_draft"]
    cust = spec(proof="customer", owner="Customer")
    assert claim(system="email", side="customer", sp=cust).tier == t["customer_statement"]
    assert claim(system="slack", side="supplier", sp=cust).tier == t["internal_chat"]


# ---------- resolving one fact ----------

def test_other_customer_never_wins():
    other = claim("30 days", system="vanta", assertion="evidence", applies_to="other_customer")
    f = resolve_one([other])
    assert f.status == "unknown" and f.winner is None and f.hints == [other]
    plan_doc = claim("14 days", system="drive")
    f = resolve_one([other, plan_doc])
    assert f.winner is plan_doc and f.value == "14 days"


def test_questionnaire_draft_never_wins():
    d = claim("30 days", system="questionnaire", assertion="questionnaire_draft")
    f = resolve_one([d])
    assert f.status == "unknown" and f.winner is None and "questionnaire draft" in f.reason
    generic = claim("14 days", system="drive", applies_to="generic_platform")
    f = resolve_one([d, generic])
    assert f.winner is generic and f.status == "needs_confirmation"


def test_plan_doc_loses_to_owner_confirmation():
    plan_doc = claim("30 days", system="drive", as_of_date=date(2026, 10, 1))
    owner = claim("14 days", speaker="Anthony", as_of_date=date(2026, 9, 1))
    f = resolve_one([plan_doc], )
    assert f.status == "needs_confirmation"
    f = resolve_one([plan_doc, owner])
    assert f.winner is owner and f.status == "verified"


def test_undecided_beats_a_plan():
    plan_doc = claim("fallback off", system="drive")
    open_q = claim("probably off", speaker="Parsa", assertion="undecided")
    f = resolve_one([plan_doc, open_q])
    assert f.status == "undecided" and "a plan does not settle" in f.reason
    # ...but the owner stating it as settled does settle it
    owner = claim("fallback off", speaker="Anthony")
    assert resolve_one([plan_doc, open_q, owner]).status == "verified"


def test_document_proof_needs_evidence():
    sp = spec(proof="document")
    owner = claim("SOC 2 2025 final", speaker="Anthony", sp=sp)
    f = resolve_one([owner], sp)
    assert f.status == "needs_proof" and f.value == "SOC 2 2025 final"
    ev = claim("SOC 2 2025 final", system="vanta", assertion="evidence", sp=sp,
               as_of_date=date(2025, 12, 31))
    assert resolve_one([owner, ev], sp, {(ev.id, owner.id): "agree"}).status == "verified"


def test_stale_by_max_age():
    sp = spec(proof="document", max_age_days=183)
    old = claim("pen test Feb 2026", system="vanta", assertion="evidence", sp=sp,
                as_of_date=date(2026, 2, 1))
    f = resolve_one([old], sp)
    assert f.status == "stale" and "policy allows 183" in f.reason
    fresh = claim("pen test Oct 2026", system="vanta", assertion="evidence", sp=sp,
                  as_of_date=date(2026, 10, 1))
    assert resolve_one([fresh], sp).status == "verified"


def test_undated_proof_with_max_age_is_flagged():
    sp = spec(proof="document", max_age_days=183)
    undated = claim("pen test done", system="vanta", assertion="evidence", sp=sp)
    f = resolve_one([undated], sp)
    assert f.status == "verified" and any("No date" in w for w in f.warnings)


def test_expired_by_valid_until_and_expiry_warning():
    sp = spec(proof="document")
    cert = claim("CAD 5M", system="vanta", assertion="evidence", sp=sp, valid_until=date(2026, 10, 1))
    f = resolve_one([cert], sp)
    assert f.status == "expired" and "Oct 01, 2026" in f.reason
    soon = claim("CAD 5M", system="vanta", assertion="evidence", sp=sp, valid_until=date(2026, 12, 1))
    f = resolve_one([soon], sp)
    assert f.status == "verified" and any("start renewal" in w for w in f.warnings)
    # the last valid day is still valid
    today = claim("CAD 5M", system="vanta", assertion="evidence", sp=sp, valid_until=AS_OF)
    assert resolve_one([today], sp).status == "verified"


def test_newer_evidence_supersedes_older():
    sp = spec(proof="document")
    old = claim("SOC 2 2025", system="vanta", assertion="evidence", sp=sp, as_of_date=date(2025, 12, 31))
    new = claim("SOC 2 2026", system="vanta", assertion="evidence", sp=sp, as_of_date=date(2026, 9, 30))
    labels = {(new.id, old.id): "conflicts"}
    f = resolve_one([old, new], sp, labels)
    assert f.winner is new and f.status == "verified"
    # a renewed certificate replaces the expired one, whatever the dates say
    gone = claim("cert A", system="vanta", assertion="evidence", sp=sp, valid_until=date(2026, 10, 1))
    renewed = claim("cert B", system="vanta", assertion="evidence", sp=sp, valid_until=date(2027, 10, 1))
    assert resolve.superseded(gone, renewed, AS_OF)


def test_real_conflict_between_owner_and_evidence():
    sp = spec(proof="document")
    ev = claim("SOC 2 2025 final", system="vanta", assertion="evidence", sp=sp,
               as_of_date=date(2025, 12, 31))
    chat = claim("SOC 2 2026 final", speaker="Anthony", sp=sp, src_date=date(2026, 11, 4))
    f = resolve_one([ev, chat], sp, {(ev.id, chat.id): "conflicts"})
    assert f.winner is ev and f.status == "conflicting"
    # the same pair labelled as agreeing is not a conflict
    assert resolve_one([ev, chat], sp, {(ev.id, chat.id): "agree"}).status == "verified"


def test_customer_proof_needs_the_customer():
    sp = spec(proof="customer", owner="Customer")
    guess = claim("in-country", speaker="Jai", sp=sp)
    assert resolve_one([guess], sp).status == "unknown"
    said = claim("in-country", system="email", side="customer", speaker="Elena", sp=sp)
    assert resolve_one([guess, said], sp).status == "verified"


def test_pairs_needed_matches_resolver_order():
    owner = claim("14 days", speaker="Anthony")
    plan_doc = claim("30 days", system="drive")
    d = claim("30 days", system="questionnaire", assertion="questionnaire_draft")
    pairs = {(a.id, b.id) for a, b in resolve.pairs_needed([plan_doc, d, owner])}
    assert (owner.id, plan_doc.id) in pairs and (owner.id, d.id) in pairs


# ---------- dependencies ----------

def facts_graph(deps: dict[str, list[str]], unverified: set[str], company=()):
    facts = {}
    for name, ds in deps.items():
        key = f"company.{name}" if name in company else f"acme.{name}"
        facts[key] = Fact(key=key, name=name, deal=None if name in company else "acme",
                          spec={"depends_on": ds}, status="unknown" if name in unverified else "verified")
    resolve.set_blockers(facts)
    return facts


def test_blockers_roots_and_chain():
    deps = {"residency": [], "fallback": ["residency"], "providers": ["fallback"],
            "subprocessors": ["providers"], "dpa": ["subprocessors", "storage"], "storage": []}
    facts = facts_graph(deps, {"residency", "fallback", "providers", "subprocessors", "dpa"})
    assert facts["acme.fallback"].blocked_by == ["acme.residency"]
    assert facts["acme.dpa"].blocked_by == ["acme.subprocessors"]   # storage is verified
    assert resolve.root_causes("acme.dpa", facts) == ["acme.residency"]
    assert resolve.chain("acme.dpa", facts) == ["acme.dpa", "acme.subprocessors", "acme.providers",
                                                "acme.fallback", "acme.residency"]
    resolve.set_blockers(facts)   # idempotent
    assert facts["acme.fallback"].blocked_by == ["acme.residency"]


def test_diamond_is_not_a_cycle():
    # cross_border depends on inference and subprocessors, which both depend on providers
    deps = {"providers": [], "inference": ["providers"], "subprocessors": ["providers"],
            "cross_border": ["inference", "subprocessors"]}
    facts = facts_graph(deps, set(deps))
    assert resolve.root_causes("acme.cross_border", facts) == ["acme.providers"]


def test_cycle_still_has_a_root_cause():
    facts = facts_graph({"a": ["b"], "b": ["a"], "c": ["a"]}, {"a", "b", "c"})
    assert resolve.root_causes("acme.a", facts)          # not empty
    assert resolve.root_causes("acme.c", facts)
    assert resolve.chain("acme.a", facts) == ["acme.a", "acme.b"]


def test_deal_fact_can_depend_on_company_fact():
    facts = facts_graph({"dpa": ["soc2"], "soc2": []}, {"dpa", "soc2"}, company={"soc2"})
    assert facts["acme.dpa"].blocked_by == ["company.soc2"]
    assert planner.closure(["acme.dpa"], facts) == {"acme.dpa", "company.soc2"}


def test_closure_ignores_unknown_keys_and_cycles():
    facts = facts_graph({"a": ["b", "missing"], "b": ["a"]}, {"a", "b"})
    assert planner.closure(["acme.a", "acme.nope"], facts) == {"acme.a", "acme.b"}


# ---------- planner helpers ----------

def test_business_days():
    mon, fri = date(2026, 11, 2), date(2026, 11, 6)
    assert planner.business_days(mon, fri) == 4
    assert planner.business_days(fri, date(2026, 11, 9)) == 1    # weekend skipped
    assert planner.business_days(fri, fri) == 0
    assert planner.business_days(fri, mon) == 0                  # backwards: no time passed


# ---------- questionnaire parsing ----------

def test_template_rows_are_detected():
    rows = ["id,section,question,draft_answer,status,note",
            "Q-001,Privacy,Where is data stored?,AWS,OPEN,",
            "Q-002,Security,Describe encryption at rest (AES-256).,,OPEN,"]
    rows += [f"Q-0{n},Procurement,Control {n}: provide the customer-specific answer.,,OPEN,"
             for n in range(10, 14)]
    rows += ["Q-099,Security,,,,",          # no question text: not an ask
             "Q-100,Short row"]             # missing cells
    q = Source(id="acme-q1-x", deal="acme", system="questionnaire", title="t",
               text="\n".join(rows), round=1)
    asks, drafts = parse_questionnaire(q)
    assert [a.qid for a in asks if a.template] == ["Q-010", "Q-011", "Q-012", "Q-013"]
    assert [a.qid for a in asks if not a.template] == ["Q-001", "Q-002"]
    assert asks[0].id == "acme:R1:Q-001"
    assert drafts == [{"ask_id": "acme:R1:Q-001", "qid": "Q-001", "question": "Where is data stored?",
                       "draft_answer": "AWS", "note": ""}]


def test_two_similar_rows_are_not_a_template():
    text = ("id,section,question\nQ-1,S,Describe test 1 results.\nQ-2,S,Describe test 2 results.\n")
    asks, _ = parse_questionnaire(Source(id="q", deal="acme", system="questionnaire", title="t",
                                         text=text, round=1))
    assert not any(a.template for a in asks)


# ---------- ledger ----------

def test_ledger_round_trip(tmp_path):
    p = tmp_path / "sub" / "ledger.jsonl"
    assert ledger.read(p) == []
    e1 = {"type": "sent", "deal": "acme", "req": "acme:R-07",
          "facts": {"company.soc2": {"value": "SOC 2 2025", "status": "verified"}}, "date": "2026-11-03"}
    e2 = {"type": "asked", "who": "Anthony", "facts": ["acme.storage_location"], "date": "2026-11-03"}
    ledger.append(e1, p)
    ledger.append(e2, p)
    assert ledger.read(p) == [e1, e2]
    ledger.reset(p)
    assert ledger.read(p) == []
    ledger.reset(p)   # resetting twice is fine


@pytest.mark.parametrize("key,expected", [("company.pen_test", "pen test"),
                                          ("acme.retention_app_logs", "Acme retention app logs")])
def test_label(key, expected):
    assert planner.label(key, {}) == expected


# ---------- planner.plan on a hand-built state ----------

def _state(catalog_facts, statuses, reqs, ledger_entries=()):
    from types import SimpleNamespace
    from pn.models import Requirement
    facts = {}
    for name, sp in catalog_facts.items():
        key = f"company.{name}" if sp["level"] == "company" else f"acme.{name}"
        facts[key] = Fact(key=key, name=name, deal=None if sp["level"] == "company" else "acme",
                          spec=sp, status=statuses.get(name, "verified"),
                          value=f"{name} value", reason="r")
    resolve.set_blockers(facts)
    return SimpleNamespace(
        facts=facts, catalog={"facts": catalog_facts}, policy=POLICY, as_of=AS_OF,
        reqs={"acme": [Requirement(id=f"acme:R-{i:02d}", deal="acme", title=f"R{i}", track="Privacy",
                                   ask="", ask_ids=[], facts=fs) for i, fs in enumerate(reqs, 1)]},
        asks={}, sources={}, ledger=list(ledger_entries), timeline={}, deal_names={"acme": "Acme"})


def test_plan_frontier_waits_and_updates():
    cat = {"residency": spec(proof="customer", owner="Customer"),
           "fallback": spec(depends_on=["residency"]),
           "storage": spec(),
           "soc2": {**spec(proof="document"), "level": "company"},
           "loop_a": spec(depends_on=["loop_b"]), "loop_b": spec(depends_on=["loop_a"]),
           "inference": spec(depends_on=["fallback"]), "subprocessors": spec(depends_on=["fallback"]),
           "cross_border": spec(depends_on=["inference", "subprocessors"])}
    for s_ in cat.values():
        s_.setdefault("level", "deal")
    ledger_entries = [
        {"type": "asked", "who": "Anthony", "facts": ["acme.storage"], "date": "2026-10-27"},
        {"type": "sent", "deal": "acme", "req": "acme:R-03",
         "facts": {"company.soc2": {"value": "old soc2", "status": "verified"}}, "date": "2026-10-20"},
    ]
    st = _state(cat, {"residency": "unknown", "fallback": "needs_confirmation", "storage": "needs_confirmation",
                      "loop_a": "needs_confirmation", "loop_b": "needs_confirmation",
                      "inference": "needs_confirmation", "subprocessors": "needs_confirmation",
                      "cross_border": "needs_confirmation"},
                [["fallback"], ["storage"], ["soc2"], ["loop_a"], ["cross_border"]], ledger_entries)
    p = planner.plan(st)
    by_fact = {k: a for a in p["actions"] for k in a.facts}
    assert "acme.residency" in by_fact                                      # frontier only
    assert not {"acme.fallback", "acme.inference", "acme.subprocessors", "acme.cross_border"} & set(by_fact)
    assert by_fact["acme.residency"].kind == "ask_customer"
    assert {"acme.loop_a", "acme.loop_b"} <= set(by_fact)                   # a cycle stays actionable
    assert st.reqs["acme"][0].status == "blocked" and st.reqs["acme"][1].status == "open"
    assert st.reqs["acme"][2].status == "ready"
    blocker = next(b for b in p["blockers"] if b["fact"] == "acme.residency")
    assert blocker["reqs"] == ["acme:R-01", "acme:R-05"]
    # Anthony was asked 5 business days ago and usually takes 3
    assert p["waits"][0]["overdue"] and by_fact["acme.storage"].overdue
    # soc2 changed value since we sent it and is verified: an update is owed, not a fresh approval
    assert p["updates"][0]["kind"] == "update_owed"
    assert "acme:R-03" not in {r.id for r in p["ready"]}
