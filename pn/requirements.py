"""LLM step 2: merge asks into requirements, and match later questionnaire rounds to them.

Round 1: every ask (questionnaire rows, sentences in customer emails, predicted topics) is grouped
into requirements, each linked to the catalog facts it needs.
Round 2+: each new question is labelled repeat / repeat_with_addition / changed / new against the
existing requirements. Code rejects any requirement id or fact key the model was not shown, and
flags a reused question number that now asks something different.
"""

from __future__ import annotations

import json

from . import llm
from .llm import BOOL, NSTR, STR, arr, enum, obj
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
  repeat               = asks the same thing as an existing requirement, nothing new
  repeat_with_addition = the same underlying need plus an extra part; put only the extra part in added_part
  changed              = the same topic but the ask itself changed (scope, period, format); describe the change in added_part
  new                  = not covered by any existing requirement
Question numbers are NOT reliable: the same number can be reused for a different question. Match on meaning only.
requirement_id: the matched requirement id, or "none" for new.
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
            status="predicted" if r["predicted_only"] else "umbrella" if r["umbrella"] else "open",
            history=[{"round": by_id[m].round or 0, "qid": by_id[m].qid, "relation": "first seen",
                      "ask_id": m, "added_part": ""} for m in members]))
    for a in asks:  # code guarantees every ask lands somewhere
        if a.id not in used:
            reqs.append(Requirement(id="", deal=deal, title=a.text[:60], track=a.track or "Other",
                                    ask=a.text, ask_ids=[a.id], facts=[],
                                    history=[{"round": a.round or 0, "qid": a.qid,
                                              "relation": "first seen", "ask_id": a.id,
                                              "added_part": ""}]))
    order = {a.id: i for i, a in enumerate(asks)}
    reqs.sort(key=lambda r: min(order[i] for i in r.ask_ids))
    for n, r in enumerate(reqs, 1):
        r.id = f"{deal}:R-{n:02d}"
    return reqs


def match_round(deal: str, round_no: int, new_asks: list[Ask], reqs: list[Requirement],
                all_asks: dict[str, Ask], catalog: dict, offline=False) -> list[dict]:
    """Return one match record per new question; also updates `reqs` in place."""
    keys = list(catalog["facts"])
    req_ids = [r.id for r in reqs]
    schema = obj(matches=arr(obj(
        ask_id=enum(*[a.id for a in new_asks]),
        relation=enum("repeat", "repeat_with_addition", "changed", "new"),
        requirement_id=enum(*(req_ids + ["none"])), added_part=STR,
        facts=arr(enum(*keys)), title=STR)))
    existing = []
    for r in reqs:
        if r.status == "missing_text":
            continue
        qs = "; ".join(f"[{all_asks[i].qid or 'email'}] {all_asks[i].text}" for i in r.ask_ids
                       if i in all_asks)
        existing.append(f"{r.id} | {r.title} | {r.ask} | asked as: {qs}")
    user = (f"Catalog facts:\n{_catalog_lines(catalog)}\n\nExisting requirements:\n" +
            "\n".join(existing) + f"\n\nRound {round_no} questions (ask id | number | text):\n" +
            "\n".join(f"{a.id} | {a.qid} | {a.text}" for a in new_asks))
    out = llm.call_json("match", VERSION, MATCH_SYSTEM, user, schema, offline=offline)

    by_req = {r.id: r for r in reqs}
    earlier_qids = {all_asks[i].qid: all_asks[i] for r in reqs for i in r.ask_ids
                    if i in all_asks and all_asks[i].qid}
    results, seen = [], set()
    for m in out["matches"]:
        if m["ask_id"] in seen:
            continue
        seen.add(m["ask_id"])
        a = next(x for x in new_asks if x.id == m["ask_id"])
        rel, rid = m["relation"], m["requirement_id"]
        if rel != "new" and rid not in by_req:   # model named a requirement it was not shown
            rel, rid = "new", "none"
        facts = [k for k in m["facts"] if k in catalog["facts"]]
        reused = None
        prev = earlier_qids.get(a.qid)
        if prev and (rel == "new" or (rid in by_req and prev.id not in by_req[rid].ask_ids)):
            reused = f"Number {a.qid} was used in an earlier round for a different question: \"{prev.text}\""
        if rel == "new":
            r = Requirement(id=f"{deal}:R-{len(reqs) + 1:02d}", deal=deal,
                            title=m["title"] or a.text[:60], track=a.track or "Other", ask=a.text,
                            ask_ids=[a.id], facts=facts)
            reqs.append(r)
            by_req[r.id] = r
            rid = r.id
        else:
            r = by_req[rid]
            if r.status == "predicted":   # we expected this topic; now they have asked
                r.status = "open"
            r.ask_ids.append(a.id)
            r.facts += [k for k in facts if k not in r.facts]
        entry = {"round": round_no, "qid": a.qid, "relation": rel, "ask_id": a.id,
                 "added_part": m["added_part"], "reused_number": reused}
        r.history.append(entry)
        results.append({**entry, "requirement_id": rid, "question": a.text})
    for a in new_asks:   # anything the model skipped becomes its own requirement
        if a.id not in seen:
            r = Requirement(id=f"{deal}:R-{len(reqs) + 1:02d}", deal=deal, title=a.text[:60],
                            track=a.track or "Other", ask=a.text, ask_ids=[a.id], facts=[])
            r.history.append({"round": round_no, "qid": a.qid, "relation": "new", "ask_id": a.id,
                              "added_part": "", "reused_number": None})
            reqs.append(r)
            results.append({**r.history[-1], "requirement_id": r.id, "question": a.text})
    return results
