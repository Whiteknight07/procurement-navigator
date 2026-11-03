"""LLM step 1: read one input and pull out claims (about facts) and asks (from the customer).

The model only extracts and labels. It never decides which claim wins; the resolver does that
with fixed rules. Every claim and ask must carry an exact quote, and code drops any quote that
is not actually in the source, so the model cannot invent evidence.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from . import llm
from .llm import BOOL, NSTR, STR, arr, enum, obj
from .models import Ask, Claim, Source

VERSION = "extract-v3"

SYSTEM = """You read one input from a vendor-onboarding (procurement) case and extract structured data.
The supplier is Lantern AI Technologies (the company we work for). A prospective customer is reviewing it.

You extract three things:
1. claims: statements of a value for a fact in the catalog (where data is stored, which model providers, log retention, SOC 2 status...).
2. gaps: places where the input says something is missing, unknown or lacks evidence for a catalog fact.
3. asks: things the CUSTOMER requires or asks of the supplier (only from customer-written inputs), or, for guidance documents, topics such reviews commonly ask about.

Hard rules:
- quote: copy the exact characters from the input (a short span, usually one line or clause). Never paraphrase in a quote.
- Never invent values. Only extract what the input says.
- value: the shortest normalized answer, e.g. "14 days", "OpenAI", "OpenAI, Anthropic", "AWS ca-central-1", "off unless approved", "SOC 2 Type II Jan-Dec 2025 final". For lists, comma-separate.
- One claim per fact per statement. A sentence that states two facts gives two claims.
- applies_to:
  this_deal = about this customer's deal or the configuration this customer would receive;
  company = about the supplier as a whole (audit reports, insurance, pen tests, company policies);
  generic_platform = describes the general product or platform, not configured for this customer;
  other_customer = about a different customer (prior customer answers). Always use this for another customer's setup.
- assertion:
  evidence = the input is a formal evidence record (report summary, certificate, audit status) stating it;
  stated = someone states it as current fact;
  planned = intended, proposed, draft, "should be", "intended", "unless approved";
  undecided = explicitly not finalized, TBD, "probably", depends on a pending decision.
- speaker: the person who said it if the input names them (e.g. "Anthony" in a chat line), else null.
- speaker_side: customer, supplier or unknown.
- as_of_date: the date the fact refers to (report date, end of an audit period), as YYYY-MM-DD. If only a month is given, use the first of that month; if only a year range, use its last day. Null if none.
- valid_until: an expiry date the input states (e.g. certificate expiry), YYYY-MM-DD, else null.
- note: anything a reviewer must know (e.g. "predates August logging changes", "needs evidence", "2026 audit not final"). Empty string if none.
- Use fact "other" only when no catalog fact fits.

Asks:
- Only from inputs written by the customer (correspondence) or guidance on what reviews require.
- Split compound requests: one ask per distinct thing asked.
- kind: requirement = something they need answered or provided;
        instruction = how they want us to answer (e.g. "answer for the configuration we would receive");
        constraint = a deadline, target date, meeting cadence or process fact (e.g. "October production pilot", "AI Governance meets Wednesdays"). Always emit one for every timeline or target date the customer mentions;
        conditional = a review or requirement that may be triggered, or a decision whose impact they want flagged;
        predicted = from a guidance document: a topic such reviews commonly cover (not yet asked by this customer).
