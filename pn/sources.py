"""Load every input as a Source. Mock connectors: each folder maps to the system it would come from.

A file that appears unchanged in more than one deal's folder (the SOC 2 status, the insurance
certificate) is about the company, not one customer. It becomes one shared source, so fixing it
once fixes it for every deal.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date
from pathlib import Path

import yaml

from .models import Ask, Source

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "state" / "inbox"


def load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text())


def text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()[:10]


def _date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def load_sources(deals_cfg: dict) -> tuple[list[Source], list[Source]]:
    """Return (documents, questionnaires). Questionnaires are parsed by code, not the model."""
    connectors = deals_cfg["connectors"]
    seen: dict[str, Source] = {}
    deal_of: dict[str, set[str]] = {}
    questionnaires: list[Source] = []

    for deal, spec in deals_cfg["deals"].items():
        folder = ROOT / spec["folder"]
        rounds = {r["file"]: r for r in spec.get("questionnaire_rounds", [])}
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            rel = str(path.relative_to(folder))
            text = path.read_text(errors="replace")
            if rel in rounds:
                r = rounds[rel]
                questionnaires.append(Source(
                    id=f"{deal}-q{r['round']}-{text_hash(text)}", deal=deal, system="questionnaire",
                    title=f"{deal}/{rel}", text=text, round=r["round"], date=_date(r.get("date"))))
                continue
            top = rel.split("/")[0].replace(".txt", "")
            system = connectors.get(top, "drive")
            h = text_hash(text)
            deal_of.setdefault(h, set()).add(deal)
            if h not in seen:
                seen[h] = Source(id=h, deal=deal, system=system, title=f"{deal}/{rel}", text=text)

    # Same file in several deals -> it is about the company.
    for h, deals in deal_of.items():
        if len(deals) > 1:
            src = seen[h]
            src.deal = None
            src.title = "company/" + src.title.split("/", 1)[1]

    company = ROOT / deals_cfg.get("company_folder", "data/company")
    if company.exists():
        for path in sorted(company.rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                text = path.read_text()
                meta, body = _front_matter(text)
                seen.setdefault(text_hash(body), Source(
                    id=text_hash(body), deal=None, system=meta.get("system", "notes"),
                    title=f"company/{path.name}", text=body, author=meta.get("author"),
                    date=_date(meta.get("date"))))

    docs = list(seen.values())
    for item in load_inbox():
        if item.system == "questionnaire":
            questionnaires.append(item)
        else:
            docs.append(item)
    return docs, questionnaires


def _front_matter(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end > 0:
            return yaml.safe_load(text[4:end]) or {}, text[end + 4:].lstrip("\n")
    return {}, text


def load_inbox() -> list[Source]:
    """Inputs added through the UI: one JSON file each, kept in arrival order."""
    out = []
    if not INBOX.exists():
        return out
    for path in sorted(INBOX.glob("*.json")):
        d = json.loads(path.read_text())
        deal = d.get("deal") or None
        if deal == "company":
            deal = None
        out.append(Source(
            id=f"in-{path.stem}", deal=deal, system=d.get("system", "email"),
            title=d.get("title") or path.stem, text=d["text"], author=d.get("author"),
            date=_date(d.get("date")), round=d.get("round"), added=True))
    return out


# ---------- questionnaires: parsed by code ----------

_TEMPLATE_DIGITS = re.compile(r"\d+")


def parse_questionnaire(src: Source) -> tuple[list[Ask], list[dict]]:
    """Rows become asks. Rows whose text is the same boilerplate with only a number changed
    ("Control 24: provide the customer-specific answer ...") carry no real question: flag them.

    Returns (asks, draft_answers) where draft_answers are answers already typed into the sheet;
    the engine attaches them as hints to the facts of the requirement holding that row.
    """
    # short rows give None for missing cells; rows with no question are not asks
    rows = [r for r in csv.DictReader(io.StringIO(src.text)) if (r.get("question") or "").strip()]
    shape = lambda q: _TEMPLATE_DIGITS.sub("#", q.strip().lower())
    counts: dict[str, int] = {}
    for r in rows:
        counts[shape(r["question"])] = counts.get(shape(r["question"]), 0) + 1
    asks, drafts = [], []
    for r in rows:
        q = (r.get("question") or "").strip()
        qid = (r.get("id") or "").strip()
        template = counts[shape(q)] >= 3
        ask_id = f"{src.deal}:R{src.round}:{qid}"
        asks.append(Ask(
            id=ask_id, deal=src.deal, origin="questionnaire", text=q, quote=q, source_id=src.id,
            track=(r.get("section") or "").strip() or None, round=src.round, qid=qid,
            template=template))
        if (r.get("draft_answer") or "").strip():
            drafts.append({"ask_id": ask_id, "qid": qid, "draft_answer": r["draft_answer"].strip(),
                           "note": (r.get("note") or "").strip()})
    return asks, drafts
