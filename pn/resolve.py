"""Fixed rules: decide each fact's current value and status from its claims.

No model call decides anything here. The only model help is `compare`, which labels whether two
claims about the same fact agree; the rules then act on that label.

Status meanings (what Jai should do about it):
  verified            settled by the right kind of proof; safe to use in an answer
  needs_confirmation  we have a value, but only from a plan, chat or generic doc: ask the owner
  needs_proof         the fact needs a document in the evidence store and we only have talk
  conflicting         sources at a usable trust level disagree
  undecided           someone still has to make a choice
  stale               evidence exists but is older than the policy allows
  expired             evidence exists but its stated expiry date has passed
  unknown             nothing usable says anything
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from . import llm
from .llm import STR, arr, enum, obj
from .models import Claim, Fact, Source

TIER_NAMES = {1: "evidence / customer statement", 2: "owner confirmation", 3: "deal document",
              4: "internal chat", 5: "generic doc or questionnaire draft", 9: "other customer"}


# ---------- trust tiers ----------

def assign_tier(c: Claim, src: Source, spec: dict, policy: dict) -> None:
    t = policy["tiers"]
    # for recency only: a document is at least as recent as the latest date it mentions
    c.eff_date = c.as_of_date or src.date or getattr(src, "inferred_date", None)  # type: ignore[attr-defined]
    owner_person = policy["team_owner"].get(spec["owner"], "")
    speaker = (c.speaker or "").strip().lower()
    is_owner = bool(speaker) and speaker in {owner_person.lower(), spec["owner"].lower()}
    side = getattr(c, "speaker_side", "unknown")

    if c.applies_to == "other_customer":
        c.tier, c.tier_reason = t["other_customer"], "another customer's answer (a hint, never the answer)"
    elif c.assertion == "questionnaire_draft":
        c.tier, c.tier_reason = t["questionnaire_draft"], "a draft typed into the questionnaire (origin unknown)"
    elif src.system == "vanta" and c.assertion == "evidence":
        c.tier, c.tier_reason = t["evidence"], "the evidence store (mock Vanta)"
    elif spec["proof"] == "customer":
        if side == "customer":
            c.tier, c.tier_reason = t["customer_statement"], "the customer's own statement"
        else:
            c.tier, c.tier_reason = t["internal_chat"], "our own guess about the customer's position"
    elif c.applies_to == "generic_platform":
        c.tier, c.tier_reason = t["generic_document"], "a generic product doc (not this customer)"
    elif src.system in ("slack", "notes", "email"):
        if is_owner and c.assertion == "stated":
            c.tier, c.tier_reason = t["owner_confirmation"], f"the owner ({spec['owner']}) stating it"
        else:
            c.tier, c.tier_reason = t["internal_chat"], "internal chat (not the owner, or not stated as settled)"
    elif src.system == "drive":
        c.tier, c.tier_reason = t["deal_document"], "a document for this deal (a plan, not proof it is live)"
    else:
        c.tier, c.tier_reason = t["internal_chat"], src.system


# ---------- comparing two claims ----------

_WORDS = re.compile(r"[a-z0-9]+")
_STOP = {"and", "the", "a", "of", "for", "plus", "is", "are", "with"}


def _tokens(v: str) -> frozenset:
    return frozenset(w.rstrip("s") for w in _WORDS.findall(v.lower()) if w not in _STOP)


COMPARE_SYSTEM = """You compare pairs of statements about the same procurement fact.
For each pair answer:
  agree            = same value, maybe worded differently
  adds_detail      = compatible; one adds detail the other lacks
  conflicts        = they cannot both be true for this fact
  different_aspect = they talk about different parts of the fact (e.g. app logs vs traces)