- who: the customer person or team asking, if named. track: Security, Privacy, AI Risk, Resilience, Legal, Procurement, Supplier, Governance or Records.
- due_date: if the ask states a deadline or target date, YYYY-MM-DD (first of the month if only a month). Dates without a year are in the context_year from the metadata.
"""


def _catalog_text(catalog: dict) -> str:
    return "\n".join(f"- {k} ({v['level']}): {v['question']}" for k, v in catalog["facts"].items())


def _schema(catalog: dict) -> dict:
    keys = list(catalog["facts"]) + ["other"]
    claim = obj(fact=enum(*keys), value=STR, quote=STR, speaker=NSTR,
                speaker_side=enum("customer", "supplier", "unknown"),
                applies_to=enum("this_deal", "company", "generic_platform", "other_customer"),
                assertion=enum("evidence", "stated", "planned", "undecided"),
                as_of_date=NSTR, valid_until=NSTR, note=STR)
    gap = obj(fact=enum(*keys), quote=STR, note=STR)
    ask = obj(text=STR, quote=STR, who=NSTR, track=NSTR,
              kind=enum("requirement", "instruction", "constraint", "conditional", "predicted"),
              due_date=NSTR)
    return obj(summary=STR, claims=arr(claim), gaps=arr(gap), asks=arr(ask))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in(quote: str, text: str) -> bool:
    q = _norm(quote).strip(" .\"'")
    return bool(q) and q in _norm(text)


def _d(s) -> date | None:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def extract_source(src: Source, deal_names: dict, catalog: dict, offline=False,
                   context_year: int | None = None) -> dict:
    about = (f"the {deal_names[src.deal]} deal" if src.deal
             else "the whole supplier company (shared by every deal)")
    meta = {"title": src.title, "system": src.system, "folder_scope": about,
            "author": src.author, "date": str(src.date) if src.date else None,
            "context_year": context_year}
    user = (f"Fact catalog:\n{_catalog_text(catalog)}\n\nInput metadata: {json.dumps(meta)}\n\n"
            f"Input text:\n<<<\n{src.text}\n>>>")
    out = llm.call_json("extract", VERSION, SYSTEM, user, _schema(catalog), offline=offline)
    return out


def to_objects(src: Source, out: dict) -> tuple[list[Claim], list[dict], list[Ask], list[str]]:
    """Turn model output into objects, dropping anything whose quote is not in the source."""
    claims, gaps, asks, dropped = [], [], [], []
    for i, c in enumerate(out.get("claims", [])):
        if not quote_in(c["quote"], src.text):
            dropped.append(f"claim quote not in source: {c['quote'][:60]}")
            continue
        if c["fact"] == "other":
            continue
        claims.append(Claim(
            id=f"C-{src.id[:6]}-{i}", source_id=src.id, deal=src.deal, fact=c["fact"],
            value=c["value"], quote=c["quote"], speaker=c["speaker"] or src.author,
            applies_to=c["applies_to"], assertion=c["assertion"],
            as_of_date=_d(c["as_of_date"]), valid_until=_d(c["valid_until"]), note=c["note"]))
        claims[-1].speaker_side = c["speaker_side"]  # type: ignore[attr-defined]
    for g in out.get("gaps", []):
        if quote_in(g["quote"], src.text) and g["fact"] != "other":
            gaps.append({**g, "source_id": src.id, "deal": src.deal})
    # Asks from a company-wide source (e.g. review guidance) have deal=None; only predicted
    # topics are kept from those, and the engine copies them into every deal.
    for i, a in enumerate(out.get("asks", [])):
        if not quote_in(a["quote"], src.text):
            dropped.append(f"ask quote not in source: {a['quote'][:60]}")
            continue
        if src.deal is None and a["kind"] != "predicted":
            continue
        if src.system != "email" and a["kind"] != "predicted":
            continue   # only the customer's own messages create customer requirements
        origin = "predicted" if a["kind"] == "predicted" else "customer"
        asks.append(Ask(id=f"{src.deal or 'co'}:{src.id[:6]}:{i}", deal=src.deal, origin=origin,
                        text=a["text"], quote=a["quote"], source_id=src.id, who=a["who"],
                        track=a["track"], kind=a["kind"], date_text=a["due_date"]))
    return claims, gaps, asks, dropped


# ---------- answers already typed into a questionnaire ----------

DRAFT_SYSTEM = """A questionnaire already has some draft answers typed in by someone on the supplier side; nobody knows where they came from.
Map each draft answer to the catalog fact(s) it answers and give its normalized value (same style as: "30 days", "OpenAI, Anthropic", "24h RTO, 24h RPO").
Copy the draft answer text exactly into quote. Do not judge whether it is right."""


def extract_drafts(deal: str, drafts: list[dict], catalog: dict, offline=False) -> list[dict]:
    if not drafts:
        return []
    keys = list(catalog["facts"])
    schema = obj(items=arr(obj(qid=STR, facts=arr(enum(*keys)), value=STR, quote=STR)))
    user = (f"Fact catalog:\n{_catalog_text(catalog)}\n\nDraft answers:\n" +
            "\n".join(f"{d['qid']} | question: {d['question']} | draft answer: {d['draft_answer']}"
                      f" | note: {d['note']}" for d in drafts))
    return llm.call_json("drafts", VERSION, DRAFT_SYSTEM, user, schema, offline=offline)["items"]


def run_all(sources: list[Source], deal_names: dict, catalog: dict, offline=False,
            workers: int = 8, context_year: int | None = None) -> dict[str, dict]:
    """Extract every source in parallel. Returns source id -> model output (or an error)."""
    def one(src):
        try:
            return src.id, extract_source(src, deal_names, catalog, offline=offline,
                                          context_year=context_year)
        except llm.LLMError as e:
            return src.id, {"error": str(e)}
    todo = [s for s in sources if s.system != "directory"]
    with ThreadPoolExecutor(workers) as pool:
        return dict(pool.map(one, todo))
