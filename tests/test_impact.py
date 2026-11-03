"""Impact ranking and the pending-leak check, on a small hand-built state (no network, no cache)."""

from __future__ import annotations

from datetime import date

from pn import drafting, planner, resolve
from pn.engine import State
from pn.models import Fact, Requirement


def spec(proof="owner_confirmation", level="deal", depends_on=(), question="What is it?"):
    return {"proof": proof, "owner": "Engineering", "track": "Security", "level": level,
            "question": question, "depends_on": list(depends_on)}


def make_state() -> State:
    # residency (customer) <- routing; retention alone holds two answers, one shared with soc2
    specs = {"residency": spec("customer", question="Where must data stay?"),
             "routing": spec(depends_on=["residency"]),
             "retention": spec(question="How long are app logs retained?"),
             "soc2": spec("document", level="company")}
    st = State(as_of=date(2026, 11, 3), catalog={"facts": specs},
               policy={"team_owner": {"Engineering": "Priya"}, "reply_days": {"default": 2}},
               deal_names={"acme": "Acme"})
    status = {"acme.residency": ("unknown", None), "acme.routing": ("unconfirmed", "EU only"),
              "acme.retention": ("unconfirmed", "14 days"), "company.soc2": ("verified", "Type II")}
    for k, (s, v) in status.items():
        name = k.split(".")[1]
        st.facts[k] = Fact(key=k, name=name, deal=None if k.startswith("company.") else "acme",
                           spec=specs[name], status=s, value=v)
    resolve.set_blockers(st.facts)
    req = lambda i, facts: Requirement(id=f"acme:{i}", deal="acme", title=i, track="Security",
                                       ask=i, ask_ids=[], facts=facts)
    st.reqs = {"acme": [req("Q-1", ["routing"]), req("Q-2", ["retention"]),
                        req("Q-3", ["retention", "soc2"])]}
    return st


def test_impact_counts_and_ranking():
    st = make_state()
    p = planner.plan(st)
    by_fact = {a.facts[0]: a for a in p["actions"] if a.facts}
    assert set(by_fact) == {"acme.residency", "acme.retention"}   # routing waits on residency
    assert p["impact"][by_fact["acme.retention"].title] == {"ready": 2, "unblocked": 0}
    assert p["impact"][by_fact["acme.residency"].title] == {"ready": 0, "unblocked": 1}
    titles = [a.title for a in p["actions"]]
    assert titles.index(by_fact["acme.retention"].title) < titles.index(by_fact["acme.residency"].title)


def test_simulation_leaves_real_facts_alone():
    st = make_state()
    planner.plan(st)
    assert st.facts["acme.residency"].status == "unknown"
    assert st.facts["acme.routing"].blocked_by == ["acme.residency"]
    assert [r.status for r in st.reqs["acme"]] == ["blocked", "open", "open"]


def test_pending_sentence_may_not_leak_best_guess():
    st = make_state()
    planner.plan(st)
    req = st.reqs["acme"][1]
    out = lambda text: {"sentences": [{"kind": "pending", "text": text, "claim_ids": [],
                                       "pending_fact": "acme.retention"}]}
    clean = drafting.check(req, out("Pending: Engineering is confirming the app log retention period."), st)
    assert clean["can_approve"]
    leak = drafting.check(req, out("Pending: Engineering is confirming logs are kept for 14 days."), st)
    assert not leak["can_approve"] and "best guess" in leak["errors"][0]
