"""Fixed rules: turn facts + requirements + the ledger into the Monday briefing.

Nothing here is typed in by a person or decided by a model. Every action comes from a gap:
a fact that is not verified, a requirement with no question text, an answer that is ready, or a
sent answer whose facts have changed since it was sent.

Only the *frontier* gets actions: a fact blocked by another unsettled fact is listed under its
root cause instead, so people are never asked something that depends on an open question.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from datetime import date, timedelta

from .models import Action, Fact
from .resolve import chain, dep_keys, root_causes, set_blockers, tokens

VERB = {
    "confirm": "Confirm", "get_evidence": "Get evidence for", "settle": "Settle conflict on",
    "decide": "Decide", "renew": "Renew", "ask_customer": "Ask the customer about",
}
BY_PROOF = {"document": "get_evidence", "owner_confirmation": "confirm", "customer": "ask_customer"}
LIVE = lambda r: r.status not in ("missing_text", "predicted", "umbrella")


def action_kind(f: Fact) -> str:
    """What has to happen to settle a fact. The catalog's proof type decides, except where the
    status says something else is needed first."""
    proof = f.spec["proof"]
    if proof == "customer":
        return "ask_customer"          # only the customer can settle it, whatever the status
    if f.status == "undecided":
        return "decide"
    if f.status == "out_of_date":
        return "renew"
    if f.status == "conflicting":     # a conflict on a document fact is settled by evidence
        return "get_evidence" if proof == "document" else "settle"
    return BY_PROOF[proof]


def fact_key(deal: str, name: str, catalog: dict) -> str:
    return f"company.{name}" if catalog["facts"][name]["level"] == "company" else f"{deal}.{name}"


def closure(keys: list[str], facts: dict[str, Fact]) -> set[str]:
    out, todo = set(), list(keys)
    while todo:
        k = todo.pop()
        if k in out or k not in facts:
            continue
        out.add(k)
        todo += dep_keys(k, facts)
    return out


def business_days(a: date, b: date) -> int:
    days, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def plan(state) -> dict:
    facts: dict[str, Fact] = state.facts
    catalog, policy, as_of = state.catalog, state.policy, state.as_of
    team_owner, reply = policy["team_owner"], policy["reply_days"]
    live = [r for rs in state.reqs.values() for r in rs if LIVE(r)]

    # requirement status
    for rs in state.reqs.values():
        for r in rs:
            r.gaps, status = req_status(r, facts, catalog)
            if LIVE(r):
                r.status = status

    # which requirements each fact holds up
    holds: dict[str, list[str]] = defaultdict(list)
    for r in live:
        for k in closure([fact_key(r.deal, n, catalog) for n in r.facts], facts):
            if facts[k].status != "verified":
                holds[k].append(r.id)

    def customer_contact(deal: str, track: str, f: Fact) -> str:
        for c in f.claims:   # whoever on the customer side raised it decides it
            if c.speaker_side == "customer" and c.speaker:
                return c.speaker
        for a in state.asks.values():
            if a.deal == deal and a.origin == "customer" and a.who and a.track == track:
                return a.who
        return "Customer Procurement"

    # actions on the frontier: the root causes of everything held up. For an acyclic catalog this
    # is "held facts that are not blocked"; it also keeps facts in a depends_on cycle actionable.
    frontier = {r for k in holds for r in root_causes(k, facts)}
    actions: dict[tuple, Action] = {}
    for k, f in facts.items():
        if f.status == "verified" or not holds.get(k) or k not in frontier:
            continue
        deal = None if k.startswith("company.") else k.split(".")[0]
        deals = sorted({rid.split(":")[0] for rid in holds[k]})
        kind = action_kind(f)
        if kind == "ask_customer":
            who = customer_contact(deal, f.spec["track"], f) if deal else "Customer Procurement"
            rd = reply.get("Customer Procurement", 2)
        else:
            who = team_owner.get(f.spec["owner"], f.spec["owner"])
            rd = reply.get(who, reply["default"])
        hint = f.hints[0] if f.hints else None
        proposed = {"fact": k, "question": f.spec["question"], "status": f.status,
                    "value": f.value, "reason": f.reason,
                    "source": state.sources[f.winner.source_id].title if f.winner else None,
                    "hint": f"\"{hint.value}\" ({hint.tier_reason})" if hint else None}
        a = Action(kind=kind, who=who, title=f"{VERB[kind]}: {label(k)}",
                   detail=f.reason, facts=[k], deals=deals, unblocks=len(set(holds[k])),
                   reply_days=rd, proposed=[proposed])
        actions[(kind, k)] = a

    # missing question text
    for deal, rs in state.reqs.items():
        for r in rs:
            if r.status == "missing_text":
                actions[("request_text", r.id)] = Action(
                    kind="request_text", who="Customer Procurement",
                    title=f"Ask {state.deal_names[deal]} for the real text of {len(r.ask_ids)} controls",
                    detail=r.ask, deals=[deal], unblocks=len(r.ask_ids),
                    reply_days=reply.get("Customer Procurement", 2))

    # waits from the ledger
    waits = []
    for e in state.ledger:
        if e["type"] != "asked":
            continue
        open_facts = [k for k in e["facts"] if k in facts and facts[k].status != "verified"]
        if not open_facts:
            continue
        since = date.fromisoformat(e["date"])
        rd = reply.get(e["who"], reply["default"])
        overdue = business_days(since, as_of) > rd
        waits.append({"who": e["who"], "since": e["date"], "facts": open_facts, "overdue": overdue,
                      "expected_days": rd, "note": e.get("note", "")})
        for a in actions.values():
            if a.who == e["who"] and set(a.facts) & set(open_facts):
                a.waiting_since, a.overdue = e["date"], overdue

    # updates owed: something we sent relied on a fact that has changed since
    updates = []
    sent_latest = {}
    for e in state.ledger:
        if e["type"] == "sent":
            sent_latest[e["req"]] = e
    for rid, e in sent_latest.items():
        changes = []
        for k, snap in e["facts"].items():
            f = facts.get(k)
            if not f:
                continue
            if f.status != snap["status"] or tokens(f.value or "") != tokens(snap["value"] or ""):
                changes.append({"fact": k, "was": snap, "now": {"value": f.value, "status": f.status}})
        if changes:
            # Tell the customer only once the new value is verified; until then it is an
            # internal warning that what we sent may be out of date.
            owed = all(c["now"]["status"] == "verified" for c in changes)
            updates.append({"req": rid, "deal": e["deal"], "sent_on": e["date"], "changes": changes,
                            "kind": "update_owed" if owed else "at_risk"})
            if owed:
                actions[("send_update", rid)] = Action(
                    kind="send_update", who="Jai", title=f"Send an update: {req_title(state, rid)}",
                    detail=f"Sent {e['date']}; {len(changes)} fact(s) changed since and are now verified.",
                    facts=[c["fact"] for c in changes], deals=[e["deal"]], unblocks=1, reply_days=0.5,
                    req=rid)

    # answers ready for Jai
    sent_reqs = set(sent_latest)
    ready = [r for r in live if r.status == "ready" and r.id not in sent_reqs]
    for r in ready:
        actions[("approve", r.id)] = Action(
            kind="approve", who="Jai", title=f"Approve answer: {r.title}",
            detail=f"{state.deal_names[r.deal]} · every fact it needs is verified",
            deals=[r.deal], unblocks=1, reply_days=0.5, req=r.id)

    # rank by how many answers wait on it (whole chain), then by what it unlocks right away;
    # already-asked actions still go last
    impact = {a.title: simulate(a.facts, facts, live, catalog) for a in actions.values() if a.facts}
    none = {"ready": 0, "unblocked": 0}
    ranked = sorted(actions.values(),
                    key=lambda a: (a.waiting_since is not None, -a.unblocks,
                                   -impact.get(a.title, none)["ready"],
                                   -impact.get(a.title, none)["unblocked"],
                                   -a.reply_days, a.title))

    # blockers: root causes and the chains that lead to them
    roots: dict[str, dict] = {}
    for r in live:
        for g in r.gaps:
            for root in root_causes(g, facts):
                d = roots.setdefault(root, {"fact": root, "reqs": set(), "chains": set()})
                d["reqs"].add(r.id)
                if g != root:
                    d["chains"].add(" → ".join(label(x) for x in chain(g, facts)))
    blockers = sorted(({**v, "reqs": sorted(v["reqs"]), "chains": sorted(v["chains"])}
                       for v in roots.values()), key=lambda v: -len(v["reqs"]))

    # delays
    delays = []
    for deal, items in state.timeline.items():
        for t in items:
            due = _d(t.date_text)
            if not due:
                continue
            open_n = sum(1 for r in state.reqs[deal] if r.status in ("open", "blocked", "unmapped"))
            if due < as_of:
                delays.append({"deal": deal, "kind": "deadline_passed",
                               "text": f"{t.text} (target {due:%b %d}) has passed; {open_n} requirements still open."})
            elif (due - as_of).days <= 21:
                delays.append({"deal": deal, "kind": "deadline_close",
                               "text": f"{t.text}: {(due - as_of).days} days left; {open_n} requirements open."})
            for k, f in facts.items():
                if k.startswith("company.") and f.winner and f.winner.valid_until and \
                        as_of <= f.winner.valid_until < due:
                    delays.append({"deal": deal, "kind": "expires_before_target",
                                   "text": f"{label(k)} expires {f.winner.valid_until:%b %d}, before {t.text}."})
    for k, f in facts.items():
        if f.status == "out_of_date" and holds.get(k):
            delays.append({"deal": ", ".join(sorted({x.split(':')[0] for x in holds[k]})),
                           "kind": "out_of_date", "text": f"{label(k)}: {f.reason}"})
    for w in waits:
        if w["overdue"]:
            delays.append({"deal": "", "kind": "overdue_wait",
                           "text": f"Waiting on {w['who']} since {w['since']} (usually {w['expected_days']} days)."})

    batches = defaultdict(list)
    for a in ranked:
        if a.kind not in ("approve", "send_update") and not a.waiting_since:
            batches[a.who].append(a)

    return {"actions": ranked, "waits": waits, "updates": updates, "blockers": blockers,
            "delays": delays, "ready": ready, "batches": dict(batches), "impact": impact}


def req_status(r, facts: dict[str, Fact], catalog: dict) -> tuple[list[str], str]:
    """(gaps, status) of one requirement from the current facts."""
    keys = [fact_key(r.deal, n, catalog) for n in r.facts]
    gaps = [k for k in keys if facts[k].status != "verified"]
    if not keys:
        return gaps, "unmapped"
    if not gaps:
        return gaps, "ready"
    if any(facts[k].blocked_by for k in gaps):
        return gaps, "blocked"
    return gaps, "open"


def simulate(keys: list[str], facts: dict[str, Fact], live: list, catalog: dict) -> dict:
    """If these facts were verified tomorrow: how many requirements would become ready, and how
    many blocked facts would no longer be blocked. Works on copies; the real facts are untouched."""
    sim = {k: copy.copy(f) for k, f in facts.items()}
    for k in keys:
        if sim[k].status != "verified":
            sim[k].status, sim[k].value = "verified", sim[k].value or "(confirmed)"
    set_blockers(sim)
    ready = sum(1 for r in live if r.status != "ready" and req_status(r, sim, catalog)[1] == "ready")
    unblocked = sum(1 for k, f in facts.items()
                    if f.status != "verified" and k not in keys and f.blocked_by and not sim[k].blocked_by)
    return {"ready": ready, "unblocked": unblocked}


def _d(s):
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def label(key: str) -> str:
    scope, name = key.split(".", 1)
    pretty = name.replace("_", " ")
    return pretty if scope == "company" else f"{scope.title()} {pretty}"


def req_title(state, rid: str) -> str:
    deal = rid.split(":")[0]
    for r in state.reqs.get(deal, []):
        if r.id == rid:
            return r.title
    return rid


def batch_message(who: str, items: list[Action], state) -> str:
    """One consolidated message per person, every item with our best current answer to confirm."""
    deals = sorted({d for a in items for d in a.deals})
    lines = [f"Hi {who}, {len(items)} item(s) blocking "
             f"{', '.join(state.deal_names[d] for d in deals)}. Each has our current best answer; "
             f"reply yes or correct it."]
    for n, a in enumerate(items, 1):
        p = a.proposed[0] if a.proposed else None
        if not p:
            lines.append(f"{n}. {a.title}. {a.detail}")
            continue
        q = p["question"]
        if p["value"]:
            cur = f"we have \"{p['value']}\" from {p['source']}"
        elif p["hint"]:
            cur = f"nothing usable (a hint says {p['hint']}, which does not count)"
        else:
            cur = "nothing on file"
        ask = {"confirm": "Is this right for the deployed setup?",
               "get_evidence": "Can you put the document in Vanta?",
               "renew": "Can you start the renewal?",
               "decide": "What is the decision?",
               "settle": "Which is right?",
               "ask_customer": "Can you tell us?"}.get(a.kind, "")
        lines.append(f"{n}. [{', '.join(state.deal_names[d] for d in a.deals)}] {q} "
                     f"Today {cur}. {p['reason']} {ask}")
    return "\n".join(lines)
