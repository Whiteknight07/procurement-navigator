# Demo script (10 minutes)

**Before you start:** `.venv/bin/streamlit run app.py`. In the sidebar, click **Clear added inputs
and the ledger** so the inbox and ledger are empty. Keep the as-of date at **Nov 3, 2026**. Every
step below runs from the committed cache, so the demo works offline.

## 0. The problem in one line (0:00–0:30)

> "Jai has two bank- and telco-sized vendor reviews running at once. The answers are spread across
> Slack, email, old deals and Vanta, and some of them are wrong, stale or copied from another
> customer. Every Monday he has to rebuild in his head what's required, what's known, what's
> missing, who is blocking, and what to do next. This builds that picture for him and keeps it
> current."

## 1. Monday briefing (0:30–2:30)

Open **Monday briefing**. Both deals are on one screen.

- **Root blockers.** "Acme residency requirement" is at the top. The chain under it reads: *cross
  border transfers → inference location → model providers → fallback routing → residency
  requirement*. Only Acme can answer this, so the action is to ask Elena. Engineering is not asked to
  guess. Point out that the DPA is stuck behind subprocessors, which comes back to the same root.
- **What's delaying.** Both are *out of date*: insurance **expired** Oct 1, 2026, and the pen test is **stale**: Feb 2026, 275
  days old against a 183-day policy. That 183 days comes from Jai saying a 6-month-old report won't
  work.
- **Pre-filled questionnaire answers not to send.** Q-004 says "30 days?". Our best source for Acme
  app logs says 14 days, and "30 days" matches the generic April platform doc (for Northstar it
  matches BluePeak, another customer). Q-011 says "24h / 24h?". Nothing of ours supports it, and it
  matches a prior customer's answer. These drafts are attached in code to the facts of the
  requirement holding that row; no model call decides where they belong.
- **37 blank controls.** Q-024–Q-060 are the same boilerplate with only the number changed. They are
  held as *no question text*. We never invent answers for them. The action is to ask Acme
  procurement for the real text.
- **What moves things forward.** There is one batched message per person, ranked by how many
  requirements it unblocks, and each item includes our current best answer for them to confirm.

## 2. Deal: the five questions (2:30–3:30)

Open **Deal → Acme**. The first five tabs are the brief's five questions. Requirement ids are
stable labels from their first ask (Q-004, E-1a2b for an email ask, P-… for a predicted topic).

1. **What's required.** Asks from questionnaire rows, Marcus's and Elena's emails and predicted
   topics, merged into one list. Open *Retention by data type*: it merges Q-004 and Elena's
   sentence. Marcus's "answer for the configuration Acme would actually receive" is kept as an
   **instruction**, not a requirement, and it shapes how each answer is read.
2. **What we know.** Verified facts and the source that settles each one.
3. **What's unresolved.** Worst first, with the reason and the owner.
4. **What's blocking.** Root causes and their chains.
5. **What to do next.** Ranked actions.

## 3. Fact detail: why a value won (3:30–4:15)

Open **Deal → Acme → Fact detail → Acme retention app logs** (or storage location).

- The value is "14 days". The best source is the deal document, a plan rather than proof it is
  live, so the status is *unconfirmed* and Anthony is the owner.
- Every source is listed with its trust tier (four tiers: evidence, owner confirmed, plan or chat,
  hint). **The generic April platform doc and the "30 days?" questionnaire draft show as hints and
  never count.** On Northstar, BluePeak's 30-day answer is a hint the same way.
- "Trust tiers, recency and expiry are fixed rules in `config/policy.yaml`. The model only extracted
  the quotes."

## 4. Approvals: the 10-second check (4:15–5:15)

Open **Approvals → Acme → Q-007** (SOC 2), or click **Review answer** on the briefing. Click
**Draft and check an answer**.

- Each sentence cites a claim. A sentence with no source becomes *Pending: owner X* and is never
  presented as fact.
- The checker is code: it flags uncited sentences, sentences that state an unverified fact, and
  citations of hints (another customer, a generic doc, a questionnaire draft). Any error turns
  approval off. The cited quote sits under each sentence, so Jai checks support by reading.
- The draft says the 2025 report is final and the 2026 audit is not yet final. The caveat comes from
  the evidence itself.
