# Eval results

Run 2026-11-03 15:34 · as-of 2026-11-03 · model openai/gpt-6.1-terra (medium)

**Score: 36 / 36 checks pass**

Builds:

- base case: offline (cache), 0.0s
- base + round 2: offline (cache), 0.0s
- story + 01_acme_round2.json: offline (cache), 0.0s
- story + 02_elena_residency.json: offline (cache), 0.0s
- story + 03_anthony_config.json: offline (cache), 0.0s
- story + 04_anthony_soc2.json: offline (cache), 0.0s
- story + 05_soc2_report.json: offline (cache), 0.0s
- story + 06_insurance_renewal.json: offline (cache), 0.0s

## Traps

| | Check | Why |
|---|---|---|
| PASS | Other-customer claims (Meridian, BluePeak) never win a fact | 7 other-customer claims seen as hints; winning anyway: none |
| PASS | Questionnaire draft answers never win a fact | 12 draft claims read; winning anyway: none |
| PASS | "30 days?" draft flagged on acme.retention_app_logs as disagreeing with 14 days | [acme] questionnaire draft "30 days?" disagrees with the best source ("14 days"); it matches acme/04_knowledge/general_or_older_reference.md (a generic product doc (a hint, not this customer)). |
| PASS | "24h / 24h?" RTO/RPO draft flagged (nothing behind it / another customer's) | [acme] questionnaire draft "24h / 24h?" has nothing behind it; it matches northstar/04_knowledge/prior_customer_answers_CONTEXT_ONLY.md (another customer's answer (a hint, never the answer)). · fact status unknown |
| PASS | Acme National Bank: 37 template controls detected, never answered | 37 detected; held as 'Controls with no question text'; in other requirements: none; request-text action: True |
| PASS | Northstar Telecom: 37 template controls detected, never answered | 37 detected; held as 'Controls with no question text'; in other requirements: none; request-text action: True |
| PASS | A generic product doc never makes a fact verified | 10 facts have a generic-doc claim, kept as a hint |

## Evidence and time

| | Check | Why |
|---|---|---|
| PASS | company.insurance expired as of 2026-11-03 | out_of_date: Expired on Oct 01, 2026. |
| PASS | company.pen_test stale (Feb 2026, older than 183 days) | out_of_date: Stale: dated Feb 2026, 275 days old; policy allows 183. |
| PASS | company.pen_test_remediation not verified (owner chat says fixed; evidence file shows them open) | conflicting, value "No remediation evidence in file" from company/05_evidence/pen_test_summary.txt; The owner says "Remediated; evidence needed"; the evidence on file says "No remediation evidence in file". Get updated evidence. |
| PASS | company.soc2 verified, with the 2026-audit caveat | verified: "SOC 2 Type II, Jan-Dec 2025, final"; caveat: Also on file: 2026 audit not yet final |

## Root causes

| | Check | Why |
|---|---|---|
| PASS | acme.residency_requirement (customer-only) is a root blocker | holds up 5 requirements, e.g. Acme cross border transfers → Acme inference location → Acme model providers → Acme fallback routing → Acme residency requirement |
| PASS | acme.fallback_routing undecided | undecided: Not decided yet: "for Acme probably disable if residency/legal requires it. Not finalized." (Anthony). |
| PASS | acme.dpa_status blocked chain reaches acme.subprocessors | blocked by ['acme.cross_border_transfers', 'acme.fallback_routing', 'acme.inference_location', 'acme.model_providers', 'acme.residency_requirement', 'acme.subprocessors']; root causes ['acme.residency_requirement'] |
| PASS | A customer-ask action exists for Acme residency | 'Ask the customer about: Acme residency requirement' → Elena, unblocks 6 |

## Requirements discovery

| | Check | Why |
|---|---|---|
| PASS | Elena's ask 'personal-information scope' lands in a requirement | "Clarify the personal-information scope." → acme:Q-002 Personal information scope ['personal_info_categories'] |
| PASS | Elena's ask 'content leaving Canada during processing' lands in a requirement | "Clarify whether content leaves Canada during processing." → acme:Q-006 Cross-border content transfers ['cross_border_transfers', 'inference_location'] |
| PASS | Elena's ask 'retention by data type' lands in a requirement | "Clarify retention by data type." → acme:Q-004 Retention by data type ['retention_source_files', 'retention_prompts_responses', 'retention_app_logs', 'retention_traces'] |
| PASS | Elena's ask 'subprocessors with content access' lands in a requirement | "Identify subprocessors with content access." → acme:Q-005 Content-accessing subprocessors ['subprocessors'] |
| PASS | Marcus's instructions recognised as instructions, not requirements | "Answer for the configuration Acme would receive."; "Identify the owner of unknown information rather than guessing." |
| PASS | Northstar timeline has the Nov 16 launch and the Wednesday cadence | ['Target launch is November 16, 2026.', 'Security and Privacy reviews can run in parallel.', 'AI Governance meets Wednesdays.'] |
| PASS | Records Management captured for Northstar | northstar:E-fc5e Records review applicability (open) |

## Round 2 matching

| | Check | Why |
|---|---|---|
| PASS | Q-004: Retention restated, plus a new deletion-request part. | repeat_with_changes → acme:Q-004 Retention by data type |
| PASS | Q-007: Same SOC 2 ask, now asks for the 2026 report if final. | repeat_with_changes → acme:Q-007 Current assurance report |
| PASS | Q-009: Word-for-word the same as round 1 Q-009. | repeat → acme:Q-009 Encryption and access controls |
| PASS | Q-012: Number reused: round 1 Q-012 was model providers, round 2 Q-012 is AI monitoring. New, or matched to the predicted monitoring topic, are both fine. | repeat_with_changes → acme:P-14a2 AI monitoring and incidents · reused number flagged |
| PASS | Q-021: Insurance again, now noting the expiry and asking for the new certificate. | repeat_with_changes → acme:Q-021 Insurance evidence and expiry |
| PASS | Q-061: Personal-information scope, conditional on real transcripts, plus minimisation. It can attach to the PI-scope requirement or to Nina's 'what changes' requirement. | repeat_with_changes → acme:Q-002 Personal information scope |
| PASS | Q-062: Business continuity was predicted from the guidance doc; now it is actually asked. | repeat_with_changes → acme:P-ced7 Business continuity and recovery |

## Story

| | Check | Why |
|---|---|---|
| PASS | After Elena's email: Acme residency verified | verified: "Customer content must not be processed outside Canada, including model inference and fallback providers" (Settled by the customer's own statement.) |
| PASS | After Anthony's config: Acme app logs 14 days verified, fallback no longer undecided | app logs verified "14 days"; fallback verified "Anthropic fallback disabled" |
| PASS | After Anthony's SOC 2 chat: soc2 conflicting (chat is not evidence) | conflicting: The owner says "2026 SOC 2 Type II report final"; the evidence on file says "SOC 2 Type II, Jan-Dec 2025, final". Get updated evidence. |
| PASS | After Anthony's SOC 2 chat: sent SOC 2 answer at risk, no update owed yet | at_risk on acme:Q-007 |
| PASS | After the Vanta report: soc2 verified as the 2026 report | verified: "SOC 2 Type II, Jan 1–Sep 30 2026, final, no exceptions" |
| PASS | After the Vanta report: update owed to Acme for the sent SOC 2 answer | update_owed on acme:Q-007; send_update action: True |
| PASS | After the insurance renewal: insurance verified | verified: "Cyber / Technology E&O, CAD 5,000,000; current, Oct 1, 2026 to Oct 1, 2027" (Settled by the evidence store (mock Vanta).) |
