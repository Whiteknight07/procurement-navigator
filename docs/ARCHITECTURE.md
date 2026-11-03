# Architecture

Procurement Navigator keeps an always-current picture of every vendor-review deal and turns it into
next actions. It answers the brief's five questions for each deal: what is required, what is known
(with proof), what is unresolved, what is blocking, and what to do next.

The whole state is **rebuilt from inputs every time**. Inputs are never edited. LLM results are
cached by content hash, so a rebuild is fast and gives the same answer twice.

Colour key in the diagrams: **LLM** steps are hexagons, **fixed rules** are rectangles, people are actors.

## 1. The big picture

```mermaid
flowchart TB
  subgraph IN["Inputs (connectors mocked)"]
    A1["Case folder<br/>emails · Slack · docs"]
    A2["Evidence store<br/>(mock Vanta)"]
    A3["Questionnaire rounds<br/>(CSV)"]
    A4["Add-input box<br/>paste / upload"]
  end
  subgraph CFG["Settings (data, not code)"]
    C1["Fact catalog<br/>owner · dependencies · expiry"]
    C2["Trust tiers · as-of date"]
    C3["People<br/>team · reply speed"]
  end
  subgraph ENG["Engine (rebuilt on every change)"]
    B1["Intake<br/>label system, date, deal;<br/>dedupe by hash"]
    B2{{"Extract (LLM)<br/>claims + asks,<br/>each with an exact quote"}}
    B3["Fact resolver (rules)<br/>trust · scope · recency · expiry"]
    B4{{"Requirement merger (LLM)<br/>dedupe asks across rounds"}}
    B5["Planner (rules)<br/>status · blocker chains ·<br/>ranked actions · updates owed"]
    B6{{"Drafter (LLM)<br/>answers from facts only"}}
    B7["Checker (rules + LLM)<br/>every sentence cited"]
  end
  subgraph OUT["Screens"]
    D1["Monday briefing<br/>all deals"]
    D2["Deal page<br/>the 5 questions"]
    D3["Approval card<br/>citations + flags"]
  end
  L[("Ledger<br/>approvals · sends · asks")]

  A1 --> B1
  A2 --> B1
  A3 --> B1
  A4 --> B1
  B1 --> B2
  B2 -- claims --> B3
  B2 -- asks --> B4
  C1 --> B3
  C2 --> B3
  B3 --> B5
  B4 --> B5
  C3 --> B5
  B5 --> D1
  B5 --> D2
  B5 --> B6 --> B7 --> D3
  D3 -- "Jai approves / sends" --> L
  L -- "what we told each customer" --> B5
```

## 2. Data model

```mermaid
erDiagram
  DEAL ||--o{ SOURCE : "has (company sources are shared)"
  SOURCE ||--o{ CLAIM : yields
  SOURCE ||--o{ ASK : yields
  FACT ||--o{ CLAIM : "settled from"
  REQUIREMENT ||--o{ ASK : merges
  REQUIREMENT }o--o{ FACT : "depends on"
  FACT }o--o{ FACT : "depends on"
  REQUIREMENT ||--o{ ANSWER : drafts
  ANSWER ||--o| LEDGER_ENTRY : "approved / sent as"
  FACT ||--o{ ACTION : "a gap creates"
```

| Object | What it is | Example |
|---|---|---|
| Source | One input, never edited | `03_internal/internal_messages.txt` (Slack) |
| Claim | One statement pulled from a source, with its exact quote | "app logs 14 days", quote "App logs intended 14 days.", kind *planned* |
| Fact | The current answer to one thing, decided by rules | `acme · retention_app_logs = 14 days`, status *needs confirmation* |
| Ask | One thing a customer asked, in one place | Round 1 Q-004, or one sentence of Elena's email |
| Requirement | One customer ask merged across rounds and channels | "Retention by data type", from Q-004 + Elena's email |
| Answer | A drafted answer: sentences, each citing claims or marked *pending* | |
| Ledger entry | A human action: approved, sent (with a fact snapshot), asked, decided | |
| Action | Worked out by the planner, never typed in | "Ask Anthony to confirm 4 config facts" |

## 3. Sequence: first load (Monday morning)

