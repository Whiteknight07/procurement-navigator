"""LLM step 2: merge asks into requirements, and match later questionnaire rounds to them.

Round 1: every ask (questionnaire rows, sentences in customer emails, predicted topics) is grouped
into requirements, each linked to the catalog facts it needs.
Round 2+: each new question is labelled repeat / repeat_with_changes / new against the existing
requirements. Code rejects any requirement or fact the model was not shown, and flags a reused
question number that now asks something different.

Requirement ids come from the requirement's first ask, not from its position in a list, so they do
not shift when a new email or round adds asks (the ledger keys sent answers by requirement id).
"""

from __future__ import annotations

import hashlib

from . import llm
from .llm import BOOL, STR, arr, enum, obj
from .models import Ask, Requirement

VERSION = "req-v3"

MERGE_SYSTEM = """You organise one customer's vendor-review asks into requirements.
A requirement is one distinct thing the customer needs answered or provided. The same need often appears in several places
(a questionnaire row and a sentence in an email): merge those into one requirement.
Do not merge different needs just because they share a topic (retention of app logs and subprocessors are different).

For each requirement give:
- title: short (3-7 words)
- track: Security, Privacy, AI Risk, Resilience, Legal, Procurement, Supplier, Governance or Records
- ask: the need in one clean sentence, keeping every specific the customer asked for
- ask_ids: the ids of every ask it merges (every given ask must appear in exactly one requirement)
- facts: the catalog facts an answer depends on (as few as cover it; empty if none fit)
- reading: how THIS customer reads the ask, from their wording and their instructions (e.g. "Acme separates storage from processing location"); empty string if nothing specific
- predicted_only: true if every merged ask is a predicted topic (from guidance) rather than something the customer asked
- umbrella: true if the ask only names a whole review, form or intake to complete ("complete the supplier profile", "AI governance intake");
  it is satisfied by the specific requirements in that track. Do not merge umbrella asks into specific requirements."""

MATCH_SYSTEM = """A customer sent a new questionnaire round. Match each new question to the existing requirements.
relation:
  repeat              = asks the same thing as an existing requirement, nothing new
  repeat_with_changes = the same underlying need, but with an extra part or a changed scope, period or format;
                        say only what is new or changed in added_part
  new                 = not covered by any existing requirement
Question numbers are NOT reliable: the same number can be reused for a different question. Match on meaning only.
requirement: the matched requirement's label (e.g. "#3"), or "none" for new.
facts: catalog facts needed for the new or added part (empty for a plain repeat).
title: a short title (3-7 words) for a new requirement, else empty string."""


def _catalog_lines(catalog: dict) -> str:
    return "\n".join(f"- {k}: {v['question']}" for k, v in catalog["facts"].items())


def merge_round_one(deal: str, deal_name: str, asks: list[Ask], instructions: list[Ask],
                    catalog: dict, offline=False) -> list[Requirement]:
    keys = list(catalog["facts"])
    ids = [a.id for a in asks]
    schema = obj(requirements=arr(obj(
        title=STR, track=STR, ask=STR, ask_ids=arr(enum(*ids)), facts=arr(enum(*keys)),
        reading=STR, predicted_only=BOOL, umbrella=BOOL)))
    lines = [f"{a.id} | {a.origin}{'/' + a.kind if a.kind != 'requirement' else ''} | "
             f"{a.track or '-'} | {a.who or '-'} | {a.text}" for a in asks]
    user = (f"Customer: {deal_name}\n\nCatalog facts:\n{_catalog_lines(catalog)}\n\n"
            f"Customer instructions on how to answer:\n" +
            ("\n".join(f"- {i.text} ({i.who})" for i in instructions) or "- none") +
            "\n\nAsks (id | origin | track | who | text):\n" + "\n".join(lines))
    out = llm.call_json("merge", VERSION, MERGE_SYSTEM, user, schema, offline=offline)

    by_id = {a.id: a for a in asks}
    reqs, used = [], set()
    for r in out["requirements"]:
        members = [i for i in r["ask_ids"] if i in by_id and i not in used]
        if not members:
            continue
        used.update(members)
        reqs.append(Requirement(
            id="", deal=deal, title=r["title"], track=r["track"], ask=r["ask"], ask_ids=members,
            facts=[k for k in dict.fromkeys(r["facts"]) if k in catalog["facts"]],
            reading=r["reading"],
            status="predicted" if r["predicted_only"] else "umbrella" if r["umbrella"] else "open"))
    for a in asks:  # code guarantees every ask lands somewhere
        if a.id not in used:
            reqs.append(Requirement(id="", deal=deal, title=a.text[:60], track=a.track or "Other",
                                    ask=a.text, ask_ids=[a.id], facts=[]))
    order = {a.id: i for i, a in enumerate(asks)}
    for r in reqs:
        r.ask_ids.sort(key=order.get)
    reqs.sort(key=lambda r: order[r.ask_ids[0]])
    taken: set[str] = set()
    for r in reqs:
        r.id = stable_id(deal, by_id[r.ask_ids[0]], taken)
    return reqs