Judge only the values; ignore who said it."""

COMPARE_VERSION = "compare-v1"


def compare_pairs(pairs: list[tuple[str, Claim, Claim]], offline=False) -> dict[tuple, str]:
    """Label each (fact question, a, b). Identical values agree without a model call."""
    out, todo = {}, []
    for q, a, b in pairs:
        k = (a.id, b.id)
        if k in out:
            continue
        if _tokens(a.value) == _tokens(b.value):
            out[k] = "agree"
        else:
            todo.append((q, a, b))
    schema = obj(label=enum("agree", "adds_detail", "conflicts", "different_aspect"), why=STR)

    def one(item):
        q, a, b = item
        user = (f"Fact: {q}\nA: {a.value} (quote: \"{a.quote}\")\n"
                f"B: {b.value} (quote: \"{b.quote}\")")
        try:
            r = llm.call_json("compare", COMPARE_VERSION, COMPARE_SYSTEM, user, schema, offline=offline)
            return (a.id, b.id), r["label"]
        except llm.LLMError:
            return (a.id, b.id), "unknown"

    with ThreadPoolExecutor(8) as pool:
        out.update(dict(pool.map(one, todo)))
    return out


# ---------- resolving one fact ----------

def _eff(c: Claim) -> date | None:
    return getattr(c, "eff_date", None) or c.as_of_date


def _order(c: Claim):
    d = _eff(c) or date.min
    return (c.tier, -d.toordinal())


def superseded(c: Claim, w: Claim, as_of: date) -> bool:
    """An older or expired claim that the winner replaces is not a conflict."""
    if c.valid_until and c.valid_until <= as_of and not (w.valid_until and w.valid_until <= as_of):
        return True
    cd, wd = _eff(c), _eff(w)
    return cd is not None and wd is not None and cd < wd


def resolve_fact(f: Fact, claims: list[Claim], as_of: date, policy: dict,
                 labels: dict[tuple, str], gaps: list[dict]) -> Fact:
    spec = f.spec
    f.hints = [c for c in claims if c.tier >= 9]
    drafts = [c for c in claims if c.assertion == "questionnaire_draft"]
    usable = sorted([c for c in claims if c.tier < 9 and c.assertion != "questionnaire_draft"],
                    key=_order)
    f.claims = usable + drafts
    for g in gaps:
        f.warnings.append(f"Source says: {g['note']}")

    if not usable:
        f.status = "unknown"
        f.reason = "Nothing usable says anything about this."
        if f.hints:
            f.reason += f" Another customer's answer exists ({f.hints[0].value}) but it is not evidence for this deal."
        if drafts:
            f.reason += f" A questionnaire draft says \"{drafts[0].value}\" with no source behind it."
        return f

    w = usable[0]
    f.winner, f.value = w, w.value
    undecided = [c for c in usable if c.assertion == "undecided"]
    conflicts = [c for c in usable[1:] if c.tier <= 4 and c.assertion != "undecided"
                 and labels.get((w.id, c.id)) == "conflicts" and not superseded(c, w, as_of)]
    settled = w.tier <= 2 and w.assertion in ("evidence", "stated")

    if (w.assertion == "undecided" or undecided) and not settled:
        u = undecided[0] if undecided else w
        f.status = "undecided"
        f.reason = f"Not decided yet: \"{u.quote}\" ({u.speaker or 'source'})."
        if w.assertion != "undecided":
            f.reason += f" A plan says \"{w.value}\" but a plan does not settle an open decision."
        return f

    if conflicts:
        c = conflicts[0]
        f.status = "conflicting"
        f.reason = (f"Sources disagree: \"{w.value}\" ({w.tier_reason}) vs \"{c.value}\" "
                    f"({c.tier_reason}).")
        return f

    if spec["proof"] == "document":
        if w.tier != 1:
            f.status = "needs_proof"
            f.reason = f"Only {w.tier_reason} supports it. This needs a document in the evidence store."
            return f
    elif spec["proof"] == "customer":
        if w.tier != 1:
            f.status = "unknown"
            f.reason = "Only our own guess; the customer has not said."
            return f
    elif w.tier > 2:
        f.status = "needs_confirmation"
        f.reason = f"Best source is {w.tier_reason}; the owner ({spec['owner']}) has not confirmed it."
        return f

    # settled by the right kind of proof: check it is still fresh
    f.status, f.reason = "verified", f"Settled by {w.tier_reason}."
    max_age = spec.get("max_age_days")
    if w.valid_until and w.valid_until < as_of:
        f.status = "expired"
        f.reason = f"Expired on {w.valid_until:%b %d, %Y}."
    elif max_age and w.as_of_date and (as_of - w.as_of_date).days > max_age:
        f.status = "stale"
        f.reason = (f"Dated {w.as_of_date:%b %Y}, {(as_of - w.as_of_date).days} days old; "
                    f"policy allows {max_age}.")
    else:
        end = w.valid_until or (w.as_of_date + timedelta(days=max_age) if max_age and w.as_of_date else None)
        if end and (end - as_of).days <= policy.get("expiry_warning_days", 45):
            f.warnings.append(f"Expires {end:%b %d, %Y}; start renewal now.")
    if w.note:
        f.warnings.append(w.note)
    return f


def draft_flags(f: Fact, labels: dict[tuple, str], sources: dict[str, Source]) -> list[str]:
    """Questionnaire drafts that disagree with the settled value, and where they likely came from."""
    flags = []
    for d in (c for c in f.claims if c.assertion == "questionnaire_draft"):
        if f.winner and labels.get((f.winner.id, d.id)) in ("agree", "adds_detail"):
            continue
        origin = [c for c in f.hints + [c for c in f.claims if c.tier == 5 and c is not d]
                  if labels.get((d.id, c.id)) in ("agree", "adds_detail")]
        msg = f"[{d.deal}] questionnaire draft \"{d.quote}\" "
        if f.winner:
            msg += f"disagrees with the best source (\"{f.value}\")"
        else:
            msg += "has nothing behind it"
        if origin:
            o = origin[0]
            msg += f"; it matches {sources[o.source_id].title} ({o.tier_reason})"
        flags.append(msg + ".")
    return flags


def pairs_needed(claims: list[Claim]) -> list[tuple[Claim, Claim]]:
    """Every pair the rules might ask about: best claim vs the others, drafts vs everything."""
    usable = sorted([c for c in claims if c.tier < 9 and c.assertion != "questionnaire_draft"], key=_order)
    out = []
    if usable:
        out += [(usable[0], c) for c in usable[1:] if c.tier <= 4]
    for d in (c for c in claims if c.assertion == "questionnaire_draft"):
        if usable:
            out.append((usable[0], d))
        out += [(d, c) for c in claims if (c.tier >= 9 or c.tier == 5) and c is not d
                and c.assertion != "questionnaire_draft"]
    return out


def set_blockers(facts: dict[str, Fact]) -> None:
    for f in facts.values():
        prefix = f.key.rsplit(".", 1)[0]
        for dep in f.spec.get("depends_on", []) or []:
            dk = f"{prefix}.{dep}"
            if dk in facts and facts[dk].status != "verified":
                f.blocked_by.append(dk)


def root_causes(key: str, facts: dict[str, Fact], seen=None) -> list[str]:
    """Follow blocked_by down to facts that are not blocked themselves: the real things to fix."""
    seen = seen or set()
    if key in seen:
        return []
    seen.add(key)
    f = facts[key]
    if not f.blocked_by:
        return [key]
    out = []
    for b in f.blocked_by:
        for r in root_causes(b, facts, seen):
            if r not in out:
                out.append(r)
    return out


def chain(key: str, facts: dict[str, Fact]) -> list[str]:
    """One readable path from a fact down to a root cause."""
    path = [key]
    while facts[path[-1]].blocked_by:
        nxt = facts[path[-1]].blocked_by[0]
        if nxt in path:
            break
        path.append(nxt)
    return path
