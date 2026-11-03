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
    inferred_date: date | None = None   # no stated date: the latest date its claims mention


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
    speaker_side: str = "unknown"   # customer | supplier | unknown
    tier: int = 4              # filled by the resolver; 4 = hint
    tier_reason: str = ""
    eff_date: date | None = None    # date used for recency: as_of_date, else the source's date


@dataclass
class Ask:
    id: str                    # e.g. acme:R1:Q-004 or acme:3f2a1b:2
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
    kind: str = "requirement"  # requirement | instruction | constraint | conditional | predicted
    date_text: str | None = None


@dataclass
class Fact:
    key: str                   # e.g. "acme.retention_app_logs" or "company.soc2"
    name: str                  # catalog key
    deal: str | None
    spec: dict
    status: str = "unknown"    # verified | unconfirmed | conflicting | undecided | out_of_date | unknown
    value: str | None = None
    winner: Claim | None = None
    claims: list[Claim] = field(default_factory=list)   # usable claims (tiers 1-3), best first
    hints: list[Claim] = field(default_factory=list)    # tier 4: never the answer
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)  # fact keys not yet settled


@dataclass
class Requirement:
    id: str                    # stable, from the first ask: acme:Q-004, acme:E-1a2b, acme:no-text
    deal: str
    title: str
    track: str
    ask: str                   # clean form of the ask
    ask_ids: list[str]
    facts: list[str]           # catalog keys
    reading: str = ""          # how this customer reads it
    status: str = "open"       # ready | open | blocked | unmapped | missing_text | predicted | umbrella
    gaps: list[str] = field(default_factory=list)   # fact keys not yet verified
    history: list[dict] = field(default_factory=list)  # later rounds: relation, added part


@dataclass
class Action:
    kind: str                  # ask_customer | confirm | get_evidence | renew | decide | settle |
                               # approve | send_update | request_text
    who: str                   # person who must act
    title: str
    detail: str
    facts: list[str] = field(default_factory=list)
    deals: list[str] = field(default_factory=list)
    unblocks: int = 0          # requirements that depend on it, across deals
    reply_days: float = 2
    proposed: list[dict] = field(default_factory=list)  # proposed answers to confirm
    req: str | None = None     # requirement id, for approve and send_update
    waiting_since: str | None = None
    overdue: bool = False
