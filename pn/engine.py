"""Build the whole state from inputs: load -> extract -> merge requirements -> resolve facts -> plan.

Nothing is stored between builds except the LLM cache and the ledger of human actions, so the
picture is always consistent with the inputs. Adding an input and rebuilding is how new
information flows through. Drafting happens later, on request, per requirement.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import extract, ledger, planner, requirements, resolve
from .models import Ask, Claim, Fact, Requirement, Source
from .sources import load_sources, load_yaml, parse_questionnaire


@dataclass
class State:
    as_of: date
    catalog: dict
    policy: dict
    deal_names: dict[str, str]
    sources: dict[str, Source] = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)
    facts: dict[str, Fact] = field(default_factory=dict)
    asks: dict[str, Ask] = field(default_factory=dict)
    reqs: dict[str, list[Requirement]] = field(default_factory=dict)
    rounds: dict[str, list[dict]] = field(default_factory=dict)   # deal -> round 2+ match records
    timeline: dict[str, list[Ask]] = field(default_factory=dict)
    instructions: dict[str, list[Ask]] = field(default_factory=dict)
    draft_flags: list[dict] = field(default_factory=list)
    ledger: list[dict] = field(default_factory=list)
    plan: dict = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def build(as_of: date | None = None, offline: bool = False, ledger_path: Path | None = None) -> State:
    catalog, policy, deals_cfg = load_yaml("catalog.yaml"), load_yaml("policy.yaml"), load_yaml("deals.yaml")
    names = {k: v["name"] for k, v in deals_cfg["deals"].items()}
    st = State(as_of=as_of or date.fromisoformat(str(policy["as_of"])), catalog=catalog,
               policy=policy, deal_names=names)
    st.ledger = ledger.read(ledger_path) if ledger_path else ledger.read()
    for name, spec in catalog["facts"].items():   # a typo here would silently drop a blocker
        for dep in spec.get("depends_on", []) or []:
            if dep not in catalog["facts"]:
                st.errors.append(f"catalog: {name} depends on unknown fact {dep}")

    docs, questionnaires = _load(st, deals_cfg)
    predicted = _extract(st, docs, deals_cfg.get("context_year"), offline)
    drafts = _questionnaire_rows(st, questionnaires)
    for deal in names:
        _requirements(st, deal, predicted, offline)
    _attach_drafts(st, drafts)
    _resolve(st, offline)
    st.plan = planner.plan(st)
    return st


def _load(st: State, deals_cfg: dict) -> tuple[list[Source], list[Source]]:
    docs, questionnaires = load_sources(deals_cfg, st.errors)
    # Looking back in time (as_of moved before the configured today): a source dated after as_of
    # had not arrived yet, so it must not count. Undated sources are kept. At the configured
    # today the demo's added inputs carry the next few days' dates and must still count.
    today = date.fromisoformat(str(st.policy["as_of"]))
    if st.as_of < today:
        future = [s for s in docs + questionnaires if s.date and s.date > st.as_of]
        if future:
            st.errors.append(f"{len(future)} input(s) dated after {st.as_of} ignored (not yet "
                             f"received): " + ", ".join(s.title for s in future))
            docs = [s for s in docs if s not in future]
            questionnaires = [s for s in questionnaires if s not in future]
    ok = lambda s: s.deal is None or s.deal in st.deal_names
    for s in docs + questionnaires:
        if ok(s):
            st.sources[s.id] = s
        else:   # e.g. an added input with a misspelt deal: skip it, keep building
            st.errors.append(f"{s.title}: unknown deal {s.deal!r}; input ignored")
    return [s for s in docs if ok(s)], [s for s in questionnaires if ok(s)]


def _extract(st: State, docs: list[Source], context_year, offline) -> list[Ask]:
    """LLM, cached, parallel. Returns predicted asks (from company guidance) to copy into each deal."""
    outs = extract.run_all(docs, st.deal_names, st.catalog, offline=offline, context_year=context_year)
    predicted = []
    for s in docs:
        out = outs.get(s.id)
        if not out:
            continue
        if "error" in out:
            st.errors.append(f"{s.title}: {out['error']}")
            continue
        claims, asks, dropped = extract.to_objects(s, out)
        if extract.looks_injected(s.text):
            # "Ignore previous instructions...": its claims still go through the trust rules (a
            # chat or email can never be evidence), but its "how to answer" instructions would go
            # straight into the drafting prompt, so they are dropped and Jai is told.
            n = sum(a.kind == "instruction" for a in asks)
            asks = [a for a in asks if a.kind != "instruction"]
            st.errors.append(f"{s.title}: looks like a prompt injection; review this input"
                             + (f" ({n} instruction(s) from it ignored)." if n else "."))
        st.claims += claims
        st.dropped += [f"{s.title}: {d}" for d in dropped]
        for a in asks:
            if a.deal is None:
                predicted.append(a)
            else:
                st.asks[a.id] = a
    return predicted


def _questionnaire_rows(st: State, questionnaires: list[Source]) -> list[tuple[Source, dict]]:
    """Rows become asks by code. Returns the draft answers already typed into the sheets."""
    _number_rounds(questionnaires)
    drafts = []
    for q in questionnaires:
        if not q.deal:
            st.errors.append(f"{q.title}: a questionnaire needs a deal")
            continue
        rows, typed = parse_questionnaire(q)
        if not rows:
            st.errors.append(f"{q.title}: no questionnaire rows (needs a header with a 'question' column)")
        for a in rows:
            st.asks[a.id] = a
        drafts += [(q, d) for d in typed]
    return drafts


def _number_rounds(questionnaires: list[Source]) -> None:
    """Configured rounds first, then added ones in arrival order. An added questionnaire with no
    round, or a round number already used for that deal, becomes the next round: otherwise its
    row ids (deal:R1:Q-004) would overwrite the earlier round's rows."""
    taken: dict[str, set[int]] = {}
    for q in sorted(questionnaires, key=lambda x: x.added):
        try:
            rn = int(q.round) if q.round else None
        except (TypeError, ValueError):
            rn = None
        used = taken.setdefault(q.deal or "", set())
        if rn is None or rn in used:
            rn = max(used, default=0) + 1
        q.round = rn
        used.add(rn)