- Click **Approve and mark as sent to Acme National Bank**. The ledger now holds this exact version,
  plus a snapshot of the facts it relied on. "This is the central record of what we sent, which Jai
  told us doesn't exist today."

## 5. New information arrives (5:15–8:30)

Open **Add input**. Add the demo inputs **in order** with **Add and rebuild**, and read each **What
changed** banner aloud.

1. **Acme questionnaire, round 2.** Open **Deal → Acme → Rounds**. Q-009 is a **repeat**. Q-004 is
   a **repeat with changes** (deletion requests). So are Q-007 and Q-021 (2026 report, expired
   certificate). **Q-012 is a reused number**: round 1 Q-012 was model providers, and round 2
   Q-012 is AI monitoring, so it is matched by meaning and flagged. Q-062 (business continuity)
   was **predicted** from the guidance doc and is now actually asked.
2. **Elena's email on processing location.** Residency becomes **verified** from the customer's
   own statement. The root blocker clears, and the chain moves down to fallback routing and
   providers.
3. **Anthony's Slack on the Acme config.** Anthony owns these facts, so app logs at 14 days,
   storage in ca-central-1 and Anthropic fallback disabled are now **verified**. Several
   requirements move toward ready.
4. **Anthony's Slack saying "2026 SOC 2 is final".** SOC 2 goes **conflicting**: Vanta says 2025,
   and a chat message is not evidence. The answer we just sent is marked **at risk**, but no update
   is owed yet because the new value isn't proven. The action is to get the report into Vanta.
5. **The SOC 2 2026 report in Vanta.** SOC 2 is **verified** as the 2026 report, and there is an
   **update owed to Acme** for the answer sent in section 4. "This is Jai's 'tell them
   straight away' rule."
6. **Renewed insurance certificate.** Insurance becomes **verified** for **both** deals, because it
   is a company fact and settling it once settles it everywhere.

## 6. Northstar, and a new customer (8:30–9:15)

- Switch **Deal → Northstar**. It runs on the same code and catalog with different asks: tool
  authorization, employee-data scope, Records Management "may be triggered" (kept as undecided and
  customer-owned), a **Nov 16 launch** and **AI Governance meeting on Wednesdays** on the timeline.
  BluePeak's answers are hints there, just as Meridian's are for Acme.
- Onboarding a new customer means adding a folder and an entry in `config/deals.yaml`. Company facts
  (SOC 2, insurance, pen test) carry over automatically. Deal facts start unknown. If they ask
  about something new, add a fact to `catalog.yaml`. If they have stricter rules, it's a
  `policy.yaml` edit. No code changes.

## 7. Close (9:15–10:00)

"The model reads and drafts. Rules decide. Jai approves. Run `python -m evals.run` and it passes all 36
hand-written checks."

---

## Likely questions, short honest answers

**Why rules instead of letting the LLM decide?** Which source wins, whether evidence is stale, and
whether a customer is owed an update are policy decisions. Jai has to be able to read why, change
the setting, and get the same answer tomorrow. The model is good at reading messy text and bad at
being a consistent, auditable judge. So it gives us quotes and labels, and code checks the quotes
are real and that ids exist.

**How do you know it works?** `evals/run.py` builds the state and checks it against an answer key I
wrote by hand from the case, not from the output. It covers 36 checks across traps, evidence age,
root causes, requirements discovery, round-2 matching per question, and the six-input story. All
36 pass, including the one that used to fail: Vanta shows the pen-test findings open while Anthony
says they were fixed, so the remediation fact is *conflicting* (get updated evidence), not verified.
There are also unit tests for the rules.

**Cost and latency?** All model calls for this case, including iterations, cost about $0.50 on
OpenRouter. A cached rebuild is effectively instant. A new email costs one extraction plus a
re-merge of requirements, which takes tens of seconds; the merge is the slow call. Unchanged sources
are never sent again.

**What breaks first at scale?** The full rebuild and the single merge prompt per deal: with hundreds
of questions, the merge gets long and less stable. Next is the hand-maintained catalog, and then
value comparison, which is token match plus a label with no notion of units. The fixes are
incremental resolution, chunked merging, and catalog suggestions from unmapped asks.

**What would you build next?** Real connectors: Vanta first because it carries the most trust, then
Slack and email. Answer reuse across customers when facts match. Auth and a signed ledger. A
"suggest a new catalog fact" flow for unmapped asks.