```mermaid
sequenceDiagram
  autonumber
  actor Jai
  participant UI as UI
  participant E as Engine
  participant C as Cache
  participant X as Extractor (LLM)
  participant R as Resolver (rules)
  participant M as Merger (LLM)
  participant P as Planner (rules)
  Jai->>UI: Open Monday briefing
  UI->>E: build(as_of = 2026-11-03)
  E->>E: Load sources, dedupe by hash
  loop each source
    E->>C: cached extraction?
    alt not cached
      E->>X: source text + fact catalog
      X-->>E: claims + asks, each with an exact quote
      E->>E: drop any quote not found in the source
      E->>C: save
    end
  end
  E->>R: claims + trust tiers + expiry rules
  R-->>E: facts, each with status + reason
  E->>M: asks from emails + questionnaire rounds
  M-->>E: requirements, each linked to facts
  E->>P: facts + requirements + ledger + people
  P-->>E: blocker chains, waits, ranked actions
  E-->>UI: state
  UI-->>Jai: To do · Blockers · Waiting on · Delays · Next moves
```

## 4. Sequence: a new questionnaire round arrives

```mermaid
sequenceDiagram
  autonumber
  actor Jai
  participant UI as UI
  participant E as Engine
  participant M as Merger (LLM)
  participant P as Planner (rules)
  Jai->>UI: Add Round 2 questionnaire
  UI->>E: new source (questionnaire, round 2)
  E->>M: each new question + existing requirements
  M-->>E: repeat / repeat + addition / changed / new, with the added part
  E->>E: reject requirement ids the model was not shown
  E->>E: same question number, different question? flag it
  alt repeat
    E->>P: reuse the answer already sent, if its facts are unchanged
  else repeat + addition
    E->>P: only the added part needs new facts
  else new
    E->>P: new requirement, linked to facts and owners
  end
  P-->>UI: round diff + updated actions
  UI-->>Jai: "3 repeats, 2 additions, 2 new, 1 reused number"
```

## 5. Sequence: a fact changes after we answered

```mermaid
sequenceDiagram
  autonumber
  actor Anthony
  actor Jai
  participant UI as UI
  participant E as Engine
  participant X as Extractor (LLM)
  participant R as Resolver (rules)
  participant L as Ledger
  participant P as Planner (rules)
  Note over Jai,L: Earlier, Jai sent Acme "SOC 2: 2025 final, 2026 in progress"
  Anthony->>UI: Slack "2026 SOC 2 is final" (pasted in)
  UI->>E: new source (Slack, Anthony)
  E->>X: extract
  X-->>E: claim: soc2 = 2026 final (stated in chat, no document)
  E->>R: re-resolve soc2
  R-->>E: needs proof (a chat message is not evidence)
  E->>P: plan
  P-->>UI: Action: put the final SOC 2 report in Vanta (Anthony)
  Anthony->>UI: Upload the final report (evidence)
  E->>R: re-resolve soc2
  R-->>E: verified: 2026 report final
  E->>L: which sent answers used soc2?
  L-->>E: Acme R-07, sent with "2026 in progress"
  E->>P: value changed since it was sent
  P-->>UI: Update owed to Acme
  UI-->>Jai: Draft update ready to approve
```

## 6. Sequence: the 10-second approval

```mermaid
sequenceDiagram
  autonumber
  actor Jai
  participant UI as UI
  participant D as Drafter (LLM)
  participant K as Checker (rules + LLM)
  participant L as Ledger
  Jai->>UI: Open a requirement
  UI->>D: requirement + customer's reading + facts (with claim ids)
  D-->>UI: sentences, each citing claim ids, or "Pending: owner X"
  UI->>K: check the draft
  K-->>UI: flags: uncited sentence · stale proof · other-customer source · plan not confirmed live
  Jai->>UI: Approve this exact version
  Jai->>UI: Mark as sent
  UI->>L: record version + snapshot of every fact it used
```

## What is automated, what is fixed, what is human

| LLM (GPT-6.1 Terra, medium reasoning) | Fixed rules | Humans |
|---|---|---|
| Pull claims and asks from messy text, with exact quotes | Trust tier of every claim | Jai approves every answer that leaves |
| Map each claim to a fact in the catalog | Which claim wins, and why | Owners confirm facts |
| Merge asks into requirements; match new rounds | Conflicts, staleness, expiry | Decision owners decide |
| Draft answers from facts only | Requirement status, blocker chains, action ranking | Jai confirms uncertain matches |
| Check that each citation supports its sentence | Updates owed, what is allowed to be sent | |

The LLM never settles a conflict and never fills a gap. A gap becomes *Pending: owner X*.