def _requirements(st: State, deal: str, predicted: list[Ask], offline) -> None:
    """Merge round 1 + email asks + predicted topics (LLM), hold no-text rows, match later rounds."""
    asks = [a for a in st.asks.values() if a.deal == deal]
    st.instructions[deal] = [a for a in asks if a.kind == "instruction"]
    st.timeline[deal] = [a for a in asks if a.kind == "constraint"]
    rows = [a for a in asks if a.origin == "questionnaire"]
    emails = [a for a in asks if a.origin == "customer" and a.kind in ("requirement", "conditional")]
    preds = []
    for p in predicted:
        c = copy.copy(p)
        c.deal, c.id = deal, p.id.replace("co:", f"{deal}:", 1)
        st.asks[c.id] = c
        preds.append(c)
    r1 = [a for a in rows if a.round == 1 and not a.template]
    try:
        reqs = requirements.merge_round_one(deal, st.deal_names[deal], r1 + emails + preds,
                                            st.instructions[deal], st.catalog, offline=offline)
    except Exception as e:  # noqa: BLE001 - keep building; the error shows in the sidebar
        st.errors.append(f"{deal} merge: {e}")
        reqs = []
    templ = [a for a in rows if a.template]
    if templ:
        reqs.append(Requirement(
            id=f"{deal}:no-text", deal=deal, title="Controls with no question text", track="Procurement",
            ask=(f"{len(templ)} questionnaire rows ({templ[0].qid}–{templ[-1].qid}) repeat the same "
                 f"boilerplate with only the number changed. There is nothing to answer until the "
                 f"customer sends the real control text."),
            ask_ids=[a.id for a in templ], facts=[], status="missing_text"))
    st.rounds[deal] = []
    for rn in sorted({a.round for a in rows if a.round > 1}):
        new = [a for a in rows if a.round == rn and not a.template]
        try:
            st.rounds[deal] += requirements.match_round(deal, rn, new, reqs, st.asks, st.catalog,
                                                        offline=offline)
        except Exception as e:  # noqa: BLE001
            st.errors.append(f"{deal} round {rn}: {e}")
    st.reqs[deal] = reqs


def _attach_drafts(st: State, drafts: list[tuple[Source, dict]]) -> None:
    """A typed-in draft answer becomes a hint claim on every fact of the requirement holding its
    row. The value is the draft text itself; the compare step decides whether it agrees."""
    for q, d in drafts:
        req = next((r for r in st.reqs.get(q.deal, []) if d["ask_id"] in r.ask_ids), None)
        for name in (req.facts if req else []):
            st.claims.append(Claim(
                id=f"D-{q.id}-{d['qid']}-{name}", source_id=q.id, deal=q.deal, fact=name,
                value=d["draft_answer"], quote=d["draft_answer"], speaker=None,
                applies_to="this_deal", assertion="questionnaire_draft", as_of_date=None,
                valid_until=None,
                note=f"typed into {d['qid']}" + (f"; sheet note: {d['note']}" if d["note"] else "")))


def _resolve(st: State, offline) -> None:
    """Rules, with the model only labelling whether claim pairs agree."""
    # a cached answer can name a fact that has since left the catalog
    st.dropped += [f"claim for unknown fact {c.fact}" for c in st.claims if c.fact not in st.catalog["facts"]]
    st.claims = [c for c in st.claims if c.fact in st.catalog["facts"]]
    for src in st.sources.values():
        dates = [c.as_of_date for c in st.claims if c.source_id == src.id and c.as_of_date]
        src.inferred_date = max(dates) if dates and not src.date else None
    for c in st.claims:
        resolve.assign_tier(c, st.sources[c.source_id], st.catalog["facts"][c.fact], st.policy)

    by_fact: dict[str, list[Claim]] = {}
    for name, spec in st.catalog["facts"].items():
        if spec["level"] == "company":
            keys = {f"company.{name}": None}
        else:
            keys = {f"{deal}.{name}": deal for deal in st.deal_names}
        for k, deal in keys.items():
            st.facts[k] = Fact(key=k, name=name, deal=deal, spec=spec)
            by_fact[k] = [c for c in st.claims if c.fact == name and (deal is None or c.deal in (deal, None))]

    pairs = [(st.facts[k].spec["question"], a, b) for k, cl in by_fact.items()
             for a, b in resolve.pairs_needed(cl)]
    labels = resolve.compare_pairs(pairs, offline=offline)
    for k, f in st.facts.items():
        resolve.resolve_fact(f, by_fact[k], st.as_of, labels, st.policy.get("expiry_warning_days", 45))
        for msg in resolve.draft_flags(f, labels, st.sources):
            if not any(x["text"] == msg for x in st.draft_flags):
                st.draft_flags.append({"fact": k, "text": msg})
    resolve.set_blockers(st.facts)
