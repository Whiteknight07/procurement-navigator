"""The objects the engine builds. Plain dataclasses; everything is rebuilt from inputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Source:
    id: str                    # short content hash, stable across rebuilds
    deal: str | None           # None = about the whole company (shared by every deal)
    system: str                # email, slack, drive, vanta, questionnaire, notes, directory
    title: str                 # file path or input title
    text: str
    author: str | None = None  # from the input's metadata, when given
    date: date | None = None   # from the input's metadata, when given
    round: int | None = None   # questionnaire round
    added: bool = False        # came in through the add-input box


@dataclass
class Claim:
    id: str                    # e.g. C-3f2a-1
    source_id: str
    deal: str | None           # the deal whose folder it came from (None = company source)
    fact: str                  # catalog key
    value: str                 # short normalized answer, e.g. "14 days"
    quote: str                 # exact text from the source
    speaker: str | None
    applies_to: str            # this_deal | company | generic_platform | other_customer
    assertion: str             # evidence | stated | planned | undecided | questionnaire_draft
    as_of_date: date | None    # what date the fact refers to (report date, audit period end)
    valid_until: date | None   # expiry date stated in the source
    note: str = ""
    tier: int = 9              # filled by the resolver
    tier_reason: str = ""


@dataclass
class Ask:
    id: str                    # e.g. acme:R1:Q-004 or acme:email:3
    deal: str
    origin: str                # questionnaire | customer | predicted
    text: str
    quote: str
    source_id: str
    who: str | None = None     # who asked (customer person or team)
    track: str | None = None
    round: int | None = None
    qid: str | None = None     # questionnaire question number, as printed
    template: bool = False     # no real question text (e.g. "Control 24: provide ...")
    kind: str = "requirement"  # requirement | instruction | constraint | conditional
    date_text: str | None = None


@dataclass
class Fact:
    key: str                   # e.g. "acme.retention_app_logs" or "company.soc2"
    name: str                  # catalog key
    deal: str | None
    spec: dict
    status: str = "unknown"    # verified | needs_confirmation | needs_proof | conflicting |
                               # undecided | stale | expired | unknown
    value: str | None = None
    winner: Claim | None = None
    claims: list[Claim] = field(default_factory=list)   # all usable claims, best first
    hints: list[Claim] = field(default_factory=list)    # other customers: never the answer
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)  # fact keys not yet settled


@dataclass
class Requirement:
    id: str                    # e.g. acme:R-04
    deal: str
    title: str
    track: str
    ask: str                   # clean form of the ask
    ask_ids: list[str]
    facts: list[str]           # catalog keys
    reading: str = ""          # how this customer reads it
    status: str = "open"       # ready | open | blocked | missing_text | predicted
    gaps: list[str] = field(default_factory=list)   # fact keys not yet verified
    history: list[dict] = field(default_factory=list)  # per round: relation, added part


@dataclass
class Action:
    kind: str                  # ask_customer | confirm | get_evidence | renew | decide |
                               # approve | send_update | request_text | resolve_conflict
    who: str                   # person who must act
    title: str
    detail: str
    facts: list[str] = field(default_factory=list)
    deals: list[str] = field(default_factory=list)
    unblocks: int = 0          # requirements that depend on it, across deals
    reply_days: float = 2
    proposed: list[dict] = field(default_factory=list)  # proposed answers to confirm
    waiting_since: str | None = None
    overdue: bool = False