def stable_id(deal: str, first: Ask, taken: set[str]) -> str:
    """acme:Q-004 for a round 1 row, acme:R2-Q-012 for a later round, acme:E-1a2b for an email ask,
    acme:P-3c4d for a predicted topic (a hash of the quote, so it survives rebuilds)."""
    if first.origin == "questionnaire":
        base = first.qid if (first.round or 1) == 1 else f"R{first.round}-{first.qid}"
    else:
        h = hashlib.sha256(first.quote.strip().lower().encode()).hexdigest()[:4]
        base = f"{'P' if first.origin == 'predicted' else 'E'}-{h}"
    rid, n = f"{deal}:{base}", 2
    while rid in taken:   # two asks with the same quote: keep ids unique
        rid, n = f"{deal}:{base}-{n}", n + 1
    taken.add(rid)
    return rid


def match_round(deal: str, round_no: int, new_asks: list[Ask], reqs: list[Requirement],
                all_asks: dict[str, Ask], catalog: dict, offline=False) -> list[dict]:
    """Return one match record per new question; also updates `reqs` in place."""
    # The model sees neutral labels (#1, #2...), not ids like acme:Q-012, so a reused question
    # number cannot pull it towards the wrong requirement. The no-text bucket is never shown.
    shown = [r for r in reqs if r.status != "missing_text"]
    by_label = {f"#{n}": r for n, r in enumerate(shown, 1)}
    schema = obj(matches=arr(obj(
        ask_id=enum(*[a.id for a in new_asks]),
        relation=enum("repeat", "repeat_with_changes", "new"),
        requirement=enum(*by_label, "none"), added_part=STR,
        facts=arr(enum(*catalog["facts"])), title=STR)))
    existing = []
    for lbl, r in by_label.items():
        qs = "; ".join(f"[{all_asks[i].qid or 'email'}] {all_asks[i].text}" for i in r.ask_ids
                       if i in all_asks)
        existing.append(f"{lbl} | {r.title} | {r.ask} | asked as: {qs}")
    user = (f"Catalog facts:\n{_catalog_lines(catalog)}\n\nExisting requirements:\n" +
            "\n".join(existing) + f"\n\nRound {round_no} questions (ask id | number | text):\n" +
            "\n".join(f"{a.id} | {a.qid} | {a.text}" for a in new_asks))
    out = llm.call_json("match", VERSION, MATCH_SYSTEM, user, schema, offline=offline)

    earlier_qids = {all_asks[i].qid: all_asks[i] for r in reqs for i in r.ask_ids
                    if i in all_asks and all_asks[i].qid}
    taken = {r.id for r in reqs}
    by_ask = {a.id: a for a in new_asks}
    results = []
    for m in out["matches"]:
        a = by_ask.pop(m["ask_id"], None)
        if a is None:   # the model listed the same question twice
            continue
        r = by_label.get(m["requirement"])
        rel = m["relation"] if r else "new"   # "repeat" of nothing, or of an unknown label, is new
        facts = [k for k in m["facts"] if k in catalog["facts"]]
        if rel == "new":
            r = _new_requirement(deal, a, m["title"], facts, reqs, taken)
        else:
            if r.status == "predicted":   # we expected this topic; now they have asked
                r.status = "open"
            r.ask_ids.append(a.id)
            r.facts += [k for k in facts if k not in r.facts]
        results.append(_record(r, a, rel, m["added_part"], earlier_qids.get(a.qid)))
    for a in by_ask.values():   # anything the model skipped becomes its own requirement
        r = _new_requirement(deal, a, "", [], reqs, taken)
        results.append(_record(r, a, "new", "", earlier_qids.get(a.qid)))
    return results


def _new_requirement(deal, a: Ask, title: str, facts: list[str], reqs: list, taken: set) -> Requirement:
    r = Requirement(id=stable_id(deal, a, taken), deal=deal, title=title or a.text[:60],
                    track=a.track or "Other", ask=a.text, ask_ids=[a.id], facts=facts)
    reqs.append(r)
    return r


def _record(r: Requirement, a: Ask, rel: str, added: str, prev: Ask | None) -> dict:
    """One round-diff line. A question number seen earlier on a different requirement is flagged."""
    reused = None
    if prev and prev.id not in r.ask_ids:
        reused = f"Number {a.qid} was used in an earlier round for a different question: \"{prev.text}\""
    entry = {"round": a.round, "qid": a.qid, "relation": rel, "ask_id": a.id,
             "added_part": added, "reused_number": reused}
    r.history.append(entry)
    return {**entry, "requirement_id": r.id, "question": a.text}
