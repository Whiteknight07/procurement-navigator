"""Build the whole state from inputs: load -> extract -> resolve -> merge -> plan.

Nothing is stored between builds except the LLM cache and the ledger of human actions, so the
picture is always consistent with the inputs. Adding an input and rebuilding is how new
information flows through.
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
    deals_cfg: dict
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
    summaries: dict[str, str] = field(default_factory=dict)


def build(as_of: date | None = None, offline: bool = False, ledger_path: Path | None = None) -> State:
    catalog, policy, deals_cfg = load_yaml("catalog.yaml"), load_yaml("policy.yaml"), load_yaml("deals.yaml")
    as_of = as_of or date.fromisoformat(str(policy["as_of"]))
    names = {k: v["name"] for k, v in deals_cfg["deals"].items()}
    st = State(as_of=as_of, catalog=catalog, policy=policy, deals_cfg=deals_cfg, deal_names=names)
    st.ledger = ledger.read(ledger_path) if ledger_path else ledger.read()

    for name, spec in catalog["facts"].items():   # a typo here would silently drop a blocker
        for dep in spec.get("depends_on", []) or []:
            if dep not in catalog["facts"]:
                st.errors.append(f"catalog: {name} depends on unknown fact {dep}")

    # 1. load
    docs, questionnaires = load_sources(deals_cfg)
    known = [s for s in docs + questionnaires if s.deal is None or s.deal in names]
    for s in docs + questionnaires:
        if s not in known:   # e.g. an added input with a misspelt deal: skip it, keep building
            st.errors.append(f"{s.title}: unknown deal {s.deal!r}; input ignored")
    docs = [s for s in docs if s in known]
    questionnaires = [s for s in questionnaires if s in known]
    for s in docs + questionnaires:
        st.sources[s.id] = s

    # 2. extract (LLM, cached, parallel)
    outs = extract.run_all(docs, names, catalog, offline=offline,
                           context_year=deals_cfg.get("context_year"))
    gaps: list[dict] = []
    predicted: list[Ask] = []
    for s in docs:
        out = outs.get(s.id)
        if not out:
            continue
        if "error" in out:
            st.errors.append(f"{s.title}: {out['error']}")
            continue
        st.summaries[s.id] = out.get("summary", "")
        claims, g, asks, dropped = extract.to_objects(s, out)
        # a cached answer can name a fact that has since left the catalog
        dropped += [f"claim for unknown fact {c.fact}" for c in claims if c.fact not in catalog["facts"]]
        claims = [c for c in claims if c.fact in catalog["facts"]]
        st.claims += claims
        gaps += g
        st.dropped += [f"{s.title}: {d}" for d in dropped]
        for a in asks:
            if a.deal is None:
                predicted.append(a)
            else:
                st.asks[a.id] = a

    # questionnaires: rows by code, typed-in draft answers mapped by the model
    q_asks: dict[str, dict[int, list[Ask]]] = {}
    # Configured rounds first, then added ones in arrival order. An added questionnaire with no
    # round, or a round number already used for that deal, becomes the next round: otherwise its
    # row ids (deal:R1:Q-004) would overwrite the earlier round's rows.
    taken: dict[str, set[int]] = {}
    for q in [x for x in questionnaires if not x.added] + [x for x in questionnaires if x.added]:
        if not q.deal:
            continue
        try:
            rn = int(q.round) if q.round else None
        except (TypeError, ValueError):
            rn = None
        used = taken.setdefault(q.deal, set())
        if rn is None or rn in used:
            rn = max(used, default=0) + 1
        q.round = rn
        used.add(rn)
    for q in sorted(questionnaires, key=lambda x: (x.deal or "", x.round or 1)):
        if not q.deal:
            st.errors.append(f"{q.title}: a questionnaire needs a deal")
            continue
        rows, drafts = parse_questionnaire(q)
        q_asks.setdefault(q.deal, {}).setdefault(q.round, []).extend(rows)
        for a in rows:
            st.asks[a.id] = a
        try:
            mapped = extract.extract_drafts(q.deal, drafts, catalog, offline=offline)
        except Exception as e:  # noqa: BLE001 - keep building without draft checks
            st.errors.append(f"{q.title} drafts: {e}")
            mapped = []
        by_qid = {d["qid"]: d for d in drafts}
        for i, m in enumerate(mapped):
            d = by_qid.get(m["qid"])
            if not d:
                continue
            for fname in m["facts"]:
                # full source id: added questionnaires share long prefixes
                st.claims.append(Claim(
                    id=f"D-{q.id}-{i}-{fname}", source_id=q.id, deal=q.deal, fact=fname,
                    value=m["value"], quote=d["draft_answer"], speaker=None, applies_to="this_deal",
                    assertion="questionnaire_draft", as_of_date=None, valid_until=None,
                    note=f"typed into {d['qid']}" + (f"; sheet note: {d['note']}" if d["note"] else "")))

    # 3. resolve facts (rules), with the model only labelling whether claim pairs agree
    for src in st.sources.values():
        dates = [c.as_of_date for c in st.claims if c.source_id == src.id and c.as_of_date]
        src.inferred_date = max(dates) if dates and not src.date else None  # type: ignore[attr-defined]
    for c in st.claims:
        resolve.assign_tier(c, st.sources[c.source_id], catalog["facts"][c.fact], policy)
    by_fact: dict[str, list[Claim]] = {}
    for name, spec in catalog["facts"].items():
        if spec["level"] == "company":
            st.facts[f"company.{name}"] = Fact(key=f"company.{name}", name=name, deal=None, spec=spec)
            by_fact[f"company.{name}"] = [c for c in st.claims if c.fact == name]
        else:
            for deal in names:
                k = f"{deal}.{name}"
                st.facts[k] = Fact(key=k, name=name, deal=deal, spec=spec)
                by_fact[k] = [c for c in st.claims if c.fact == name and c.deal in (deal, None)]
    pairs = []
    for k, cl in by_fact.items():
        q = st.facts[k].spec["question"]
        pairs += [(q, a, b) for a, b in resolve.pairs_needed(cl)]
    labels = resolve.compare_pairs(pairs, offline=offline)
    for k, f in st.facts.items():
        fg = [g for g in gaps if g["fact"] == f.name and (f.deal is None or g["deal"] in (f.deal, None))]
        resolve.resolve_fact(f, by_fact[k], as_of, policy, labels, fg)
        for msg in resolve.draft_flags(f, labels, st.sources):
            if not any(x["text"] == msg for x in st.draft_flags):
                st.draft_flags.append({"fact": k, "text": msg})
    resolve.set_blockers(st.facts)

    # 4. requirements (LLM merge, then later rounds matched)
    for deal in names:
        deal_asks = [a for a in st.asks.values() if a.deal == deal]
        st.instructions[deal] = [a for a in deal_asks if a.kind == "instruction"]
        st.timeline[deal] = [a for a in deal_asks if a.kind == "constraint"]
        rounds = q_asks.get(deal, {})
        r1 = [a for a in rounds.get(1, []) if not a.template]
        emails = [a for a in deal_asks if a.origin == "customer" and a.kind in ("requirement", "conditional")]
        preds = []
        for p in predicted:
            c = copy.copy(p)
            c.deal, c.id = deal, p.id.replace("co:", f"{deal}:", 1)
            st.asks[c.id] = c
            preds.append(c)
        try:
            reqs = requirements.merge_round_one(deal, names[deal], r1 + emails + preds,
                                                st.instructions[deal], catalog, offline=offline)
        except Exception as e:  # noqa: BLE001
            st.errors.append(f"{deal} merge: {e}")
            reqs = []
        templ = [a for r in rounds.values() for a in r if a.template]
        if templ:
            reqs.append(Requirement(
                id=f"{deal}:R-00", deal=deal, title="Controls with no question text", track="Procurement",
                ask=(f"{len(templ)} questionnaire rows ({templ[0].qid}–{templ[-1].qid}) repeat the same "
                     f"boilerplate with only the number changed. There is nothing to answer until the "
                     f"customer sends the real control text."),
                ask_ids=[a.id for a in templ], facts=[], status="missing_text"))
        st.rounds[deal] = []
        for rn in sorted(k for k in rounds if k > 1):
            new = [a for a in rounds[rn] if not a.template]
            if new:
                try:
                    st.rounds[deal] += requirements.match_round(deal, rn, new, reqs, st.asks, catalog,
                                                                offline=offline)
                except Exception as e:  # noqa: BLE001
                    st.errors.append(f"{deal} round {rn}: {e}")
        st.reqs[deal] = reqs

    # 5. plan (rules)
    st.plan = planner.plan(st)
    return st
