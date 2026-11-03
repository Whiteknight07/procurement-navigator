"""Fixed rules: decide each fact's current value and status from its claims.

No model call decides anything here. The only model help is `compare_pairs`, which labels whether
two claims about the same fact agree; the rules then act on that label.

Status meanings (what Jai should do about it):
  verified     settled by the proof its catalog `proof` type needs; safe to use in an answer
  unconfirmed  we have a value, but not that proof (owner has not confirmed, no document, the
               customer has not said)
  conflicting  sources at a usable trust level disagree
  undecided    someone still has to make a choice
  out_of_date  the proof is stale (older than max_age_days) or expired (past its stated expiry)
  unknown      nothing usable says anything (hints may exist, but they never count)
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from . import llm
from .llm import STR, enum, obj
from .models import Claim, Fact, Source

HINT = 4   # tier of generic docs, questionnaire drafts and other customers' answers


# ---------- trust tiers ----------

def assign_tier(c: Claim, src: Source, spec: dict, policy: dict) -> None:
    t = policy["tiers"]
    # for recency only: a document is at least as recent as the latest date it mentions
    c.eff_date = c.as_of_date or src.date or src.inferred_date
    owner_person = policy["team_owner"].get(spec["owner"], "")
    speaker = (c.speaker or "").strip().lower()
    is_owner = bool(speaker) and speaker in {owner_person.lower(), spec["owner"].lower()}

    if c.applies_to == "other_customer":
        c.tier, c.tier_reason = t["hint"], "another customer's answer (a hint, never the answer)"
    elif c.assertion == "questionnaire_draft":
        c.tier, c.tier_reason = t["hint"], "a questionnaire draft answer (origin unknown, a hint)"
    elif src.system == "vanta" and c.assertion == "evidence":
        c.tier, c.tier_reason = t["evidence"], "the evidence store (mock Vanta)"
    elif spec["proof"] == "customer":
        if c.speaker_side == "customer":
            c.tier, c.tier_reason = t["evidence"], "the customer's own statement"
        else:
            c.tier, c.tier_reason = t["plan_or_chat"], "our own guess about the customer's position"
    elif c.applies_to == "generic_platform":
        c.tier, c.tier_reason = t["hint"], "a generic product doc (a hint, not this customer)"
    elif src.system in ("slack", "notes", "email") and is_owner and c.assertion == "stated":
        c.tier, c.tier_reason = t["owner_confirmed"], f"the owner ({spec['owner']}) stating it"
    elif src.system == "drive":
        c.tier, c.tier_reason = t["plan_or_chat"], "a document for this deal (a plan, not proof it is live)"
    else:
        c.tier, c.tier_reason = t["plan_or_chat"], "internal chat (not the owner, or not stated as settled)"


# ---------- comparing two claims ----------

_WORDS = re.compile(r"[a-z0-9]+")
_STOP = {"and", "the", "a", "of", "for", "plus", "is", "are", "with"}
_NUM = dict(zip("123456789", "one two three four five six seven eight nine".split()))


def tokens(v: str) -> frozenset:
    """Content words, singular, small numbers spelled out: "2 findings" == "two finding"."""
    return frozenset(_NUM.get(w, w).rstrip("s") for w in _WORDS.findall(v.lower()) if w not in _STOP)


COMPARE_SYSTEM = """You compare pairs of statements about the same procurement fact.
For each pair answer:
  agree            = same value, maybe worded differently
  adds_detail      = compatible; one adds detail the other lacks
  conflicts        = they cannot both be true for this fact
  different_aspect = they talk about different parts of the fact (e.g. app logs vs traces)
