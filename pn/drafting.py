"""LLM step 3: draft an answer for one requirement, then check it.

Customer-facing text may only state facts that are verified. Anything else becomes a
"Pending" sentence naming the owner, so nothing unconfirmed leaves the company. Our current best
guess for unverified facts goes into internal notes that only Jai sees.

The checker is mostly code: every answer sentence must cite claims that belong to verified facts
of this requirement, and nothing may cite another customer. A second model call checks that each
cited quote actually supports its sentence.
"""

from __future__ import annotations

import hashlib
import json

from . import llm
from .llm import STR, arr, enum, obj
from .planner import fact_key, label

VERSION = "draft-v2"

SYSTEM = """You draft one answer to a customer's vendor-review question for the supplier, Lantern AI Technologies.
You are given the requirement, how this customer reads it, and the facts with their status and citable claims.

Rules:
- Write short, plain sentences a security or privacy reviewer would accept.
- kind "answer": a sentence stating only VERIFIED facts. Cite the claim ids it relies on (claim_ids). Never state a value from a fact that is not verified.
- kind "pending": for each fact that is not verified, one sentence saying what is being confirmed and by which team, e.g. "Pending: Engineering is confirming the retention period for observability traces." Put that fact key in pending_fact and no claim ids. Never include the unverified value in a pending sentence.
- Respect the customer's reading (e.g. if they separate storage from processing location, answer them separately).
- If a verified fact carries a caveat (e.g. an audit still in progress), state the caveat.
- internal_notes: for Jai only: our current best guess for each pending fact and anything risky. Not sent to the customer.
- Do not invent anything not in the facts."""

CHECK_SYSTEM = """For each sentence, judge whether the quoted evidence supports it. The supplier is Lantern AI Technologies;
naming the supplier or using plain wording is fine. Judge only whether the facts in the sentence come from the quotes.
supported = the quotes say this; partly = the sentence goes beyond the quotes; unsupported = the quotes do not say this."""


def _facts_payload(req, state) -> list[dict]:
    out = []
    for name in req.facts:
        k = fact_key(req.deal, name, state.catalog)
        f = state.facts[k]
        citable = [c for c in f.claims if c.tier < 9 and c.assertion != "questionnaire_draft"]
        out.append({
            "fact": k, "question": f.spec["question"], "status": f.status,
            "value": f.value if f.status == "verified" else None,
            "owner_team": f.spec["owner"], "caveats": f.warnings[:3],
            "claims": [{"id": c.id, "value": c.value, "quote": c.quote,
                        "source": state.sources[c.source_id].title}
                       for c in (citable if f.status == "verified" else [])][:4],
            "internal_best_guess": None if f.status == "verified" else f.value,
            "why_not_verified": None if f.status == "verified" else f.reason,
        })
    return out


def draft(req, state, offline=False) -> dict:
    asked = [state.asks[i] for i in req.ask_ids if i in state.asks]
    additions = [h["added_part"] for h in req.history if h.get("added_part")]
    instr = [i.text for i in state.instructions.get(req.deal, [])]
    payload = {
        "customer": state.deal_names[req.deal], "requirement": req.title, "ask": req.ask,
        "asked_as": [a.text for a in asked], "later_additions": additions,
        "customer_reading": req.reading, "customer_instructions": instr,
        "facts": _facts_payload(req, state),
    }
    fkeys = [fact_key(req.deal, n, state.catalog) for n in req.facts] or ["none"]
    schema = obj(sentences=arr(obj(kind=enum("answer", "pending"), text=STR,
                                   claim_ids=arr(STR), pending_fact=enum(*(fkeys + ["none"])))),
                 internal_notes=arr(STR))
    out = llm.call_json("draft", VERSION, SYSTEM, json.dumps(payload, indent=1, default=str),
                        schema, offline=offline)
    out["checks"] = check(req, out, state, offline=offline)
    out["version"] = hashlib.sha256(json.dumps(out["sentences"]).encode()).hexdigest()[:8]
    out["text"] = " ".join(s["text"] for s in out["sentences"])
    out["facts_used"] = {k: {"value": state.facts[k].value, "status": state.facts[k].status}
                         for k in fkeys if k in state.facts}
    return out


def check(req, out: dict, state, offline=False) -> dict:
    claims = {c.id: c for c in state.claims}
    allowed = {fact_key(req.deal, n, state.catalog) for n in req.facts}
    errors, warnings, per_sentence = [], [], []
    to_verify = []
    for i, s in enumerate(out["sentences"]):
        flags = []
        if s["kind"] == "answer":
            if not s["claim_ids"]:
                flags.append("no citation")
            for cid in s["claim_ids"]:
                c = claims.get(cid)
                if not c:
                    flags.append(f"cites unknown claim {cid}")
                    continue
                k = fact_key(req.deal, c.fact, state.catalog)
                if c.tier >= 9:
                    flags.append("cites another customer")
                elif k not in allowed:
                    flags.append(f"cites a fact outside this requirement ({label(k, state.catalog)})")
                elif state.facts[k].status != "verified":
                    flags.append(f"states an unverified fact ({label(k, state.catalog)})")
            if s["claim_ids"] and not flags:
                to_verify.append((i, s))
        else:
            pf = s["pending_fact"]
            if pf in state.facts and state.facts[pf].status == "verified":
                flags.append("marked pending but the fact is verified")
        per_sentence.append(flags)
        errors += [f"sentence {i + 1}: {f}" for f in flags]

    # model check: does each cited quote support its sentence?
    if to_verify:
        items = [{"n": i + 1, "sentence": s["text"],
                  "quotes": [claims[c].quote for c in s["claim_ids"] if c in claims]}
                 for i, s in to_verify]
        schema = obj(results=arr(obj(n=llm.NUM, label=enum("supported", "partly", "unsupported"),
                                     why=STR)))
        try:
            res = llm.call_json("check", VERSION, CHECK_SYSTEM, json.dumps(items), schema,
                                offline=offline)["results"]
            for r in res:
                n = int(r["n"])
                if r["label"] != "supported" and 1 <= n <= len(per_sentence):
                    per_sentence[n - 1].append(f"evidence {r['label']}: {r['why']}")
                    (errors if r["label"] == "unsupported" else warnings).append(
                        f"sentence {n}: evidence {r['label']} ({r['why']})")
        except llm.LLMError as e:
            warnings.append(f"support check skipped: {e}")

    covered = {s["pending_fact"] for s in out["sentences"] if s["kind"] == "pending"}
    for k in allowed:
        f = state.facts[k]
        if f.status != "verified" and k not in covered:
            warnings.append(f"{label(k, state.catalog)} is not verified and has no pending line")
        if f.status in ("stale", "expired"):
            warnings.append(f"{label(k, state.catalog)} is {f.status}: {f.reason}")
    return {"errors": errors, "warnings": warnings, "per_sentence": per_sentence,
            "can_approve": not errors}
