"""Integration: the whole base build from the warm LLM cache, no network.

Skipped when the cache is cold (offline build raises / records "not cached" errors)."""

from __future__ import annotations

import pytest

from pn import engine, llm, sources


@pytest.fixture(scope="module")
def state(tmp_path_factory, monkeypatch_module):
    # build from the case folders only: an empty inbox and an empty ledger
    monkeypatch_module.setattr(sources, "INBOX", tmp_path_factory.mktemp("inbox"))
    try:
        st = engine.build(offline=True, ledger_path=tmp_path_factory.mktemp("l") / "ledger.jsonl")
    except llm.LLMError as e:
        pytest.skip(f"LLM cache not warm: {e}")
    if any("not cached" in e for e in st.errors):
        pytest.skip(f"LLM cache not warm: {st.errors[:2]}")
    return st


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_company_evidence_freshness(state):
    ins, pen = state.facts["company.insurance"], state.facts["company.pen_test"]
    assert ins.status == pen.status == "out_of_date"
    assert ins.reason.startswith("Expired") and pen.reason.startswith("Stale")
    # the Vanta file shows the findings open; the owner's "fixed" chat cannot be verified by it
    assert state.facts["company.pen_test_remediation"].status != "verified"


def test_residency_is_root_blocker_for_acme(state):
    root = next(b for b in state.plan["blockers"] if b["fact"] == "acme.residency_requirement")
    assert len([r for r in root["reqs"] if r.startswith("acme:")]) >= 3
    # nobody is asked about facts that wait on it
    asked = {k for a in state.plan["actions"] for k in a.facts}
    assert "acme.fallback_routing" not in asked and "acme.model_providers" not in asked


def test_other_customer_logs_never_win(state):
    f = state.facts["acme.retention_app_logs"]
    assert f.winner is not None and f.winner.applies_to != "other_customer"
    assert f.winner.tier < 4 and "30" not in f.value
    assert any(h.value.startswith("30") for h in f.hints)   # Meridian's answer kept as a hint


def test_template_controls_flagged(state):
    for deal in ("acme", "northstar"):
        bucket = [r for r in state.reqs[deal] if r.status == "missing_text"]
        assert len(bucket) == 1 and len(bucket[0].ask_ids) == 37
        assert bucket[0].id == f"{deal}:no-text"
        ids = [r.id for r in state.reqs[deal]]
        assert len(ids) == len(set(ids)) and f"{deal}:Q-004" in ids


def test_plan_shape(state):
    assert set(state.plan) >= {"actions", "waits", "updates", "blockers", "delays", "ready",
                               "batches"}
    ids = [c.id for c in state.claims]
    assert len(ids) == len(set(ids))   # claim ids are unique