Judge only the values; ignore who said it."""

COMPARE_VERSION = "compare-v1"
AGREE = ("agree", "adds_detail")


def compare_pairs(pairs: list[tuple[str, Claim, Claim]], offline=False) -> dict[tuple, str]:
    """Label each (fact question, a, b). Identical values agree without a model call."""
    out, todo = {}, []
    for q, a, b in pairs:
        if (a.id, b.id) in out:
            continue
        if tokens(a.value) == tokens(b.value):
            out[(a.id, b.id)] = "agree"
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


def pairs_needed(claims: list[Claim]) -> list[tuple[Claim, Claim]]:
    """Every pair the rules might ask about: the best claim vs the others, drafts vs everything."""
    usable = sorted([c for c in claims if c.tier < HINT], key=_order)
    out = [(usable[0], c) for c in usable[1:]] if usable else []
    for d in (c for c in claims if c.assertion == "questionnaire_draft"):
        if usable:
            out.append((usable[0], d))
        out += [(d, h) for h in claims if h.tier == HINT and h.assertion != "questionnaire_draft"]
    return out


# ---------- resolving one fact ----------

def _order(c: Claim):
    return (c.tier, -(c.eff_date or date.min).toordinal())


def superseded(c: Claim, w: Claim, as_of: date) -> bool:
    """An older or expired claim that the winner replaces is not a conflict."""
    if c.valid_until and c.valid_until < as_of and not (w.valid_until and w.valid_until < as_of):
        return True
    return c.eff_date is not None and w.eff_date is not None and c.eff_date < w.eff_date


def _conflict(f: Fact, w: Claim, others: list[Claim], labels: dict, as_of: date) -> str | None:
    """Reason text if a usable claim disagrees with the winner, else None."""
    for c in others:
        if c.assertion == "undecided" or superseded(c, w, as_of):
            continue
        label = labels.get((w.id, c.id))
        if label == "conflicts":
            return f"Sources disagree: \"{w.value}\" ({w.tier_reason}) vs \"{c.value}\" ({c.tier_reason})."
        # The owner saying more than the evidence shows ("findings fixed" while the Vanta file says
        # they are open, "2026 report final" while Vanta has 2025) must not let the evidence on
        # file verify the fact. The model labels such pairs "adds_detail", so code checks the
        # words: does the owner's value say anything the winning evidence does not?
        if (f.spec["proof"] == "document" and w.tier == 1 and c.tier == 2 and label != "agree"
                and tokens(c.value) - tokens(f"{w.value} {w.quote}")):
            return (f"The owner says \"{c.value}\"; the evidence on file says \"{w.value}\". "
                    f"Get updated evidence.")
    return None


def resolve_fact(f: Fact, claims: list[Claim], as_of: date, labels: dict[tuple, str],
                 warning_days: int = 45) -> Fact:
    spec = f.spec
    f.hints = [c for c in claims if c.tier >= HINT]
    usable = sorted([c for c in claims if c.tier < HINT], key=_order)
    f.claims = usable

    if not usable:
        f.status, f.reason = "unknown", "Nothing usable says anything about this."
        for h in f.hints[:3]:
            f.reason += f" {h.tier_reason.split(' (')[0].capitalize()} says \"{h.value}\", which does not count."
        return f

    w = usable[0]
    f.winner, f.value = w, w.value
    undecided = [c for c in usable if c.assertion == "undecided"]
    settled = w.tier <= 2 and w.assertion in ("evidence", "stated")

    if undecided and not settled:
        u = undecided[0]
        f.status = "undecided"
        f.reason = f"Not decided yet: \"{u.quote}\" ({u.speaker or 'source'})."
        if w is not u:
            f.reason += f" A plan says \"{w.value}\" but a plan does not settle an open decision."
        return f

    conflict = _conflict(f, w, usable[1:], labels, as_of)
    if conflict:
        f.status, f.reason = "conflicting", conflict
        return f

    # the proof the catalog asks for: a document, the owner, or the customer
    need = {"document": 1, "customer": 1}.get(spec["proof"], 2)
    if w.tier > need:
        f.status = "unconfirmed"
        f.reason = {"document": f"Only {w.tier_reason} supports it. This needs a document in the evidence store.",
                    "customer": "Only our own guess; the customer has not said."}.get(
            spec["proof"], f"Best source is {w.tier_reason}; the owner ({spec['owner']}) has not confirmed it.")
        return f

    # settled by the right kind of proof: check it is still fresh
    f.status, f.reason = "verified", f"Settled by {w.tier_reason}."
    max_age = spec.get("max_age_days")
    if w.valid_until and w.valid_until < as_of:
        f.status, f.reason = "out_of_date", f"Expired on {w.valid_until:%b %d, %Y}."
    elif max_age and w.as_of_date and (as_of - w.as_of_date).days > max_age:
        f.status = "out_of_date"
        f.reason = (f"Stale: dated {w.as_of_date:%b %Y}, {(as_of - w.as_of_date).days} days old; "
                    f"policy allows {max_age}.")
    else:
        if max_age and not w.as_of_date and not w.valid_until:
            # no date at all: we cannot tell whether it is fresh, so say so instead of passing silently
            f.warnings.append(f"No date on the proof; cannot check it is within {max_age} days.")
        end = w.valid_until or (w.as_of_date + timedelta(days=max_age) if max_age and w.as_of_date else None)
        if end and (end - as_of).days <= warning_days:
            f.warnings.append(f"Expires {end:%b %d, %Y}; start renewal now.")
    if w.note:
        f.warnings.append(w.note)
    # equally trusted, compatible detail is a caveat Jai must see ("2026 audit not yet final")
    f.warnings += [f"Also on file: {c.value}" for c in usable[1:] if c.tier == w.tier
                   and labels.get((w.id, c.id)) == "adds_detail" and not superseded(c, w, as_of)]
    return f


def draft_flags(f: Fact, labels: dict[tuple, str], sources: dict[str, Source]) -> list[str]:
    """Questionnaire drafts that disagree with the settled value, and where they likely came from."""
    flags = []
    for d in (h for h in f.hints if h.assertion == "questionnaire_draft"):
        if f.winner and labels.get((f.winner.id, d.id)) in AGREE + ("different_aspect",):
            continue   # agrees, or the draft is about another fact of the same question
        origin = sorted((h for h in f.hints if h.assertion != "questionnaire_draft"
                         and labels.get((d.id, h.id)) in AGREE),
                        key=lambda h: h.applies_to != "other_customer")   # copied from a customer first
        msg = f"[{d.deal}] questionnaire draft \"{d.quote}\" "
        msg += f"disagrees with the best source (\"{f.value}\")" if f.winner else "has nothing behind it"
        if origin:
            msg += f"; it matches {sources[origin[0].source_id].title} ({origin[0].tier_reason})"
        flags.append(msg + ".")
    return flags


# ---------- dependencies ----------

def dep_keys(key: str, facts: dict[str, Fact]) -> list[str]:
    """Fact keys that `key` depends on. A dependency on a company-level fact points at the one
    shared company fact, not at a per-deal key that does not exist."""
    prefix = key.rsplit(".", 1)[0]
    out = []
    for dep in facts[key].spec.get("depends_on", []) or []:
        dk = f"company.{dep}" if f"company.{dep}" in facts else f"{prefix}.{dep}"
        if dk in facts and dk != key:
            out.append(dk)
    return out


def set_blockers(facts: dict[str, Fact]) -> None:
    for f in facts.values():
        # recomputed from scratch so calling it twice cannot duplicate entries
        f.blocked_by = [dk for dk in dep_keys(f.key, facts) if facts[dk].status != "verified"]


def root_causes(key: str, facts: dict[str, Fact], seen=None) -> list[str]:
    """Follow blocked_by down to facts that are not blocked themselves: the real things to fix."""
    # `seen` is the path from the starting fact, not every fact visited: two facts sharing a
    # dependency (a diamond) is normal; only a path that loops back to itself is cut off.
    seen = seen or set()
    if key in seen:
        return []
    f = facts[key]
    if not f.blocked_by:
        return [key]
    out = []
    for b in f.blocked_by:
        for r in root_causes(b, facts, seen | {key}):
            if r not in out:
                out.append(r)
    # Empty only when every path loops back (a depends_on cycle in the catalog). Treat this fact
    # as a root then, otherwise the gap would vanish from blockers and actions.
    return out or [key]


def chain(key: str, facts: dict[str, Fact]) -> list[str]:
    """One readable path from a fact down to a root cause."""
    path = [key]
    while facts[path[-1]].blocked_by:
        nxt = facts[path[-1]].blocked_by[0]
        if nxt in path:
            break
        path.append(nxt)
    return path
